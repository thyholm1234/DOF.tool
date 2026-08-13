from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models import User
from backend.app.db.session import get_db

SESSION_KEY = "obserkode"

_login_attempts: dict[str, list[float]] = defaultdict(list)


def register_login_attempt(obserkode: str) -> bool:
    """Record a login attempt. Returns False when the rate limit is exceeded."""
    settings = get_settings()
    now = time.time()
    window = settings.login_attempt_window_seconds
    attempts = [stamp for stamp in _login_attempts[obserkode] if now - stamp < window]
    if len(attempts) >= settings.login_max_attempts:
        _login_attempts[obserkode] = attempts
        return False
    attempts.append(now)
    _login_attempts[obserkode] = attempts
    return True


def clear_login_attempts(obserkode: str) -> None:
    _login_attempts.pop(obserkode, None)


def start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session[SESSION_KEY] = user.obserkode


def end_session(request: Request) -> None:
    request.session.clear()


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    obserkode = request.session.get(SESSION_KEY)
    if not obserkode:
        return None
    return await db.get(User, obserkode)


async def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login påkrævet.",
        )
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Kræver admin.")
    return user


def require_service_key(x_admin_key: str | None = Header(default=None)) -> None:
    """Guard for machine-to-machine endpoints that cannot hold a browser session."""
    if x_admin_key != get_settings().admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldig service-nøgle.",
        )
