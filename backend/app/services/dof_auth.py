from dataclasses import dataclass

import httpx
from fastapi import HTTPException, status

from backend.app.core.config import get_settings


@dataclass(frozen=True)
class DofLoginResult:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    user: dict


async def authenticate_dof_user(username: str, password: str) -> DofLoginResult:
    settings = get_settings()
    login_url = f"{settings.dof_api_base_url}{settings.dof_api_login_path.lstrip('/')}"
    payload = {"username": username, "password": password}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(login_url, json=payload)

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DOFbasen-login fejlede.",
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="DOFbasen returnerede ikke et access_token.",
        )

    return DofLoginResult(
        access_token=access_token,
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        user=data.get("user", {}),
    )
