"""Oversigten over alle lokaliteter (Lokaliteter-siden).

Bygges af workeren hver 6. time i `location_year` (én række pr. lokalitet og år; year = 0 er alle år), fordi alle år i
ét svep over hele arkivet tager ca. 20 sekunder og et enkelt år ca. 1–2. Rækken for indeværende år har desuden tal for
de seneste 30 dage. Siden læser kun tabellen.

En enkelt lokalitets sider bygges ved indlæsning og caches en time (api/geo.py). Artslisten ('Arter på lokaliteten') er
den tunge del; for de mest aktive lokaliteter bygges den på forhånd sammen med oversigten og holder til næste bygning.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump, get_json, kv, set_json
from portal.services import migration
from portal.timeutil import dk_today

log = logging.getLogger("portal.locyear")

RANK = "CASE kategori WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 WHEN 'bemaerk' THEN 1 ELSE 0 END"
BUILT = "locyear:built"
EVERY = 6 * 3600  # sekunder mellem to bygninger
TOP_N = 100       # artslister bygget på forhånd for de mest aktive lokaliteter
KAT = {3: "SU", 2: "SUB", 1: "bemaerk", 0: "alm"}


def species_key(loknr: int, year: int | None) -> str:
    return f"lok:sp:{loknr}:{year or 'alle'}"


async def species_list(session: AsyncSession, loknr: int, year: int | None) -> dict:
    """Artsliste med rekorder for lokaliteten – i alt eller for ét år – og årene med observationer.

    Flere turlister samme dag tæller ikke dobbelt: én pr. art pr. dag med det største antal. Rekorden er dagens største
    enkeltobservation (ikke summen, så samme flok talt af flere ikke lægges sammen). kategori = den højeste, arten har
    haft her (i året); group = trækkortets artsgruppe.

    Alle år: listen og årene i én gennemgang af lokalitetens observationer. Ét år: kun årets observationer, og årene
    genbruges fra alle-år-listen i cachen, når den findes (de er ens for alle år)."""
    per_day = f"""SELECT artnr, dato, max(antal) AS antal, max({RANK}) AS rank
        FROM observations WHERE loknr = :l AND hemmelig = 0 AND antal > 0 {{period}} GROUP BY artnr, dato"""
    species_sql = """SELECT s.artnr, s.navn, s.sortering, count(*) AS n_obs, max(d.dato) AS last_date, max(d.rank) AS rank,
               max(d.antal) AS rekord, (array_agg(d.dato ORDER BY d.antal DESC, d.dato DESC))[1] AS rekord_dato
        FROM d JOIN species s ON s.artnr = d.artnr GROUP BY s.artnr, s.navn, s.sortering ORDER BY n_obs DESC LIMIT 600"""
    years = None
    if year is None:
        res = (await session.execute(text(f"""
            WITH d AS MATERIALIZED ({per_day.format(period="")}),
            sp AS ({species_sql}),
            yr AS (SELECT extract(year FROM dato)::int AS year, count(DISTINCT artnr) AS n_taxa FROM d GROUP BY 1)
            SELECT (SELECT json_agg(sp) FROM sp) AS species, (SELECT json_agg(yr ORDER BY year DESC) FROM yr) AS years
        """), {"l": loknr})).one()
        rows, years = res.species or [], res.years or []
    else:
        rows = (await session.execute(text(f"""
            WITH d AS ({per_day.format(period="AND dato >= make_date(CAST(:y AS int), 1, 1) AND dato < make_date(CAST(:y AS int) + 1, 1, 1)")})
            {species_sql}
        """), {"l": loknr, "y": year})).mappings().all()
        years = ((await get_json(species_key(loknr, None))) or {}).get("years")
        if years is None:
            years = [dict(r) for r in (await session.execute(text("""
                SELECT extract(year FROM dato)::int AS year, count(DISTINCT artnr) AS n_taxa
                FROM observations WHERE loknr = :l AND hemmelig = 0 AND antal > 0 GROUP BY 1 ORDER BY 1 DESC
            """), {"l": loknr})).mappings().all()]
    groups = await migration.species_groups(session)
    return {"species": [{**{k: r[k] for k in ("artnr", "navn", "sortering", "n_obs", "last_date", "rekord", "rekord_dato")},
                         "kategori": KAT[r["rank"]], "group": groups.get(r["artnr"])} for r in rows],
            "years": years}


async def prebuild_species(session: AsyncSession, n: int = TOP_N) -> None:
    """Artslisterne (alle år og i år) for de n mest aktive lokaliteter (seneste 30 dage, så året), til næste bygning."""
    this_year = dk_today().year
    top = (await session.execute(text("""
        SELECT loknr FROM location_year WHERE year = :y ORDER BY n_30 DESC, n_obs DESC LIMIT :n
    """), {"y": this_year, "n": n})).scalars().all()
    t0 = time.time()
    for i, loknr in enumerate(top):
        await jobstatus.step("Artslister for de mest aktive lokaliteter", i, len(top))
        for year in (None, this_year):
            await set_json(species_key(loknr, year), await species_list(session, loknr, year), EVERY + 3600)
    log.info("Artslister bygget for %s lokaliteter (%.0f s)", len(top), time.time() - t0)


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
