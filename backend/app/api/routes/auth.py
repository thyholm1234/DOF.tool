import hashlib

from fastapi import APIRouter, Cookie, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from backend.app.api.schemas import (
    CommunityLoginRequest,
    CommunityRegisterRequest,
    CommunityUserRead,
    DofConnectionRequest,
    DofLoginRequest,
    DofLoginResponse,
)
from backend.app.core.config import get_settings
from backend.app.db.models import CommunityUser, UserPreference, UserSession
from backend.app.db.session import get_db
from backend.app.services.community_auth import (
    get_current_user,
    hash_password,
    new_session,
    verify_password,
)
from backend.app.services.dof_auth import authenticate_dof_user, fetch_dof_observer_name

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/dof/login", response_model=DofLoginResponse)
async def login_with_dof(credentials: DofLoginRequest) -> DofLoginResponse:
    result = await authenticate_dof_user(
        username=credentials.username,
        password=credentials.password,
    )
    return DofLoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        user=result.user,
    )


@router.post("/dof/connect", response_model=dict)
async def connect_dof_user(
    credentials: DofConnectionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await authenticate_dof_user(credentials.username, credentials.password)
    name = str(result.user.get("name") or result.user.get("display_name") or "").strip()
    if not name:
        name = await fetch_dof_observer_name(credentials.username)
    name = name or credentials.username.upper()

    preference = await db.get(UserPreference, credentials.user_id)
    if preference is None:
        preference = UserPreference(user_id=credentials.user_id)
        db.add(preference)
    preference.observer_code = credentials.username.upper()
    preference.display_name = name
    await db.commit()
    return {"ok": True, "observer_code": preference.observer_code, "display_name": name, "access_token": result.access_token}


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
    )


async def _create_session(user: CommunityUser, db: AsyncSession, response: Response) -> None:
    token, token_hash, expires_at = new_session()
    db.add(UserSession(token_hash=token_hash, user_id=user.id, expires_at=expires_at))
    await db.commit()
    _set_session_cookie(response, token)


@router.post("/register", response_model=CommunityUserRead, status_code=201)
async def register_community_user(
    payload: CommunityRegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> CommunityUser:
    email = payload.email.strip().lower()
    existing = await db.scalar(select(CommunityUser).where(func.lower(CommunityUser.email) == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Der findes allerede en bruger med den e-mail.")
    user_count = await db.scalar(select(func.count(CommunityUser.id)))
    user = CommunityUser(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role="superadmin" if not user_count else "user",
    )
    db.add(user)
    await db.flush()
    await _create_session(user, db, response)
    return user


@router.post("/login", response_model=CommunityUserRead)
async def login_community_user(
    payload: CommunityLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> CommunityUser:
    user = await db.scalar(
        select(CommunityUser).where(func.lower(CommunityUser.email) == payload.email.strip().lower())
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Forkert e-mail eller adgangskode.")
    await _create_session(user, db, response)
    return user


@router.post("/logout", response_model=dict)
async def logout_community_user(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if session_token:
        session = await db.get(UserSession, hashlib.sha256(session_token.encode()).hexdigest())
        if session:
            await db.delete(session)
            await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"status": "ok"}


@router.get("/me", response_model=CommunityUserRead)
async def current_community_user(
    user: CommunityUser = Depends(get_current_user),
) -> CommunityUser:
    return user

