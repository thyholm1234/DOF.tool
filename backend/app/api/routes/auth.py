from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import DofLoginRequest, SessionUser
from backend.app.core.config import get_settings
from backend.app.core.security import (
    clear_login_attempts,
    current_user,
    end_session,
    register_login_attempt,
    require_user,
    start_session,
)
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services.dof_auth import authenticate_dof_user, normalize_obserkode

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_session_user(user: User) -> SessionUser:
    return SessionUser(
        obserkode=user.obserkode,
        navn=user.navn or user.obserkode,
        is_admin=bool(user.is_admin),
        environment=get_settings().environment,
    )


@router.post("/login", response_model=SessionUser)
async def login(
    payload: DofLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SessionUser:
    try:
        obserkode = normalize_obserkode(payload.obserkode)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Ugyldig obserkode."
        ) from error

    if not register_login_attempt(obserkode):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="For mange loginforsøg. Prøv igen om 10 minutter.",
        )

    result = await authenticate_dof_user(obserkode, payload.adgangskode)
    clear_login_attempts(obserkode)

    user = await db.get(User, result.obserkode)
    if user is None:
        user = User(obserkode=result.obserkode, navn=result.navn or result.obserkode)
        db.add(user)
    elif result.navn and user.navn != result.navn:
        user.navn = result.navn
    user.last_login_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(user)

    start_session(request, user)
    return _to_session_user(user)


@router.get("/me", response_model=SessionUser)
async def me(user: User = Depends(require_user)) -> SessionUser:
    return _to_session_user(user)


@router.get("/session")
async def session_state(user: User | None = Depends(current_user)) -> dict:
    """Public endpoint used by the frontend to decide whether to show the login page."""
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "user": _to_session_user(user).model_dump()}


@router.post("/logout")
async def logout(request: Request) -> dict:
    end_session(request)
    return {"status": "ok"}
