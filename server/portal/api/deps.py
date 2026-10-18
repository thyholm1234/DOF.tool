from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.db import get_session
from portal.models import User
from portal.services.auth import role_at_least, user_from_token

COOKIE = "dt_session"


async def optional_user(request: Request, db: AsyncSession = Depends(get_session)) -> User | None:
    user = await user_from_token(db, request.cookies.get(COOKIE))
    if user is not None:
        # last_seen opdateres højst hvert 10. minut
        if await kv().set(f"seen:{user.id}", "1", nx=True, ex=600):
            user.last_seen = datetime.now(timezone.utc)
            await db.commit()
    return user


async def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Log ind for at bruge denne funktion")
    return user


def require_role(role: str):
    async def dep(user: User = Depends(require_user)) -> User:
        if not role_at_least(user, role):
            raise HTTPException(403, "Kræver rettigheder som " + role)
        return user

    return dep


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"
