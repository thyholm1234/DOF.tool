from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
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
from portal.services import konto, passkeys, zitadel
from portal.services import account, activity, learn_access, article_access, auth, dku, su, dofbasen, features, netfugl, kl_access, photo_access, photo_privileges, position_privacy
from portal.timeutil import dk_today
from portal.services import dataset

router = APIRouter(prefix="/api", tags=["auth"])


class LoginIn(BaseModel):
    login: str = Field("", max_length=254)  # e-mail eller obserkode
    obserkode: str = Field("", max_length=16)  # ældre klienter (før kontiene) – de får besked om at oprette en konto
    password: str = Field(max_length=200)


class VerifyIn(BaseModel):
    obserkode: str = Field(max_length=16)
    password: str = Field(max_length=200)  # DOFbasen-adgangskoden – sendes kun videre til DOFbasen


class CompleteIn(BaseModel):
    token: str = Field(max_length=64)
    password: str = Field(max_length=konto.PW_MAX)
    email: str | None = Field(None, max_length=254)
    navn: str | None = Field(None, max_length=160)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=konto.PW_MAX)


class EmailIn(BaseModel):
    current: str = Field(max_length=200)
    email: str = Field(max_length=254)


# Indstillinger, superadmin ikke ser i «se som bruger»: positionsenheden og ICS-tokenet (giver adgang til kalenderen)
VIEW_AS_HIDDEN_PREFS = {"position_device", "ics_token"}


def user_out(user: User, view_as: bool = False) -> dict:
    prefs = user.prefs or {}
    if view_as:  # brugerens egen sag: hvilken enhed positionen følger (services/position_privacy.py) og kalendertokenet
        prefs = {k: v for k, v in prefs.items() if k not in VIEW_AS_HIDDEN_PREFS}
    return {"id": user.id, "obserkode": user.obserkode, "navn": user.navn, "role": user.role,
            "email": None if view_as else user.email, "account": bool(user.pw_hash),
            "modules": sorted(auth.modules(user)), "prefs": prefs, "history_synced_at": user.history_synced_at}


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    """Login med DOF.tool-kontoen: e-mail eller obserkode + kontoens adgangskode (services/konto.py). 409 = obserkoden
    har endnu ingen konto (brugeren sendes til oprettelsen)."""
    try:
        user = await konto.login(db, body.login or body.obserkode, body.password, client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await start_session(user, response)
    if user.history_synced_at is None:
        await enqueue_history(user.obserkode)
    await activity.track(user.id, "login", None, {"new": False, "method": "password"})
    return {"user": user_out(user), "new": False, "passkeys": passkeys_offer(user)}


@router.post("/auth/konto/bekraeft")
async def account_verify(body: VerifyIn, request: Request, db: AsyncSession = Depends(get_session)):
    """Opret konto / glemt adgangskode, trin 1: obserkoden bekræftes med ét DOFbasen-login. Giver et engangsbevis til trin 2."""
    try:
        return await konto.verify(db, body.obserkode, body.password, client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/auth/konto/opret")
async def account_complete(body: CompleteIn, response: Response, db: AsyncSession = Depends(get_session)):
    """Trin 2: kontoen oprettes (eller får ny adgangskode) og bindes til obserkoden fra trin 1. Tidligere sessioner logges ud."""
    try:
        user, new = await konto.complete(db, body.token, body.password, body.email, body.navn)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await start_session(user, response)
    if new or user.history_synced_at is None:
        await enqueue_history(user.obserkode)
    if new:
        await bump("lists")
    await activity.track(user.id, "account", None, {"new": new})
    return {"user": user_out(user), "new": new, "passkeys": passkeys_offer(user)}


@router.post("/me/password")
async def change_password(body: PasswordIn, request: Request, response: Response, user: User = Depends(require_user),
                          db: AsyncSession = Depends(get_session)):
    """Skift adgangskode. Alle andre enheder logges ud; denne får en ny session."""
    if getattr(request.state, "view_as", None):
        raise HTTPException(403, "Ikke i «se som bruger»")
    try:
        await konto.change_password(db, user, body.current, body.new, client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await start_session(user, response)
    await activity.track(user.id, "password_change", None, {})
    return {"ok": True}


@router.post("/me/email")
async def change_email(body: EmailIn, request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if getattr(request.state, "view_as", None):
        raise HTTPException(403, "Ikke i «se som bruger»")
    try:
        await konto.change_email(db, user, body.current, body.email, client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await activity.track(user.id, "email_change", None, {})
    return {"user": user_out(user)}


async def start_session(user: User, response: Response) -> None:
    """Ny session + login-cookie. Et frisk login giver i et kvarter lov til at tilføje en passkey (services/passkeys.py)."""
    token = await auth.create_session(user)
    await passkeys.mark_fresh(token)
    s = get_settings()
    response.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True,
                        secure=s.cookie_secure or s.public_url.startswith("https://"), samesite="lax", path="/")


def passkeys_offer(user: User) -> bool:
    """Skal appen tilbyde at oprette en passkey efter login? (Passkeys slået til, og brugeren har ikke forbundet en endnu.)"""
    return zitadel.enabled() and not user.zitadel_id


# ---------------------------------------------------------------- passkeys (services/passkeys.py)


class Credential(BaseModel):
    """WebAuthn-svaret fra browseren med base64url-felter (web/js/passkey.js). Kun størrelsen kontrolleres her; indholdet
    kontrolleres af passkeys.check_client_data og af Zitadel."""
    id: str = Field(max_length=1400)
    rawId: str = Field(max_length=1400)
    type: str = Field(pattern="^public-key$")
    response: dict[str, str | None]
    authenticatorAttachment: str | None = Field(None, max_length=40)

    def plain(self) -> dict:
        if len(json.dumps(self.response)) > 16000:
            raise HTTPException(400, "Svaret fra passkey er for stort")
        return {"id": self.id, "rawId": self.rawId, "type": self.type, "response": self.response}


class PasskeyStartIn(BaseModel):
    login: str = Field("", max_length=254)  # e-mail eller obserkode
    obserkode: str = Field("", max_length=16)  # ældre klienter


class PasskeyFinishIn(BaseModel):
    token: str = Field(max_length=64)
    credential: Credential


class PasskeyRegisterIn(BaseModel):
    credential: Credential
    name: str = Field("", max_length=60)


def _auth_error(exc: auth.AuthError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.post("/auth/passkey/start")
async def passkey_login_start(body: PasskeyStartIn, request: Request, db: AsyncSession = Depends(get_session)):
    try:
        return await passkeys.login_start(db, body.login or body.obserkode, client_ip(request))
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/auth/passkey/finish")
async def passkey_login_finish(body: PasskeyFinishIn, response: Response, db: AsyncSession = Depends(get_session)):
    try:
        user = await passkeys.login_finish(db, body.token, body.credential.plain())
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    await start_session(user, response)
    await activity.track(user.id, "login", None, {"new": False, "method": "passkey"})
    return {"user": user_out(user), "new": False}


@router.get("/me/passkeys")
async def my_passkeys(request: Request, user: User = Depends(require_user)):
    if getattr(request.state, "view_as", None):  # brugerens egne enheder vises ikke i «se som bruger»
        return {"enabled": zitadel.enabled(), "passkeys": []}
    try:
        return {"enabled": zitadel.enabled(), "passkeys": await passkeys.list_for(user)}
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/me/passkeys/start")
async def my_passkey_start(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    try:
        return await passkeys.register_start(db, user, request.cookies.get(COOKIE))
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/me/passkeys/finish")
async def my_passkey_finish(body: PasskeyRegisterIn, user: User = Depends(require_user)):
    try:
        await passkeys.register_finish(user, body.credential.plain(), body.name)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    await activity.track(user.id, "passkey_add", None, {"name": body.name[:60]})
    return {"ok": True}


@router.delete("/me/passkeys/{passkey_id}")
async def my_passkey_delete(passkey_id: str = Path(pattern=r"^[0-9]{1,32}$"), user: User = Depends(require_user)):
    try:
        await passkeys.remove(user, passkey_id)
    except auth.AuthError as exc:
        raise _auth_error(exc) from exc
    await activity.track(user.id, "passkey_remove", None, {"id": passkey_id})
    return {"ok": True}


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
    await position_privacy.revoke_for_session(request.cookies.get(COOKIE))  # telefonen, der delte, holder op
    await auth.destroy_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    if user is None:
        return {"user": None}
    va = getattr(request.state, "view_as", None)
    # DOFbasen-modulet: hvilke funktioner brugeren ser, og hvad brugeren må (styrer menuen)
    flags = await features.load(db)
    return {"user": {**user_out(user, view_as=va is not None), "is_test": user.is_test,
                     "view_as": {"until": va["until"], "by": va["real_navn"], "grund": va["grund"]} if va else None, "dofbasen": (await dofbasen.context(db, user)).out(),
                     "netfugl": (await netfugl.context(db, user)).out(), "artikler": (await article_access.context(db, user)).out(),
                     "fotos": (await photo_access.context(db, user)).out(await photo_privileges.level(db, user.id)),
                     "krydslister": (await kl_access.context(db, user)).out(), "dku": await dku.context(db, user), "su": await su.context(db, user),
                     "position": features.visible(flags, "position", superadmin=user.role == "superadmin", insider=False),
                     "voice": features.visible(flags, "voice", superadmin=user.role == "superadmin", insider=False),
                     "trip_weather": features.visible(flags, "trip_weather", superadmin=user.role == "superadmin", insider=False),
                     "search": features.visible(flags, "search", superadmin=user.role == "superadmin", insider=False),
                     "dagsliste": features.visible(flags, "dagsliste", superadmin=user.role == "superadmin", insider=False),
                     "statistik": features.visible(flags, "statistik", superadmin=user.role == "superadmin", insider=False),
                     "daymap": features.visible(flags, "daymap", superadmin=user.role == "superadmin", insider=False),
                     "artsquiz": await learn_access.artsquiz(db, user), "lydquiz": await learn_access.lydquiz(db, user),
                     "profiler": features.visible(flags, "profiler", superadmin=user.role == "superadmin", insider=False),
                     "lokalitetsadmin": features.visible(flags, "lokalitetsadmin", superadmin=user.role == "superadmin", insider=False)}}


ALLOWED_PREFS = {"afdelinger", "kommuner", "lists_public", "profile_public", "push_enabled", "quiet_hours", "theme", "home",
                 "feed_level", "feed_scope", "local_max", "phone", "trip_gps"}
# share_location og position_device styres kun af /api/me/location* (api/position.py), der også udsteder og
# tilbagekalder positionstokenet – de kan ikke sættes her.


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


RESYNC_EVERY = 3600  # sekunder mellem to synkroniseringer for samme bruger


@router.post("/me/sync")
async def resync(user: User = Depends(require_user)):
    """Synkronisér alle egne observationer fra DOFbasen (1900 til i år; højst én gang i timen). Ét kald i gatewayens kø."""
    if not await kv().set(f"resync:{user.id}", "1", nx=True, ex=RESYNC_EVERY):
        wait = max(1, round(await kv().ttl(f"resync:{user.id}") / 60))
        raise HTTPException(429, f"Du kan synkronisere igen om {wait} min.")
    job = await gateway.enqueue("history", {"obserkode": user.obserkode, "year_from": 1900, "year_to": dk_today().year, "full": True},
                                priority=gateway.NORMAL, dedupe=f"history-full:{user.obserkode}")
    await kv().set(f"resync:job:{user.id}", job, ex=86400)
    return {"queued": True, **await gateway.eta([job])}


@router.get("/me/sync")
async def resync_status(user: User = Depends(require_user)):
    """Status for «Synkronisér alle mine observationer»: venter (med forventet ventetid i sekunder), seneste resultat og om knappen kan bruges."""
    job = await kv().get(f"resync:job:{user.id}")
    last = await kv().get(f"resync:result:{user.obserkode}")
    return {**(await gateway.eta([job]) if job else {"pending": False, "eta": None}), "last": json.loads(last) if last else None,
            "available": not await kv().exists(f"resync:{user.id}"), "available_in": max(0, await kv().ttl(f"resync:{user.id}")),
            "history_synced_at": user.history_synced_at}


@router.get("/me/stats")
async def my_stats(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    row = (await dataset.execute(db, """
        SELECT count(*) AS n_obs, count(DISTINCT turid) AS n_trips, min(dato) AS first, max(dato) AS last,
               count(DISTINCT artnr) AS n_taxa
        FROM observations WHERE obserkode = :k
    """, {"k": user.obserkode}, secret_for=user.obserkode)).mappings().first()
    return dict(row)
