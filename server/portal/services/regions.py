"""Arter pr. år for hele landet, lokalafdelinger og kommuner.

Forudberegnes i `region_year_species` (én række pr. region, år og listeart) med samme artsregler som
årslisterne: kun krydsbare observationer (kategori A–C), underarter tæller som nominatarten, udelukkede arter og antal 0 tæller ikke.
Natligt genberegnes de seneste år; efter arkivimport fra det tidligste importerede år.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump, cached, version
from portal.services.categories import countable_sql
from portal.timeutil import dk_today

log = logging.getLogger("portal.regions")

_BASE = """
    FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
    {join}
    WHERE o.dato >= :a AND o.dato < :b AND o.dato <= current_date AND o.antal > 0 AND NOT ls.excluded
      AND """ + countable_sql()
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


async def refresh_reach(session: AsyncSession, first_year: int | None = None) -> int:
    """Observatører og ture pr. region, år og art (+ år 0 = alle år) til 'Huller'. Ture tæller kun med turid.
    Genberegner fra first_year (None = alle år) til i år. Returnerer antal beregnede perioder."""
    last = dk_today().year
    if first_year is None:
        first_year = (await session.execute(text("SELECT extract(year FROM min(dato))::int FROM observations"))).scalar() or last
    years = list(range(first_year, last + 1))
    for i, y in enumerate(years):
        await jobstatus.step(f"Huller: {y}", i, len(years) + 1)
        p = {"y": y, "a": date(y, 1, 1), "b": date(y + 1, 1, 1)}
        await session.execute(text("DELETE FROM region_reach WHERE year = :y"), p)
        await session.execute(text("DELETE FROM region_totals WHERE year = :y"), p)
        for key, join, extra in _LEVELS.values():
            base = _BASE.format(join=join)
            await session.execute(text(f"""
                INSERT INTO region_reach (region, year, artnr, n_observers, n_trips)
                SELECT {key}, :y, ls.artnr, count(DISTINCT o.obserkode), count(DISTINCT o.turid) {base} {extra}
                GROUP BY 1, ls.artnr
            """), p)
            await session.execute(text(f"""
                INSERT INTO region_totals (region, year, n_trips, n_observers)
                SELECT {key}, :y, count(DISTINCT o.turid), count(DISTINCT o.obserkode) {base} {extra}
                GROUP BY 1
            """), p)
        await session.commit()
    # Alle år (year = 0) lægges sammen fra årene uden at røre observationerne: ture ligger på én dato, og observatører
    # tælles som observatør-år – så andelen er et vægtet gennemsnit af årsandelene (vægt = aktive observatører i året).
    await jobstatus.step("Huller: alle år", len(years), len(years) + 1)
    await session.execute(text("DELETE FROM region_reach WHERE year = 0"))
    await session.execute(text("DELETE FROM region_totals WHERE year = 0"))
    await session.execute(text("""
        INSERT INTO region_reach (region, year, artnr, n_observers, n_trips)
        SELECT region, 0, artnr, sum(n_observers), sum(n_trips) FROM region_reach WHERE year > 0 GROUP BY region, artnr
    """))
    await session.execute(text("""
        INSERT INTO region_totals (region, year, n_trips, n_observers)
        SELECT region, 0, sum(n_trips), sum(n_observers) FROM region_totals WHERE year > 0 GROUP BY region
    """))
    await session.commit()
    await bump("regions")
    log.info("Huller genberegnet for %s–%s", first_year, last)
    return len(years) + 1


async def ensure_reach(session: AsyncSession, since_year: int | None = None) -> int:
    """Fuld beregning første gang (tom tabel), ellers fra since_year (højst det forrige år)."""
    # Alle år (year = 0) skrives sidst: findes de ikke, er en tidligere bygning afbrudt og alt bygges forfra
    empty = not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM region_reach WHERE year = 0)"))).scalar()
    return await refresh_reach(session, None if empty else min(since_year or dk_today().year - 1, dk_today().year - 1))


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



async def gaps(session: AsyncSession, scope, year: int | None, mine: set[int]) -> dict:
    """Arter andre har i området/året, men som ikke er på `mine` (listeartnumre). Begge mål beregnes på én gang.

    dk / afdelinger / kommuner læses fra de forudberegnede tabeller; egne områder tælles direkte og gemmes 6 timer."""
    region = scope.key
    y = year or 0

    async def produce():
        if scope.kind in ("dk", "afdeling", "kommune"):
            rows = (await session.execute(text("""
                SELECT r.artnr, r.n_observers, r.n_trips FROM region_reach r WHERE r.region = :r AND r.year = :y
            """), {"r": region, "y": y})).all()
            tot = (await session.execute(text(
                "SELECT n_trips, n_observers FROM region_totals WHERE region = :r AND year = :y"), {"r": region, "y": y})).first()
        else:
            pred, params = scope.predicate("o")
            base = (_BASE.format(join="") + f" AND {pred}").replace(":a", ":ya").replace(":b", ":yb")
            params.update({"ya": date(y or 1900, 1, 1), "yb": date(y + 1 if y else 3000, 1, 1)})
            rows = (await session.execute(text(f"""
                SELECT ls.artnr, count(DISTINCT o.obserkode), count(DISTINCT o.turid) {base} GROUP BY ls.artnr
            """), params)).all()
            tot = (await session.execute(text(f"SELECT count(DISTINCT o.turid), count(DISTINCT o.obserkode) {base}"), params)).first()
        return {"rows": [list(r) for r in rows], "trips": tot[0] if tot else 0, "observers": tot[1] if tot else 0}

    data = await cached(f"gaps:{region}:{y}:{await version('regions')}", 6 * 3600, produce)
    missing = [(a, o, t) for a, o, t in data["rows"] if a not in mine]
    names = {r[0]: r for r in (await session.execute(
        text("SELECT artnr, navn, klass, sortering FROM species WHERE artnr = ANY(:a)"), {"a": [m[0] for m in missing]})).all()}
    # Senest set i området (fra Arter pr. år) – kun for hullerne
    last = {}
    if scope.kind in ("dk", "afdeling", "kommune") and missing:
        last = {r[0]: r[1] for r in (await session.execute(text("""
            SELECT artnr, max(last_date) FROM region_year_species WHERE region = :r AND artnr = ANY(:a) GROUP BY artnr
        """), {"r": region, "a": [m[0] for m in missing]})).all()}
    trips, obs = max(data["trips"], 1), max(data["observers"], 1)
    items = [{"artnr": a, "art": names[a][1], "klass": names[a][2], "n_observers": o, "n_trips": t,
              "share": t / trips, "obs_share": o / obs, "last": last[a].isoformat() if a in last else None}
             for a, o, t in missing if a in names]
    return {"scope": region, "year": year, "observers": data["observers"], "trips": data["trips"],
            "n_gaps": len(items), "items": items}
