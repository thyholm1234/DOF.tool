"""Statistik-modulet (#/statistik): NFL – nye fuglearter for landet.

En art er ny for landet i det år, hvor den første gang optræder i Arter pr. år for Danmark (region_year_species,
region 'dk'). Dermed gælder samme regler: kun krydsbare fund (kategori A–C), underarter tæller som arten, udelukkede arter
og antal 0 tæller ikke. Det første fund vises med dato og lokalitet (lokaliteten skjules ved hemmelige fund).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.timeutil import dk_today

NFL_FROM = 1990


async def nfl(session: AsyncSession, first_year: int = NFL_FROM) -> dict:
    """Nye arter for landet pr. år fra first_year til i år: antal pr. år (også år uden nye) og arterne med første fund."""

    async def produce():
        rows = (await session.execute(text("""
            SELECT f.artnr, s.navn, s.latin, s.klass, s.sortering, f.first_date, f.n_years, f.n_obs,
                   ob.antal, CASE WHEN ob.hemmelig = 0 THEN ob.loknr END AS loknr,
                   CASE WHEN ob.hemmelig = 0 THEN l.navn END AS lokalitet
            FROM (SELECT artnr, min(first_date) AS first_date, count(*)::int AS n_years, sum(n_obs)::int AS n_obs
                  FROM region_year_species WHERE region = 'dk' GROUP BY artnr HAVING min(year) >= :y) f
            JOIN species s ON s.artnr = f.artnr
            LEFT JOIN LATERAL (
                SELECT o.loknr, o.antal, o.hemmelig FROM observations o JOIN species os ON os.artnr = o.artnr
                WHERE os.list_artnr = f.artnr AND o.dato = f.first_date AND o.antal > 0
                ORDER BY o.hemmelig, o.obsid LIMIT 1
            ) ob ON true
            LEFT JOIN locations l ON l.loknr = ob.loknr
            ORDER BY f.first_date, s.sortering NULLS LAST
        """), {"y": first_year})).mappings().all()
        species = [{**dict(r), "year": r["first_date"].year, "first_date": r["first_date"].isoformat()} for r in rows]
        counts = {y: 0 for y in range(first_year, dk_today().year + 1)}
        for s in species:
            counts[s["year"]] = counts.get(s["year"], 0) + 1
        return {"from": first_year, "years": [{"year": y, "n": n} for y, n in sorted(counts.items())], "species": species}

    return await cached(f"stats:nfl:{first_year}:{await version('regions')}", 86400, produce)
