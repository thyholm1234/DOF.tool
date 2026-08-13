from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

import httpx
from fastapi import HTTPException, status

from backend.app.core.config import get_settings

OBSERKODE_RE = re.compile(r"^[A-Z0-9]{2,16}$")
_NAME_MARKER = 'Navn</acronym>:</td><td valign="top">'
_NAME_FALLBACK_RE = re.compile(
    r"Navn(?:</acronym>)?\s*:\s*</td>\s*<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class DofLoginResult:
    obserkode: str
    token: str
    navn: str


def normalize_obserkode(value: str | None) -> str:
    kode = (value or "").strip().upper()
    if not OBSERKODE_RE.fullmatch(kode):
        raise ValueError("Ugyldig obserkode")
    return kode


def extract_observer_name(html: str) -> str:
    content = html or ""
    index = content.find(_NAME_MARKER)
    if index != -1:
        start = index + len(_NAME_MARKER)
        end = content.find("</td>", start)
        if end != -1:
            return unescape(content[start:end].strip())

    match = _NAME_FALLBACK_RE.search(content)
    if not match:
        return ""
    return unescape(re.sub(r"<[^>]+>", "", match.group(1) or "").strip())


async def fetch_observer_name(obserkode: str) -> str:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                str(settings.dof_observer_profile_url), params={"obserkode": obserkode}
            )
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        return ""
    return extract_observer_name(response.text)


async def authenticate_dof_user(obserkode: str, password: str) -> DofLoginResult:
    """Validate credentials against DOFbasen and return the observer's token and name."""
    settings = get_settings()
    try:
        kode = normalize_obserkode(obserkode)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                str(settings.dof_login_url), json={"username": kode, "password": password}
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Kunne ikke kontakte DOFbasen.",
        ) from error

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DOFbasen-login fejlede. Tjek obserkode og adgangskode.",
        )

    token = (response.json() or {}).get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="DOFbasen returnerede ikke et token.",
        )

    return DofLoginResult(obserkode=kode, token=token, navn=await fetch_observer_name(kode))
