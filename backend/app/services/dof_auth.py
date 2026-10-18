from dataclasses import dataclass
import html
import re

import httpx
from fastapi import HTTPException, status

from backend.app.core.config import get_settings


@dataclass(frozen=True)
class DofLoginResult:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    user: dict


def extract_dof_observer_name(page: str) -> str:
    match = re.search(
        r"Navn</acronym>:\s*</td>\s*<td[^>]*>(.*?)</td>",
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    value = html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))
    return _clean_observer_name(value)


def _clean_observer_name(value: str) -> str:
    value = html.unescape(value or "").replace("\xa0", " ").strip()
    if "�" not in value and any(marker in value for marker in ("Ã", "Â", "â")):
        try:
            repaired = value.encode("latin-1").decode("utf-8")
            if "�" not in repaired:
                value = repaired
        except UnicodeError:
            pass
    return value.strip(" \t\r\n\"'“”")


async def fetch_dof_observer_name(observer_code: str) -> str:
    """Look up the public display name after DOFbasen authentication."""
    url = f"https://dofbasen.dk/popobser.php?obserkode={observer_code}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "DOF.tool/1.0"})
            response.raise_for_status()
    except httpx.HTTPError:
        return ""

    # DOFbasen deklarerer ISO-8859-1 i HTML'en, mens HTTP-headeren ikke altid gør.
    page = response.content.decode("iso-8859-1", errors="replace")
    return extract_dof_observer_name(page)


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
    access_token = data.get("access_token") or data.get("token")
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
