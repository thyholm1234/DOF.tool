"""Arter: søgning, artsindekset (årets tal) og artssiden (seneste fund, fordeling på afdelinger, estimerede fund)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.models import Species
from portal.services import finds
from portal.timeutil import dk_today


async def search(session: AsyncSession, q: str, limit: int) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT artnr, navn, latin, klass, art_type FROM species
        WHERE (:q = '' OR navn ILIKE :like OR latin ILIKE :like) AND NOT excluded
        ORDER BY (navn ILIKE :p) DESC, art_type <> 'art', navn LIMIT :n
    """), {"q": q, "like": f"%{q}%", "p": f"{q}%", "n": limit})).mappings().all()
    return [dict(r) for r in rows]


async def index(session: AsyncSession) -> list[dict]:
    """Artsindeks: alle taxa med årets tal (dagsaggregater + dagens observationer)."""
    async def produce():
        today = dk_today()
        rows = (await session.execute(text("""
            WITH yr AS (
                SELECT artnr, sum(n_obs) AS n_year, min(dato) AS first_year, max(dato) AS last_year,
                       sum(n_obs) FILTER (WHERE dato > :d30) AS n_30
                FROM species_day WHERE dato >= :y0 AND dato < :today GROUP BY artnr),
            td AS (SELECT artnr, count(*) AS n_today FROM observations WHERE dato = :today GROUP BY artnr),
            ever AS (SELECT artnr, max(dato) AS last_date FROM species_day GROUP BY artnr)
            SELECT s.artnr, s.navn, s.latin, s.klass, s.art_type, s.sortering,
                   coalesce(yr.n_year, 0) + coalesce(td.n_today, 0) AS n_year, coalesce(yr.n_30, 0) + coalesce(td.n_today, 0) AS n_30,
                   coalesce(td.n_today, 0) AS n_today, CASE WHEN td.n_today > 0 THEN :today ELSE greatest(yr.last_year, ever.last_date) END AS last_date,
                   CASE WHEN yr.first_year IS NOT NULL THEN yr.first_year WHEN td.n_today > 0 THEN :today END AS first_year
            FROM species s LEFT JOIN yr USING (artnr) LEFT JOIN td USING (artnr) LEFT JOIN ever USING (artnr)
            WHERE NOT s.excluded
        """), {"y0": date(today.year, 1, 1), "today": today, "d30": today - timedelta(days=30)})).mappings().all()
        return [{**dict(r), "last_date": r["last_date"].isoformat() if r["last_date"] else None,
                 "first_year": r["first_year"].isoformat() if r["first_year"] else None} for r in rows]

    return await cached(f"species:index:{await version('feed')}", 900, produce)


async def detail(session: AsyncSession, artnr: int) -> dict | None:
    """Artssiden: seneste 30 dages fund, observationer pr. afdeling og i hele DK, og estimerede fund (SU/SUB)."""
    sp = await session.get(Species, artnr)
    if sp is None:
        return None

    async def produce():
        recent = (await session.execute(text("""
            SELECT o.dato, o.loknr, l.navn AS lok, l.lat, l.lon, max(o.antal) AS antal, max(o.kategori) AS kategori
            FROM observations o JOIN locations l ON l.loknr = o.loknr
            WHERE o.artnr = :a AND o.hemmelig = 0 AND o.dato > current_date - 30
            GROUP BY o.dato, o.loknr, l.navn, l.lat, l.lon ORDER BY o.dato DESC LIMIT 500
        """), {"a": artnr})).mappings().all()
        counts = (await session.execute(text("""
            SELECT a.navn, c.n_obs FROM area_species_counts c JOIN afdelinger a ON 'afd:' || a.id = c.area_key
            WHERE c.artnr = :a ORDER BY c.n_obs DESC
        """), {"a": artnr})).mappings().all()
        total = (await session.execute(text("SELECT n_obs, n_days, last_date FROM area_species_counts WHERE area_key='dk' AND artnr=:a"),
                                       {"a": artnr})).mappings().first()
        return {"recent": [dict(r) for r in recent], "by_afdeling": [dict(r) for r in counts],
                "total": dict(total) if total else None}

    data = await cached(f"sp:{artnr}", 900, produce)
    return {"artnr": sp.artnr, "navn": sp.navn, "latin": sp.latin, "klass": sp.klass, "art_type": sp.art_type, **data,
            "finds": finds.public(await finds.species_finds(session, artnr))}


async def list_artnr(session: AsyncSession, artnr: int) -> int | None:
    """Listearten (arten selv eller den art, en underart/form tælles under), None hvis arten ikke findes."""
    return (await session.execute(text("SELECT list_artnr FROM species WHERE artnr = :a"), {"a": artnr})).scalar()
