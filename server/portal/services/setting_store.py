"""Nøgle/værdi-indstillinger i tabellen settings (JSONB) – ét sted at læse og skrive dem.

Bruges af alt, der gemmer en konfiguration fra admin-panelet (planer, funktionsflag, backup, sæsoner …) eller en
engangsmarkør ("seed er kørt"). Hvor værdien skal caches på tværs af processer, gør modulet det selv (fx features).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import Setting


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    """Den gemte værdi, eller default hvis nøglen ikke findes (eller er null)."""
    row = await session.get(Setting, key)
    return default if row is None or row.value is None else row.value


async def get_setting_dict(session: AsyncSession, key: str, defaults: dict | None = None) -> dict:
    """Værdien som dict lagt oven på defaults; alt andet end en dict tæller som ingenting."""
    value = await get_setting(session, key)
    return {**(defaults or {}), **(value if isinstance(value, dict) else {})}


async def setting_exists(session: AsyncSession, key: str) -> bool:
    return await session.get(Setting, key) is not None


async def put_setting(session: AsyncSession, key: str, value: Any, *, commit: bool = True) -> None:
    """Gem (opret eller overskriv) værdien. commit=False, når kalderen skriver mere i samme transaktion."""
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    if commit:
        await session.commit()
