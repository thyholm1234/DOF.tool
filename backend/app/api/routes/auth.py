from fastapi import APIRouter

from backend.app.api.schemas import DofLoginRequest, DofLoginResponse
from backend.app.services.dof_auth import authenticate_dof_user

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

