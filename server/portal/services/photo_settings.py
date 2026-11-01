"""Fotomodulets indstillinger (settings-nøglen «fotos») og tekstsider («fotos_tekster»)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from portal.services.setting_store import get_setting_dict, put_setting

KEY, TEXTS = "fotos", "fotos_tekster"
DEFAULTS = {"maanedlig_kvote": 10, "max_pr_art": 30, "upload_max_mb": 25, "default_per_side": 24,
            "afvis_grunde": ["Tvivl om artsbestemmelsen", "Teknisk kvalitet", "Arten er godt dækket i galleriet", "Findes allerede i et andet galleri",
                             "Overholder ikke retningslinjerne"]}


async def get(session: AsyncSession) -> dict:
    return await get_setting_dict(session, KEY, DEFAULTS)


async def save(session: AsyncSession, body: dict) -> dict:
    cur = await get(session)
    for k in ("maanedlig_kvote", "max_pr_art", "upload_max_mb", "default_per_side"):
        if k in body:
            v = int(body[k])
            if not 0 <= v <= 1000:
                raise ValueError(f"{k} skal være mellem 0 og 1000")
            cur[k] = v
    if cur["default_per_side"] not in (24, 48, 96):
        raise ValueError("default_per_side skal være 24, 48 eller 96")
    if "afvis_grunde" in body:
        if not isinstance(body["afvis_grunde"], list):
            raise ValueError("afvis_grunde skal være en liste")
        cur["afvis_grunde"] = [str(x).strip()[:200] for x in body["afvis_grunde"] if str(x).strip()][:30]
    await put_setting(session, KEY, {k: cur[k] for k in DEFAULTS})
    return cur


async def get_texts(session: AsyncSession) -> dict:
    return await get_setting_dict(session, TEXTS, {"retningslinjer": ""})


async def save_texts(session: AsyncSession, body: dict) -> dict:
    cur = await get_texts(session)
    if "retningslinjer" in body:
        cur["retningslinjer"] = str(body["retningslinjer"])[:50000]
    await put_setting(session, TEXTS, cur)
    return cur
