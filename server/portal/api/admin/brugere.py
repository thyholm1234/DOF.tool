"""Admin → Brugere: brugerliste, testbrugere, moduler, rettigheder, opdatering og sletning."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    perm_brugere,
)
from portal.cache import bump, kv
from portal.db import get_session
from portal.models import (
    User,
)
from portal.services import (
    account,
    activity,
    totp,
)
from portal.services import auth as auth_service
from portal.services.auth import (
    MODULES,
    ROLE_MODULES,
    ROLE_NAMES,
    SUPERADMIN,
    modules,
)

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/admin/users")
async def users(q: str = "", user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT u.id, u.obserkode, u.navn, u.email, (u.pw_hash IS NOT NULL OR u.is_test) AS account, u.role, u.blocked, u.approved,
               u.is_test, u.permissions, u.totp_at IS NOT NULL AS totp, u.created_at, u.last_seen, u.history_synced_at,
               (SELECT count(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS devices,
               (SELECT count(*) FROM alert_rules r WHERE r.user_id = u.id) AS rules
        FROM users u WHERE (:q = '' OR u.obserkode ILIKE :like OR u.navn ILIKE :like OR u.email ILIKE :like)
        ORDER BY u.approved, u.last_seen DESC NULLS LAST, u.created_at DESC LIMIT 300
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    out = []
    for r in rows:
        probe = User(role=r["role"], blocked=False, permissions=r["permissions"])
        out.append({**dict(r), "modules": sorted(modules(probe))})
    return out


# Testbrugere har forudsigelige obserkoder (TEST0001 …) og logger ind med en lokal adgangskode: gætteri begrænses kun af
# loginloftet (30 i timen pr. obserkode), så adgangskoden skal være lang. Den genererede er 12 tegn (token_urlsafe(9)).
TEST_PW_MIN = 12


class TestUserIn(BaseModel):
    navn: str = Field(min_length=1, max_length=160)
    password: str | None = Field(None, min_length=TEST_PW_MIN, max_length=100)


@router.post("/admin/test-users")
async def create_test_user(body: TestUserIn, user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    """Opret en testbruger med lokal adgangskode (til test af appen uden en DOFbasen-konto). Adgangskoden vises kun her."""
    target, password = await auth_service.create_test_user(db, body.navn, body.password)
    await activity.track(user.id, "test_user", None, {"user": target.id})
    return {"id": target.id, "obserkode": target.obserkode, "navn": target.navn, "password": password}


@router.post("/admin/test-users/{user_id}/password")
async def reset_test_password(user_id: int, body: dict[str, Any], user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None or not target.is_test:
        raise HTTPException(404, "Ikke en testbruger")
    password = str(body.get("password") or "") or secrets.token_urlsafe(9)
    if len(password) < TEST_PW_MIN:
        raise HTTPException(400, f"Adgangskoden skal have mindst {TEST_PW_MIN} tegn")
    target.test_pw_hash = auth_service.hash_test_password(password)
    await db.commit()
    async for key in kv().scan_iter("sess:*"):
        if await kv().get(key) == str(target.id):
            await kv().delete(key)
    return {"obserkode": target.obserkode, "password": password}


@router.get("/admin/modules")
async def module_list(user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    """Modulerne til afkrydsningslisten, rollernes standard og hvad den indloggede selv må tildele – samt kontakten for
    godkendelse af nye brugere."""
    return {"modules": MODULES, "roles": {r: sorted(m) for r, m in ROLE_MODULES.items()},
            "role_names": dict(ROLE_NAMES), "grantable": sorted(modules(user)), "require_approval": await auth_service.approval_required(db)}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: int, body: dict[str, Any], user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if target.role == SUPERADMIN:
        raise HTTPException(400, "Superadmin styres fra .env og kan ikke ændres her")
    events: list[tuple[str, dict]] = []  # logges først efter commit, så en afvist ændring ikke står i loggen
    if "role" in body:
        role = body["role"]
        if role == SUPERADMIN:
            raise HTTPException(400, "Superadmin kan ikke tildeles – den styres fra .env")
        if role not in ROLE_MODULES:
            raise HTTPException(400, "Ukendt rolle")
        if (role == "admin" or target.role == "admin") and user.role != SUPERADMIN:
            raise HTTPException(403, "Kun superadmin kan ændre administratorer")
        if target.id == user.id and role != user.role:
            raise HTTPException(400, "Du kan ikke ændre din egen rolle")
        before, old_role = modules(target), target.role
        target.role = role
        if user.role != "superadmin" and (before ^ modules(target)) - modules(user):
            raise HTTPException(403, "Rollen giver eller fjerner moduler, du ikke selv har adgang til")
        if old_role != role:  # rettighedsændringer logges (Admin → Aktivitet), så de kan spores bagefter
            events.append(("role", {"user": target.id, "from": old_role, "to": role}))
    if "approved" in body:
        if target.id == user.id:
            raise HTTPException(400, "Du kan ikke ændre din egen godkendelse")
        if target.role in ("admin", "superadmin") and user.role != "superadmin":
            raise HTTPException(403, "Kun superadmin kan ændre administratorer")
        was = target.approved
        target.approved = bool(body["approved"])
        if not target.approved:  # fjernet godkendelse: log ud overalt
            async for key in kv().scan_iter("sess:*"):
                if await kv().get(key) == str(target.id):
                    await kv().delete(key)
        if was != target.approved:
            await activity.track(user.id, "approval", None, {"user": target.id, "approved": target.approved})
    if "permissions" in body:
        before_perms = target.permissions
        _set_permissions(user, target, body["permissions"])
        if before_perms != target.permissions:
            events.append(("permissions", {"user": target.id, "from": before_perms, "to": target.permissions}))
    if "blocked" in body:
        if target.role == "superadmin":
            raise HTTPException(400)
        if target.role == "admin" and user.role != "superadmin":
            raise HTTPException(403, "Kun superadmin kan spærre administratorer")
        was_blocked = target.blocked
        target.blocked = bool(body["blocked"])
        if was_blocked != target.blocked:
            events.append(("blocked", {"user": target.id, "blocked": target.blocked}))
        if target.blocked:
            async for key in kv().scan_iter("sess:*"):
                if await kv().get(key) == str(target.id):
                    await kv().delete(key)
    await db.commit()
    for kind, detail in events:
        await activity.track(user.id, kind, None, detail)
    await bump("lists")
    return {"ok": True}


def _set_permissions(user: User, target: User, value: Any) -> None:
    """None = rollens standard; ellers en liste af moduler. Man kan kun give og fjerne moduler, man selv har."""
    if target.id == user.id:
        raise HTTPException(400, "Du kan ikke ændre dine egne rettigheder")
    if target.role in ("admin", "superadmin") and user.role != "superadmin":
        raise HTTPException(403, "Kun superadmin kan ændre administratorers rettigheder")
    if target.role == "superadmin":
        raise HTTPException(400, "Superadmin har altid alle moduler")
    before = modules(target)
    if value is None:
        after = set(ROLE_MODULES.get(target.role, ()))
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        unknown = set(value) - set(MODULES)
        if unknown:
            raise HTTPException(400, f"Ukendte moduler: {', '.join(sorted(unknown))}")
        after = set(value)
    else:
        raise HTTPException(400, "Rettigheder skal være en liste af moduler eller null")
    if user.role != "superadmin" and (before ^ after) - modules(user):
        raise HTTPException(403, "Du kan kun give og fjerne moduler, du selv har adgang til")
    target.permissions = None if value is None else sorted(after)


@router.delete("/admin/users/{user_id}/totp")
async def remove_totp(user_id: int, user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    """Fjern brugerens totrinslogin (mistet telefon). Brugeren logger derefter ind med adgangskoden alene."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if target.role in ("admin", "superadmin") and user.role != "superadmin":
        raise HTTPException(403, "Kun superadmin kan fjerne totrinslogin for administratorer")
    try:
        await totp.remove(db, target)
    except auth_service.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await activity.track(user.id, "totp_remove", None, {"user": target.id})
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
async def delete_user(user_id: int, user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None or target.role == "superadmin":
        raise HTTPException(400)
    # Sletning af eksisterende brugere kræver superadmin; en afventende (afvist) ansøgning kan fjernes af dem, der styrer brugere
    if target.approved and not target.is_test and user.role != "superadmin":
        raise HTTPException(403, "Kun superadmin kan slette godkendte brugere")
    tid, kode = target.id, target.obserkode
    await account.delete(db, target)
    await activity.track(user.id, "user_delete", None, {"user": tid, "obserkode": kode})
    await bump("lists")
    return {"ok": True}
