"""Indstillinger for artikelmodulet (tabellen settings, nøgle «artikler»)."""

from __future__ import annotations

import json
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.services.setting_store import get_setting_dict, put_setting

KEY = "artikler"
DEFAULTS: dict = {
    "embed_hosts": ["xeno-canto.org", "player.vimeo.com", "www.youtube-nocookie.com", "dofbasen.dk", "www.dof.dk"],
    "report_threshold": 3,
    "comments_enabled": True,
    "comment_edit_minutes": 15,
    "ambiguous_species": ["Ravn", "Krage", "Due", "Stær", "Høg", "Spurv", "Musvit", "Svale", "Gøg", "Hejre", "Svane", "Ugle"],
    "front": {"hero_count": 1, "secondary_count": 3, "on_this_day": True},
}


FRAME_KEY = "artikler:frame_hosts"
_frame: dict = {"at": 0.0, "hosts": None}


async def get(session: AsyncSession) -> dict:
    stored = await get_setting_dict(session, KEY, DEFAULTS)
    out = {**DEFAULTS, **stored}
    out["front"] = {**DEFAULTS["front"], **(stored.get("front") or {})}
    return out


async def frame_hosts() -> list[str]:
    """Værter, siden må indlejre (iframe) – Content-Security-Policy frame-src følger samme liste som rendererens tilladelsesliste.
    Gemmes i Valkey ved ændring, så sidevisninger ikke skal i databasen; 30 sekunders cache i processen."""
    if _frame["hosts"] is not None and time.monotonic() - _frame["at"] < 30:
        return _frame["hosts"]
    try:
        raw = await kv().get(FRAME_KEY)
        hosts = json.loads(raw) if raw else list(DEFAULTS["embed_hosts"])
    except Exception:  # noqa: BLE001 – en Valkey-fejl må ikke vælte sidevisningen
        hosts = list(DEFAULTS["embed_hosts"])
    _frame.update(at=time.monotonic(), hosts=hosts)
    return hosts


async def save(session: AsyncSession, changes: dict) -> dict:
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError("Ukendte indstillinger: " + ", ".join(sorted(unknown)))
    cur = await get(session)
    cur.update(changes)
    hosts = sorted({str(h).strip().lower() for h in cur["embed_hosts"] if str(h).strip()})
    if bad := [h for h in hosts if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", h)]:
        raise ValueError("Ugyldigt værtsnavn: " + ", ".join(bad) + " (skriv kun domænet, fx xeno-canto.org)")
    cur["embed_hosts"] = hosts
    cur["report_threshold"] = max(1, min(50, int(cur["report_threshold"])))
    cur["comment_edit_minutes"] = max(0, min(1440, int(cur["comment_edit_minutes"])))
    await put_setting(session, KEY, cur)
    await kv().set(FRAME_KEY, json.dumps(cur["embed_hosts"]))
    _frame["hosts"] = None
    return cur
