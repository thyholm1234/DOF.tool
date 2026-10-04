from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models import CommunityUser, UserSession
from backend.app.db.session import get_db


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def new_session() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=get_settings().session_days
    )
    return token, token_hash, expires_at


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CommunityUser:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Log ind for at fortsætte.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = await db.get(UserSession, token_hash)
    if session is None or session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Sessionen er udløbet.")
    user = await db.get(CommunityUser, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Brugeren findes ikke.")
    return user


async def require_admin(user: CommunityUser = Depends(get_current_user)) -> CommunityUser:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Adminadgang kræves.")
    return user


async def require_superadmin(user: CommunityUser = Depends(get_current_user)) -> CommunityUser:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadminadgang kræves.")
    return user