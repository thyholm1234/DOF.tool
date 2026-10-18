"""Dataoverblik og opdateringsplaner for DOFbasen-observationer og DMI-vejrarkivet.

Planer (som i DML-Bird-Populations' datapanel, men kørende af sig selv): for hvert vindue – dagen i dag,
de sidste 3 dage, den sidste uge, den sidste måned, det sidste år og alle data – vælges om og hvor ofte
data skal hentes igen. Planlæggeren i workeren lægger jobs i kø; DOFbasen-jobs går gennem gatewayen
(højst ét kald i minuttet), vejr-jobs gennem vejrarkivets egen kø (én månedsfil ad gangen).

DOFbasen: i dag = live-hentningens interval; 3 dage/uge = dagsudtræk; måned/år/alle = månedsudtræk.
Vejr: DMI's 10 km-grid findes som månedsfiler, så alle vinduer oversættes til de måneder, de rører.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv
from portal.models import Setting
from portal.timeutil import dk_today

TIERS = [("today", "Dagen i dag"), ("days3", "De sidste 3 dage"), ("week", "Den sidste uge"),
         ("month", "Den sidste måned"), ("year", "Det sidste år"), ("all", "Alle data")]
UNITS = {"min": 60, "hour": 3600, "day": 86400}

DEFAULTS = {
    "dof": {
        "today": {"on": True, "every": 1, "unit": "min"},
        "days3": {"on": True, "every": 1, "unit": "hour"},
        "week": {"on": True, "every": 1, "unit": "day"},
        "month": {"on": True, "every": 7, "unit": "day"},
        "year": {"on": True, "every": 30, "unit": "day"},
        "all": {"on": False, "every": 365, "unit": "day"},
    },
    "weather": {
        "today": {"on": False, "every": 1, "unit": "day"},
        "days3": {"on": False, "every": 1, "unit": "day"},
        "week": {"on": False, "every": 1, "unit": "day"},
        "month": {"on": True, "every": 7, "unit": "day"},
        "year": {"on": True, "every": 30, "unit": "day"},
        "all": {"on": False, "every": 365, "unit": "day"},
    },
}
DOF_FIRST_YEAR = 1961
WEATHER_FIRST_YEAR = 2011


def interval_s(t: dict) -> int:
    return max(60, int(t.get("every") or 1) * UNITS.get(t.get("unit"), 86400))


async def get_plan(session: AsyncSession, source: str) -> dict:
    row = await session.get(Setting, f"sync:{source}")
    saved = (row.value if row else None) or {}
    plan = {k: {**v, **(saved.get("tiers", {}).get(k) or {})} for k, v in DEFAULTS[source].items()}
    out = {"tiers": plan}
    if source == "weather":
        out["start_year"] = int(saved.get("start_year") or WEATHER_FIRST_YEAR)
        out["workers"] = int(saved.get("workers") or 0)
    return out


async def save_plan(session: AsyncSession, source: str, data: dict) -> dict:
    current = await get_plan(session, source)
    tiers = current["tiers"]
    for k, v in (data.get("tiers") or {}).items():
        if k not in tiers or not isinstance(v, dict):
            continue
        unit = v.get("unit", tiers[k]["unit"])
        if unit not in UNITS:
            raise ValueError(f"Ukendt enhed: {unit}")
        every = int(v.get("every", tiers[k]["every"]))
        if every < 1:
            raise ValueError("Intervallet skal være mindst 1")
        tiers[k] = {"on": bool(v.get("on", tiers[k]["on"])), "every": every, "unit": unit}
    value = {"tiers": tiers}
    if source == "weather":
        value["start_year"] = max(1990, min(dk_today().year, int(data.get("start_year") or current["start_year"])))
        value["workers"] = max(0, min(32, int(data.get("workers", current["workers"]) or 0)))
    row = await session.get(Setting, f"sync:{source}")
    if row is None:
        session.add(Setting(key=f"sync:{source}", value=value))
    else:
        row.value = value
    await session.commit()
    if source == "dof":
        await apply_live_interval(tiers["today"])
    return await get_plan(session, source)


async def apply_live_interval(today_tier: dict) -> None:
    """Live-hentningens interval læses af gatewayen (aldrig under minimumsintervallet)."""
    await kv().set("dof:live_interval", interval_s(today_tier) if today_tier.get("on") else 0)


async def last_runs(source: str) -> dict:
    return await kv().hgetall(f"sync:last:{source}")


async def mark_run(source: str, tier: str) -> None:
    await kv().hset(f"sync:last:{source}", tier, datetime.now(timezone.utc).isoformat())


async def due_tiers(session: AsyncSession, source: str) -> list[str]:
    plan = (await get_plan(session, source))["tiers"]
    last = await last_runs(source)
    now = datetime.now(timezone.utc)
    due = []
    for key, _ in TIERS:
        t = plan[key]
        if not t["on"] or (source == "dof" and key == "today"):
            continue
        prev = last.get(key)
        if prev is None or (now - datetime.fromisoformat(prev)).total_seconds() >= interval_s(t):
            due.append(key)
    return due


# ---------------------------------------------------------------- hvad et vindue dækker


def months_between(a: date, b: date) -> list[tuple[int, int]]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def window_days(tier: str) -> int | None:
    return {"today": 0, "days3": 3, "week": 7, "month": 31, "year": 366}.get(tier)


def tier_months(tier: str, first_year: int) -> list[tuple[int, int]]:
    today = dk_today()
    if tier == "all":
        return months_between(date(first_year, 1, 1), today)
    start = today - timedelta(days=window_days(tier) or 0)
    return months_between(start, today)


def dof_tier_jobs(tier: str) -> list[tuple[str, dict]]:
    """(job, parametre) for et DOFbasen-vindue. Korte vinduer bruger dagsudtræk, lange månedsudtræk."""
    today = dk_today()
    if tier in ("days3", "week"):
        return [("day", {"date": (today - timedelta(days=i)).isoformat()}) for i in range(1, window_days(tier) + 1)]
    if tier in ("month", "year", "all"):
        return [("month", {"year": y, "month": m}) for y, m in reversed(tier_months(tier, DOF_FIRST_YEAR))]
    return []


def calls_per_day(source: str, plan: dict) -> dict:
    """Skøn over antal kald/filer pr. døgn pr. vindue (til panelet)."""
    out = {}
    for key, _ in TIERS:
        t = plan[key]
        if not t["on"]:
            out[key] = 0.0
            continue
        per_run = (1 if key == "today" else len(dof_tier_jobs(key))) if source == "dof" \
            else len(tier_months(key, WEATHER_FIRST_YEAR))
        out[key] = round(per_run * 86400 / interval_s(t), 1)
    return out


# ---------------------------------------------------------------- DOFbasen: månedsoversigt


async def dof_counts(session: AsyncSession) -> dict:
    """Observationer pr. måned (dyr optælling – caches 6 timer, nulstilles efter månedsjobs)."""

    async def produce():
        rows = (await session.execute(text("""
            SELECT to_char(date_trunc('month', dato), 'YYYY-MM') AS k, count(*) FROM observations GROUP BY 1
        """))).all()
        return {k: n for k, n in rows}

    return await cached("datastat:dof:counts", 6 * 3600, produce)


async def dof_status(session: AsyncSession) -> dict:
    counts = await dof_counts(session)
    full = {}
    for y, m, src, rows, at in (await session.execute(text(
            "SELECT year, month, source, rows, fetched_at FROM month_sync"))).all():
        full[f"{y}-{m:02d}"] = {"source": src, "rows": rows, "at": at}
    for name, rows, at in (await session.execute(text("SELECT name, rows, imported_at FROM archive_files"))).all():
        parts = name.replace(".csv", "").split("_")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            k = f"{parts[1]}-{parts[2]}"
            if k not in full or full[k]["at"] < at:
                full[k] = {"source": "arkiv", "rows": rows, "at": at}
    days = {}
    for k, n, oldest, newest in (await session.execute(text("""
        SELECT to_char(date_trunc('month', dato), 'YYYY-MM'), count(*), min(fetched_at), max(fetched_at)
        FROM day_sync WHERE source <> 'archive' GROUP BY 1
    """))).all():
        days[k] = (n, oldest, newest)
    queued = await queued_months()
    today = dk_today()
    months = {}
    for key in sorted(set(counts) | set(full) | set(days)):
        y, m = int(key[:4]), int(key[5:])
        n_days = calendar.monthrange(y, m)[1] if (y, m) != (today.year, today.month) else max(today.day - 1, 0)
        f = full.get(key)
        d = days.get(key)
        updated = f["at"] if f else None
        source = f["source"] if f else None
        if d and n_days and d[0] >= n_days and (updated is None or d[1] > updated):
            updated, source = d[1], "dage"  # alle dage hentet enkeltvis efter seneste månedshentning
        months[key] = {"obs": counts.get(key, 0), "source": source, "updated": updated.isoformat() if updated else None,
                       "days": d[0] if d else 0, "days_total": n_days, "days_newest": d[2].isoformat() if d else None,
                       "queued": key in queued}
    return {"months": months, "first_year": DOF_FIRST_YEAR}


async def queued_months() -> set[str]:
    out = set()
    async for k in kv().scan_iter(match="dof:dedupe:month:*", count=500):
        y, _, m = k.rsplit(":", 1)[1].partition("-")
        if y.isdigit() and m.isdigit():
            out.add(f"{y}-{int(m):02d}")
    return out


async def record_month(session: AsyncSession, y: int, m: int, source: str, rows: int) -> None:
    await session.execute(text("""
        INSERT INTO month_sync (year, month, source, rows, fetched_at) VALUES (:y, :m, :s, :r, now())
        ON CONFLICT (year, month) DO UPDATE SET source = :s, rows = :r, fetched_at = now()
    """), {"y": y, "m": m, "s": source, "r": rows})
    await kv().delete("datastat:dof:counts")


def jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, default=str))
