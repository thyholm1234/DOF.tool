"""Fælles login med DOFbasen-obserkode.

Første login verificeres hos DOFbasen (via gatewayen – tæller med i 1 kald/min). Derefter gemmes
en argon2-hash lokalt, så efterfølgende logins ikke belaster DOFbasen. Adgangskoden gemmes aldrig
i klartekst; i gateway-køen ligger den højst 3 minutter.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.config import get_settings
from portal.dof import gateway
from portal.models import User

ph = PasswordHasher()
OBSERKODE_RE = re.compile(r"^[A-Z0-9]{2,16}$")
ROLES = ("user", "editor", "admin", "superadmin")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def normalize(kode: str) -> str:
    kode = (kode or "").strip().upper()
    if not OBSERKODE_RE.fullmatch(kode):
        raise AuthError("Ugyldig obserkode", 400)
    return kode


def verify(hash_: str | None, password: str) -> bool:
    if not hash_:
        return False
    try:
        return ph.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


async def ensure_superadmin(session: AsyncSession) -> None:
    s = get_settings()
    if not s.superadmin_obserkode or not s.superadmin_password:
        return
    kode = normalize(s.superadmin_obserkode)
    user = (await session.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if user is None:
        user = User(obserkode=kode, navn=kode, role="superadmin", prefs={})
        session.add(user)
    user.role = "superadmin"
    if not verify(user.password_hash, s.superadmin_password):
        user.password_hash = ph.hash(s.superadmin_password)
    await session.commit()


async def _rate_limit(kode: str, ip: str) -> None:
    r = kv()
    for key in (f"login:fail:{kode}", f"login:fail:ip:{ip}"):
        n = int(await r.get(key) or 0)
        if n >= (8 if "ip:" not in key else 30):
            raise AuthError("For mange forsøg. Prøv igen om 10 minutter.", 429)


async def _fail(kode: str, ip: str) -> None:
    r = kv()
    for key in (f"login:fail:{kode}", f"login:fail:ip:{ip}"):
        await r.incr(key)
        await r.expire(key, 600)


async def login(session: AsyncSession, obserkode: str, password: str, ip: str) -> tuple[User, bool]:
    """Returnerer (bruger, ny_bruger)."""
    kode = normalize(obserkode)
    if not password:
        raise AuthError("Adgangskode mangler", 400)
    await _rate_limit(kode, ip)
    user = (await session.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if user and user.blocked:
        raise AuthError("Brugeren er spærret", 403)
    if user and verify(user.password_hash, password):
        if user.navn in (None, "", user.obserkode):
            navn = (await session.execute(text(
                "SELECT trim(coalesce(fornavn,'') || ' ' || coalesce(efternavn,'')) FROM observers WHERE obserkode = :k"),
                {"k": kode})).scalar()
            if navn:
                user.navn = navn
                await session.commit()
        return user, False

    # Verificér hos DOFbasen – samme kø som alt andet, men forrest
    job_id = await gateway.enqueue("login", {"obserkode": kode}, priority=gateway.INTERACTIVE,
                                   secret={"password": password})
    result = await gateway.wait_result(job_id, timeout=150)
    if result is None:
        await gateway.cancel(job_id)
        raise AuthError("DOFbasen svarede ikke i tide. Prøv igen om lidt.", 503)
    if not result.get("ok") or not (result.get("result") or {}).get("valid"):
        await _fail(kode, ip)
        raise AuthError("Forkert obserkode eller adgangskode (tjekket hos DOFbasen)")

    new = user is None
    if new:
        navn = (await session.execute(text(
            "SELECT trim(coalesce(fornavn,'') || ' ' || coalesce(efternavn,'')) FROM observers WHERE obserkode = :k"),
            {"k": kode})).scalar()
        user = User(obserkode=kode, navn=navn or kode, role="user",
                    prefs={"lists_public": True, "push_enabled": True, "afdelinger": [], "kommuner": []})
        session.add(user)
    user.password_hash = ph.hash(password)
    user.dof_verified_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user, new


async def create_session(user: User) -> str:
    token = secrets.token_urlsafe(32)
    await kv().set(f"sess:{token}", user.id, ex=get_settings().session_days * 86400)
    return token


async def user_from_token(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    user_id = await kv().get(f"sess:{token}")
    if not user_id:
        return None
    user = await session.get(User, int(user_id))
    if user is None or user.blocked:
        return None
    return user


async def destroy_session(token: str | None) -> None:
    if token:
        await kv().delete(f"sess:{token}")


def role_at_least(user: User | None, role: str) -> bool:
    return bool(user) and ROLES.index(user.role) >= ROLES.index(role)
