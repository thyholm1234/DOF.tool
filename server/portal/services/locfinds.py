"""Estimerede fund pr. art og lokalitet gennem alle år – grundlag for 'lokale hits'.

Et fund er som i finds.py, men inden for én lokalitet: dage med arten hører til samme fund, når der højst er
SITE_GAP_DAYS dage imellem (en fugl der bliver på stedet), eller højst LINK_DAYS dag, hvis fuglen var trækkende den ene
af dagene (en trækkende fugl er videre og kommer ikke igen). Ti dages rast giver altså ét fund, ikke ti.

Beregningen er tung (alle observationer), så den køres én lokalafdeling ad gangen, spredt over måneden efter en plan
der styres under Admin → Drift (Fordeling). Lokaliteter uden lokalafdeling er sin egen portion (afdeling 0).
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import COMMANDS, kv
from portal.models import Setting
from portal.services.finds import LINK_DAYS, MIGRATING, SITE_GAP_DAYS
from portal.timeutil import dk_now

PLAN = "locfinds:plan"
STATUS = "locfinds:status"
NONE = 0  # lokaliteter uden lokalafdeling
LAST_DAY = 28  # findes i alle måneder

# Dagens sidste adfærdsled uden "HØ" (kun hørt) – trækkende hvis det er en retning eller T/TU/TF/OF (som finds.is_migrating)
_MIG = (r"coalesce(substring(regexp_replace(coalesce(o.adfkode, ''), '(\s+HØ)+\s*$', '') from '(\S+)\s*$') = ANY(CAST(:mig AS text[])), false)")


def _loc_filter(afd: int) -> str:
    return "afdeling_id IS NULL" if afd == NONE else "afdeling_id = :afd"


async def compute(session: AsyncSession, afd: int) -> int:
    """Genberegn alle lokaliteter i én lokalafdeling. Returnerer antal (lokalitet, art)-rækker."""
    params = {"afd": afd, "mig": list(MIGRATING), "gap": SITE_GAP_DAYS, "link": LINK_DAYS}
    await session.execute(text(f"DELETE FROM loc_species_finds WHERE loknr IN (SELECT loknr FROM locations WHERE {_loc_filter(afd)})"),
                          params)
    res = await session.execute(text(f"""
        WITH d AS (
            SELECT o.loknr, o.artnr, o.dato, bool_and({_MIG}) AS mig
            FROM observations o
            WHERE o.loknr IN (SELECT loknr FROM locations WHERE {_loc_filter(afd)}) AND o.antal > 0 AND o.dato < current_date
            GROUP BY o.loknr, o.artnr, o.dato
        ), g AS (
            SELECT loknr, artnr, dato, dato - lag(dato) OVER w AS gap, mig OR lag(mig) OVER w AS mig
            FROM d WINDOW w AS (PARTITION BY loknr, artnr ORDER BY dato)
        )
        INSERT INTO loc_species_finds (loknr, artnr, n_finds, n_days, last_date)
        SELECT loknr, artnr, count(*) FILTER (WHERE gap IS NULL OR gap > (CASE WHEN mig THEN CAST(:link AS integer) ELSE CAST(:gap AS integer) END)),
               count(*), max(dato)
        FROM g GROUP BY loknr, artnr
    """), params)
    await session.commit()
    return res.rowcount or 0


async def batches(session: AsyncSession) -> list[dict]:
    """Lokalafdelinger (og 'uden lokalafdeling') med antal lokaliteter."""
    rows = (await session.execute(text("""
        SELECT coalesce(l.afdeling_id, 0) AS id, coalesce(a.navn, 'Uden lokalafdeling') AS navn, count(*) AS n_loc
        FROM locations l LEFT JOIN afdelinger a ON a.id = l.afdeling_id
        GROUP BY 1, 2 ORDER BY (coalesce(l.afdeling_id, 0) = 0), 2
    """))).mappings().all()
    return [dict(r) for r in rows]


def spread(ids: list[int], first: int = 1, last: int = LAST_DAY) -> dict[str, int]:
    """Fordel portionerne jævnt over dagene first..last i måneden."""
    first, last = max(1, min(first, LAST_DAY)), max(1, min(last, LAST_DAY))
    if last < first:
        first, last = last, first
    n = len(ids)
    span = last - first
    return {str(i): first + (round(k * span / (n - 1)) if n > 1 else 0) for k, i in enumerate(ids)}


async def _setting(session: AsyncSession, key: str) -> dict:
    row = await session.get(Setting, key)
    return dict((row.value if row else None) or {})


async def _put(session: AsyncSession, key: str, value: dict) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def get_plan(session: AsyncSession, ids: list[int] | None = None) -> dict:
    """{"days": {afdeling: dag i måneden}}; nye afdelinger fordeles jævnt indtil planen gemmes."""
    plan = await _setting(session, PLAN)
    ids = ids if ids is not None else [b["id"] for b in await batches(session)]
    default = spread(ids)
    days = {str(i): int((plan.get("days") or {}).get(str(i), default[str(i)])) for i in ids}
    return {"days": days}


async def save_plan(session: AsyncSession, data: dict) -> dict:
    ids = [b["id"] for b in await batches(session)]
    days = {}
    for i in ids:
        v = (data.get("days") or {}).get(str(i))
        if v is not None:
            if not 1 <= int(v) <= LAST_DAY:
                raise ValueError(f"Dagen skal være mellem 1 og {LAST_DAY}")
            days[str(i)] = int(v)
    current = await get_plan(session, ids)
    await _put(session, PLAN, {"days": {**current["days"], **days}})
    return await get_plan(session, ids)


async def status(session: AsyncSession) -> dict:
    return await _setting(session, STATUS)


async def mark(session: AsyncSession, afd: int, **fields) -> None:
    st = await status(session)
    st[str(afd)] = {**(st.get(str(afd)) or {}), **fields}
    await _put(session, STATUS, st)


def target(day: int, at: str, now: datetime) -> datetime:
    """Denne måneds kørselstidspunkt for en portion (dansk tid uden tidszone)."""
    hh, mm = (int(x) for x in at.split(":"))
    return datetime(now.year, now.month, min(day, LAST_DAY), hh, mm)


async def due(session: AsyncSession, at: str) -> list[int]:
    """Portioner hvis dag i denne måned er nået, og som ikke er kørt eller lagt i kø siden."""
    now = dk_now().replace(tzinfo=None)
    plan = (await get_plan(session))["days"]
    st = await status(session)
    out = []
    for afd, day in plan.items():
        t = target(day, at, now)
        s = st.get(afd) or {}
        seen = max((datetime.fromisoformat(s[k]) for k in ("at", "queued_at") if s.get(k)), default=None)
        if now >= t and (seen is None or seen < t):
            out.append(int(afd))
    return out


async def run(session: AsyncSession, afd: int) -> int:
    t0 = time.time()
    await mark(session, afd, started_at=dk_now().replace(tzinfo=None).isoformat(), error=None)
    try:
        n = await compute(session, afd)
    except Exception as exc:
        await session.rollback()
        await mark(session, afd, error=str(exc).splitlines()[0][:300])
        raise
    await mark(session, afd, at=dk_now().replace(tzinfo=None).isoformat(), rows=n, secs=round(time.time() - t0, 1))
    return n


async def has_data(session: AsyncSession) -> bool:
    return bool(await session.scalar(text("SELECT EXISTS (SELECT 1 FROM loc_species_finds)")))


async def enqueue(session: AsyncSession, ids: list[int] | None = None) -> list[int]:
    """Læg portioner (None = alle) i kø hos workerens kommando-lytter og markér dem, så planen ikke tager dem igen."""
    ids = ids if ids is not None else [b["id"] for b in await batches(session)]
    st = await status(session)
    now = dk_now().replace(tzinfo=None).isoformat()
    for afd in ids:
        st[str(afd)] = {**(st.get(str(afd)) or {}), "queued_at": now}
    await _put(session, STATUS, st)
    await kv().rpush(COMMANDS, json.dumps({"cmd": "loc_finds", "afdelinger": ids}))
    await jobstatus.queued("loc_finds")
    return ids
