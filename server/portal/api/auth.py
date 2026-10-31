from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import COOKIE, client_ip, optional_user, require_user
from portal.cache import bump, kv
from portal.config import get_settings
from portal.db import get_session
from portal.dof import gateway
from portal.dof.archive import MONTH_RE, archive_files
from portal.models import User
from portal.services import account, activity, article_access, auth, dku, dofbasen, features, netfugl
from portal.timeutil import dk_today

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    obserkode: str = Field(max_length=16)
    password: str = Field(max_length=200)


def user_out(user: User) -> dict:
    return {"id": user.id, "obserkode": user.obserkode, "navn": user.navn, "role": user.role,
            "modules": sorted(auth.modules(user)), "prefs": user.prefs or {}, "history_synced_at": user.history_synced_at}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    try:
        user, new = await auth.login(db, body.obserkode, body.password, client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    token = await auth.create_session(user)
    s = get_settings()
    response.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True,
                        secure=s.cookie_secure or s.public_url.startswith("https://"), samesite="lax", path="/")
    if new or user.history_synced_at is None:
        await enqueue_history(user.obserkode)
    if new:
        await bump("lists")
    await activity.track(user.id, "login", None, {"new": new})
    return {"user": user_out(user), "new": new}


async def enqueue_history(obserkode: str) -> None:
    """Hent brugerens ældre observationer, som arkivet ikke dækker (ét kald)."""
    years = sorted(int(m.group(1)) for p in archive_files() if (m := MONTH_RE.match(p.name)))
    # Arkivet er komplet fra første år med alle 12 måneder; før det hentes brugerens egne data
    complete_from = next((y for y in years if years.count(y) >= 12), None)
    year_to = (complete_from - 1) if complete_from else dk_today().year
    await gateway.enqueue("history", {"obserkode": obserkode, "year_from": 1900, "year_to": year_to},
                          priority=gateway.NORMAL, dedupe=f"history:{obserkode}")


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    await auth.destroy_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    if user is None:
        return {"user": None}
    # DOFbasen-modulet: hvilke funktioner brugeren ser, og hvad brugeren må (styrer menuen)
    flags = await features.load(db)
    return {"user": {**user_out(user), "is_test": user.is_test, "dofbasen": (await dofbasen.context(db, user)).out(),
                     "netfugl": (await netfugl.context(db, user)).out(), "artikler": (await article_access.context(db, user)).out(), "dku": await dku.context(db, user),
                     "position": features.visible(flags, "position", superadmin=user.role == "superadmin", insider=False),
                     "voice": features.visible(flags, "voice", superadmin=user.role == "superadmin", insider=False),
                     "trip_weather": features.visible(flags, "trip_weather", superadmin=user.role == "superadmin", insider=False),
                     "search": features.visible(flags, "search", superadmin=user.role == "superadmin", insider=False),
                     "statistik": features.visible(flags, "statistik", superadmin=user.role == "superadmin", insider=False),
                     "daymap": features.visible(flags, "daymap", superadmin=user.role == "superadmin", insider=False),
                     "profiler": features.visible(flags, "profiler", superadmin=user.role == "superadmin", insider=False),
                     "lokalitetsadmin": features.visible(flags, "lokalitetsadmin", superadmin=user.role == "superadmin", insider=False)}}


ALLOWED_PREFS = {"afdelinger", "kommuner", "lists_public", "profile_public", "push_enabled", "quiet_hours", "theme", "home",
                 "feed_level", "feed_scope", "local_max", "phone", "share_location", "trip_gps"}


@router.patch("/me")
async def update_me(prefs: dict[str, Any], user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    unknown = set(prefs) - ALLOWED_PREFS - {"navn"}
    if unknown:
        raise HTTPException(400, f"Ukendte felter: {', '.join(sorted(unknown))}")
    new = dict(user.prefs or {})
    for k, v in prefs.items():
        if k == "navn":
            user.navn = str(v)[:160] if v else user.navn
            continue
        if k in ("afdelinger", "kommuner"):
            v = [str(x) for x in (v or [])][:5]
        if k == "trip_gps" and v not in ("ask", "on", "off"):
            raise HTTPException(400, "trip_gps skal være ask, on eller off")
        if k == "phone":
            v = re.sub(r"[^0-9+ ]", "", str(v or "")).strip()[:20]
            if v and len(re.sub(r"\D", "", v)) < 8:
                raise HTTPException(400, "Telefonnummeret skal have mindst 8 cifre")
        new[k] = v
    if len(json.dumps(new)) > 8000:
        raise HTTPException(400, "For store indstillinger")
    affects_lists = any(k in prefs for k in ("afdelinger", "kommuner", "lists_public", "navn"))
    user.prefs = new
    await db.commit()
    if affects_lists:
        await bump("lists")
    return {"user": user_out(user)}


@router.delete("/me")
async def delete_me(request: Request, response: Response, user: User = Depends(require_user),
                    db: AsyncSession = Depends(get_session)):
    if user.role == "superadmin":
        raise HTTPException(400, "Superadmin kan ikke slettes her")
    await account.delete(db, user)
    response.delete_cookie(COOKIE, path="/")
    await bump("lists")
    return {"ok": True}


@router.get("/me/export")
async def export_me(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Alle dine data i portalen som JSON (art. 15 og 20)."""
    data = await account.export(db, user)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="dof-tool-{user.obserkode}.json"',
                                       "Cache-Control": "no-store"})


@router.post("/me/sync")
async def resync(user: User = Depends(require_user)):
    """Bed om ny hentning af egne ældre observationer (højst én gang pr. døgn)."""
    if not await kv().set(f"resync:{user.id}", "1", nx=True, ex=86400):
        raise HTTPException(429, "Du har allerede bedt om synkronisering i dag")
    await enqueue_history(user.obserkode)
    return {"queued": True, "queue": await kv().zcard(gateway.Q)}


@router.get("/me/stats")
async def my_stats(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(text("""
        SELECT count(*) AS n_obs, count(DISTINCT turid) AS n_trips, min(dato) AS first, max(dato) AS last,
               count(DISTINCT artnr) AS n_taxa
        FROM observations WHERE obserkode = :k
    """), {"k": user.obserkode})).mappings().first()
    return dict(row)
