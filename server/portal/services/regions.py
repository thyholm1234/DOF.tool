"""Arter pr. år for hele landet, lokalafdelinger og kommuner.

Forudberegnes i `region_year_species` (én række pr. region, år og listeart) med samme artsregler som
årslisterne: underarter tæller som nominatarten, udelukkede arter og antal 0 tæller ikke.
Natligt genberegnes de seneste år; efter arkivimport fra det tidligste importerede år.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump, cached, version
from portal.timeutil import dk_today

log = logging.getLogger("portal.regions")

_BASE = """
    FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
    {join}
    WHERE o.dato >= :a AND o.dato < :b AND o.dato <= current_date AND o.antal > 0 AND NOT ls.excluded
"""
_LEVELS = {
    "dk": ("'dk'", "", ""),
    "afd": ("'afd:' || a", ", unnest(o.afdelinger) a", ""),
    "kom": ("'kom:' || l.kommune_kode", "JOIN locations l ON l.loknr = o.loknr", "AND l.kommune_kode IS NOT NULL"),
}


async def refresh(session: AsyncSession, first_year: int | None = None) -> int:
    """Genberegn fra first_year (None = alle år) til i år. Returnerer antal år."""
    last = dk_today().year
    if first_year is None:
        first_year = (await session.execute(text("SELECT extract(year FROM min(dato))::int FROM observations"))).scalar() or last
    years = list(range(first_year, last + 1))
    for i, y in enumerate(years):
        await jobstatus.step(f"Arter pr. år: {y}", i, len(years))
        p = {"a": date(y, 1, 1), "b": date(y + 1, 1, 1), "y": y}
        await session.execute(text("DELETE FROM region_year_species WHERE year = :y"), {"y": y})
        for key, join, extra in _LEVELS.values():
            await session.execute(text(f"""
                INSERT INTO region_year_species (region, year, artnr, n_obs, first_date, last_date)
                SELECT {key}, :y, ls.artnr, count(*), min(o.dato), max(o.dato) {_BASE.format(join=join)} {extra}
                GROUP BY 1, ls.artnr
            """), p)
        await session.commit()
    await bump("regions")
    log.info("Arter pr. år genberegnet for %s–%s", first_year, last)
    return len(years)


async def ensure(session: AsyncSession, since_year: int | None = None) -> None:
    """Fuld beregning første gang (tom tabel), ellers fra since_year (højst de to seneste år)."""
    empty = not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM region_year_species)"))).scalar()
    last = dk_today().year - 1
    await refresh(session, None if empty else min(since_year or last, last))


async def _names(session: AsyncSession) -> dict[str, str]:
    afd = (await session.execute(text("SELECT id, navn FROM afdelinger"))).all()
    kom = (await session.execute(text("SELECT kode, navn FROM kommuner"))).all()
    return {"dk": "Danmark", **{f"afd:{i}": n for i, n in afd}, **{f"kom:{k}": n for k, n in kom}}


async def overview(session: AsyncSession, level: str) -> dict:
    """Antal arter pr. region og år for et niveau (dk / afd / kom)."""
    if level not in _LEVELS:
        raise ValueError(level)

    async def produce():
        rows = (await session.execute(text("""
            SELECT region, year, count(*) FROM region_year_species
            WHERE CASE WHEN :lv = 'dk' THEN region = 'dk' ELSE region LIKE :lv || ':%' END
            GROUP BY region, year
        """), {"lv": level})).all()
        total = (await session.execute(text("""
            SELECT region, count(DISTINCT artnr) FROM region_year_species
            WHERE CASE WHEN :lv = 'dk' THEN region = 'dk' ELSE region LIKE :lv || ':%' END GROUP BY region
        """), {"lv": level})).all()
        names = await _names(session)
        regions: dict[str, dict] = {}
        for region, y, n in rows:
            regions.setdefault(region, {"key": region, "label": names.get(region, region), "years": {}})["years"][y] = n
        for region, n in total:
            if region in regions:
                regions[region]["total"] = n
        return {"years": sorted({y for _, y, _ in rows}, reverse=True),
                "regions": sorted(regions.values(), key=lambda r: r["label"])}

    return await cached(f"regions:ov:{level}:{await version('regions')}", 86400, produce)


async def detail(session: AsyncSession, region: str, year: int | None) -> dict:
    """Artsliste for en region i et år (None = alle år samlet) + antal arter pr. år til grafen."""

    async def produce():
        per_year = (await session.execute(text("""
            SELECT year, count(*) FROM region_year_species WHERE region = :r GROUP BY year ORDER BY year
        """), {"r": region})).all()
        if year is None:
            return await _all_years(session, region, per_year)
        rows = (await session.execute(text("""
            SELECT r.artnr, s.navn, s.klass, s.sortering, r.n_obs, r.first_date, r.last_date,
                   (SELECT max(p.year) FROM region_year_species p
                    WHERE p.region = r.region AND p.artnr = r.artnr AND p.year < r.year) AS prev_year
            FROM region_year_species r JOIN species s ON s.artnr = r.artnr
            WHERE r.region = :r AND r.year = :y ORDER BY s.sortering NULLS LAST, s.navn
        """), {"r": region, "y": year})).mappings().all()
        # Arter set året før, men ikke i år
        missing = (await session.execute(text("""
            SELECT p.artnr, s.navn, s.klass, p.n_obs FROM region_year_species p JOIN species s ON s.artnr = p.artnr
            WHERE p.region = :r AND p.year = :y - 1
              AND NOT EXISTS (SELECT 1 FROM region_year_species c WHERE c.region = :r AND c.year = :y AND c.artnr = p.artnr)
            ORDER BY s.sortering NULLS LAST, s.navn
        """), {"r": region, "y": year})).mappings().all()
        names = await _names(session)
        return {
            "region": region, "label": names.get(region, region), "year": year,
            "per_year": [{"year": y, "n": n} for y, n in per_year],
            "species": [{**dict(r), "first_date": r["first_date"].isoformat(), "last_date": r["last_date"].isoformat(),
                         "new": r["prev_year"] is None} for r in rows],
            "missing": [dict(m) for m in missing],
        }

    return await cached(f"regions:d:{region}:{year or 'alle'}:{await version('regions')}", 86400, produce)


async def _all_years(session: AsyncSession, region: str, per_year: list) -> dict:
    """Samlet liste over alle år: hver art med antal år, første/sidste fund og observationer i alt."""
    rows = (await session.execute(text("""
        SELECT r.artnr, s.navn, s.klass, s.sortering, sum(r.n_obs)::int AS n_obs, min(r.first_date) AS first_date,
               max(r.last_date) AS last_date, count(*)::int AS n_years, max(r.year) AS last_year
        FROM region_year_species r JOIN species s ON s.artnr = r.artnr
        WHERE r.region = :r GROUP BY r.artnr, s.navn, s.klass, s.sortering ORDER BY s.sortering NULLS LAST, s.navn
    """), {"r": region})).mappings().all()
    names = await _names(session)
    return {
        "region": region, "label": names.get(region, region), "year": None,
        "per_year": [{"year": y, "n": n} for y, n in per_year],
        "species": [{**dict(r), "first_date": r["first_date"].isoformat(), "last_date": r["last_date"].isoformat(), "new": False}
                    for r in rows],
        "missing": [],
    }
