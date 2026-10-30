"""Hvor ofte workerens planlagte opgaver kører – styres under Admin → Drift.

Hver opgave har et interval (hver N minutter/timer/dage/uger) og for timer og længere et tidspunkt, som kørslerne lægges
efter: «hver 6. time kl. 01:20» giver 01:20, 07:20, 13:20 og 19:20; «hver uge kl. 05:00» kører mandag. Tidspunkterne
regnes fra mandag 3. januar 2000 i dansk tid, så de ligger fast uanset genstarter. Seneste kørsel gemmes i Valkey.

En opgave uden registreret kørsel (fx lige efter denne funktion blev indført) venter til sit næste tidspunkt i stedet
for at starte med det samme – undtagen minut-intervaller, der kører straks.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.timeutil import dk_now
from portal.services.setting_store import get_setting, put_setting

KEY = "worker:schedule"
LAST = "sched:last"
UNITS = {"min": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}
ANCHOR = datetime(2000, 1, 3)  # en mandag

# nøgle → (navn, forklaring, standard)
TASKS: dict[str, tuple[str, str, dict]] = {
    "forecast": ("DMI-prognose", "Vejrprognose til lokaliteter og trækvarsel",
                 {"on": True, "every": 1, "unit": "hour", "at": "00:15"}),
    "compute": ("Genberegning", "Dagsaggregater, områdetal, fænologi, arter pr. år, oprydning og huller",
                {"on": True, "every": 1, "unit": "day", "at": "02:30"}),
    "location_years": ("Lokalitetsoversigt", "Tal pr. lokalitet og år til Lokaliteter-siden",
                       {"on": True, "every": 6, "unit": "hour", "at": "00:40"}),
    "daymap": ("Dagskort", "Kort → Dag: de seneste 7 dage beregnes igen og caches, så sene indtastninger kommer med "
               "(kun når dagskortet er slået til)", {"on": True, "every": 3, "unit": "hour", "at": "00:20"}),
    "reference": ("Referencedata fra DOFbasen", "Arter, lokaliteter og koder hentes igen gennem gatewayen",
                  {"on": True, "every": 1, "unit": "day", "at": "03:00"}),
    "reference_sync": ("Kopiér referencedata", "Fra observationsgruppen til brugergruppen",
                       {"on": True, "every": 1, "unit": "hour", "at": "00:00"}),
    "climate": ("DMI-klima", "Månedsmiddeltemperaturer til fænologien", {"on": True, "every": 1, "unit": "week", "at": "05:00"}),
    "events": ("DOF-ture", "Ture og arrangementer fra dof.dk", {"on": True, "every": 6, "unit": "hour", "at": "01:20"}),
    "gap_fill": ("Udfyld huller", "Dage og måneder der mangler fra DOFbasen lægges i kø",
                 {"on": True, "every": 6, "unit": "hour", "at": "00:00"}),
    "netfugl_kryds": ("Netfugl: krydslister", "Automatiske kryds fra DOFbasen (kun når Netfugl er slået til)",
                      {"on": True, "every": 6, "unit": "hour", "at": "00:10"}),
    "netfugl_rss": ("Netfugl: RSS-kilder", "Henter nyheder fra RSS-kilderne (kun når Netfugl er slået til)",
                    {"on": True, "every": 1, "unit": "hour", "at": "00:05"}),
    "speech_model": ("Talemodel til appen", "Bygger tale-indtastningens model (CoRal) første gang og når modellen er ændret; "
                     "ellers sker der ingenting",
                     {"on": True, "every": 1, "unit": "week", "at": "04:00"}),
    "backup": ("Backup til SFTP", "pg_dump af databaserne sendes til SFTP-serveren (opsættes under Backup; ellers sker der ingenting)",
               {"on": True, "every": 1, "unit": "day", "at": "01:30"}),
    "loc_finds": ("Lokale hits: fund pr. lokalitet", "Én gang om måneden, fordelt på lokalafdelinger (se Fordeling)",
                  {"on": True, "every": 1, "unit": "month", "at": "03:30"}),
}


def _clean(key: str, v: dict) -> dict:
    d = TASKS[key][2]
    unit = d["unit"] if d["unit"] == "month" else v.get("unit", d["unit"])
    if unit not in UNITS and unit != "month":
        raise ValueError(f"Ukendt enhed: {unit}")
    every = 1 if unit == "month" else int(v.get("every", d["every"]))
    if not 1 <= every <= 1000:
        raise ValueError("Intervallet skal være mellem 1 og 1000")
    at = str(v.get("at") or d["at"])
    hh, _, mm = at.partition(":")
    if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
        raise ValueError(f"Ugyldigt tidspunkt: {at}")
    return {"on": bool(v.get("on", d["on"])), "every": every, "unit": unit, "at": f"{int(hh):02d}:{int(mm):02d}"}


async def load(session: AsyncSession) -> dict[str, dict]:
    saved = await get_setting(session, KEY) or {}
    return {k: _clean(k, {**d, **(saved.get(k) or {})}) for k, (_, _, d) in TASKS.items()}


async def save(session: AsyncSession, data: dict) -> dict[str, dict]:
    current = await load(session)
    for k, v in data.items():
        if k in TASKS and isinstance(v, dict):
            current[k] = _clean(k, {**current[k], **v})
    await put_setting(session, KEY, current)
    return current


def _naive(t: datetime) -> datetime:
    return t.astimezone(dk_now().tzinfo).replace(tzinfo=None) if t.tzinfo else t


def slot(cfg: dict, now: datetime) -> datetime | None:
    """Seneste planlagte tidspunkt ≤ now (dansk tid uden tidszone); None for minut-intervaller."""
    if cfg["unit"] == "min":
        return None
    hh, mm = (int(x) for x in cfg["at"].split(":"))
    period = UNITS[cfg["unit"]] * cfg["every"]
    anchor = ANCHOR.replace(hour=hh, minute=mm)
    return anchor + ((now - anchor) // period) * period


def is_due(cfg: dict, last: datetime | None, now: datetime) -> bool:
    if not cfg["on"]:
        return False
    if cfg["unit"] == "min":
        return last is None or now - last >= UNITS["min"] * cfg["every"]
    return last is not None and last < slot(cfg, now)


def next_run(cfg: dict, last: datetime | None, now: datetime) -> datetime | None:
    if not cfg["on"] or cfg["unit"] == "month":
        return None
    if cfg["unit"] == "min":
        return now if last is None else max(now, last + UNITS["min"] * cfg["every"])
    s = slot(cfg, now)
    return now if last is not None and last < s else s + UNITS[cfg["unit"]] * cfg["every"]


async def last_runs() -> dict[str, datetime]:
    return {k: datetime.fromisoformat(v) for k, v in (await kv().hgetall(LAST)).items()}


async def mark(name: str, when: datetime | None = None) -> None:
    await kv().hset(LAST, name, _naive(when or dk_now()).isoformat())


async def due(session: AsyncSession) -> list[str]:
    """Opgaver der skal køre nu (markeres som kørt). Månedsopgaven (loc_finds) har sin egen fordeling."""
    cfg = await load(session)
    last = await last_runs()
    now = _naive(dk_now())
    out = []
    for k, c in cfg.items():
        if c["unit"] == "month":
            continue
        if k not in last and c["unit"] != "min":
            await mark(k, now)  # første gang: vent til næste tidspunkt
            continue
        if is_due(c, last.get(k), now):
            await mark(k, now)
            out.append(k)
    return out


async def overview(session: AsyncSession) -> list[dict]:
    cfg = await load(session)
    last = await last_runs()
    now = _naive(dk_now())
    return [{"key": k, "label": label, "desc": desc, **cfg[k], "last": last[k].isoformat() if k in last else None,
             "next": (n.isoformat() if (n := next_run(cfg[k], last.get(k), now)) else None)}
            for k, (label, desc, _) in TASKS.items()]
