"""Oversigten over alle lokaliteter (Lokaliteter-siden).

Bygges af workeren hver 6. time i `location_year` (én række pr. lokalitet og år; year = 0 er alle år), fordi alle år i
ét svep over hele arkivet tager ca. 20 sekunder og et enkelt år ca. 1–2. Rækken for indeværende år har desuden tal for
de seneste 30 dage. Siden læser kun tabellen. En enkelt lokalitets side bygges derimod altid ved indlæsning (api/geo.py).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump, kv
from portal.timeutil import dk_today

log = logging.getLogger("portal.locyear")

RANK = "CASE kategori WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 WHEN 'bemaerk' THEN 1 ELSE 0 END"
BUILT = "locyear:built"
EVERY = 6 * 3600  # sekunder mellem to bygninger


async def refresh_year(session: AsyncSession, year: int) -> int:
    """Genberegn ét år (0 = alle år). Returnerer antal lokaliteter."""
    period = "" if year == 0 else "AND dato >= :a AND dato < :b"
    params: dict = {"y": year, "d30": dk_today() - timedelta(days=30)}
    if year:
        params.update(a=date(year, 1, 1), b=date(year + 1, 1, 1))
    await session.execute(text("DELETE FROM location_year WHERE year = :y"), {"y": year})
    res = await session.execute(text(f"""
        INSERT INTO location_year (loknr, year, n_obs, n_taxa, last_date, rank, n_30, rank30)
        SELECT loknr, :y, count(*), count(DISTINCT artnr), max(dato), max({RANK}),
               count(*) FILTER (WHERE dato > :d30), coalesce(max({RANK}) FILTER (WHERE dato > :d30), 0)
        FROM observations WHERE hemmelig = 0 AND loknr IS NOT NULL AND dato <= current_date {period} GROUP BY loknr
    """), params)
    await session.commit()
    return res.rowcount or 0


async def refresh(session: AsyncSession, years: list[int] | None = None) -> None:
    """Genberegn de angivne år (None = alle år i arkivet) og alle-år-rækken."""
    last = dk_today().year
    t0 = time.time()
    if years is None:
        first = (await session.execute(text("SELECT extract(year FROM min(dato))::int FROM observations"))).scalar() or last
        years = list(range(first, last + 1))
    todo = [*years, 0]
    for i, y in enumerate(todo):
        await jobstatus.step("Alle år" if y == 0 else f"Lokalitetstal: {y}", i, len(todo))
        await refresh_year(session, y)
    await kv().set(BUILT, json.dumps({"at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.time() - t0, 1),
                                      "years": [y for y in todo if y]}))
    await bump("locyear")
    log.info("Lokalitetsoversigt bygget for %s (%.0f s)", years, time.time() - t0)


async def ensure(session: AsyncSession) -> None:
    """Fuld beregning første gang (tom tabel); ellers indeværende og sidste år samt alle år (rettelser i arkivet)."""
    empty = not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM location_year WHERE year > 0)"))).scalar()
    last = dk_today().year
    await refresh(session, None if empty else [last - 1, last])


async def built_info() -> dict | None:
    raw = await kv().get(BUILT)
    return json.loads(raw) if raw else None
