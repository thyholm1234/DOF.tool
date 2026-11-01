from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.db import get_session
from portal.models import User
from portal.services import view_as
from portal.services.auth import MODULES, SUPERADMIN, can, role_at_least, user_from_token

COOKIE = "dt_session"


async def real_user(request: Request, db: AsyncSession = Depends(get_session)) -> User | None:
    """Den indloggede bruger selv (også når en superadmin ser appen som en anden)."""
    user = await user_from_token(db, request.cookies.get(COOKIE))
    if user is not None:
        # last_seen opdateres højst hvert 10. minut
        if await kv().set(f"seen:{user.id}", "1", nx=True, ex=600):
            user.last_seen = datetime.now(timezone.utc)
            await db.commit()
    return user


async def optional_user(request: Request, real: User | None = Depends(real_user), db: AsyncSession = Depends(get_session)) -> User | None:
    """Brugeren, appen skal vises for: den indloggede – eller, i «se som bruger»-tilstanden (kun superadmin, kun læsning), målbrugeren."""
    if real is not None and real.role == SUPERADMIN:
        state = await view_as.current(request.cookies.get(COOKIE))
        if state is not None:
            target = await db.get(User, state["user_id"])
            if target is not None and not target.blocked:
                request.state.view_as = {**state, "real_id": real.id, "real_navn": real.navn or real.obserkode}
                return target
    return real


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


def require_perm(module: str):
    """Adgang til et admin-modul (rolle eller individuel rettighed)."""
    assert module in MODULES

    async def dep(user: User = Depends(require_user)) -> User:
        if not can(user, module):
            raise HTTPException(403, f"Kræver adgang til {MODULES[module].split(':')[0]}")
        return user

    return dep


def client_ip(request: Request) -> str:
    """Klientens IP. Uvicorn sætter den fra X-Forwarded-For, men kun når forespørgslen kommer fra en betroet proxy
    (FORWARDED_ALLOW_IPS) – headeren læses aldrig direkte, for den kan klienten selv skrive."""
    return request.client.host if request.client else "?"
