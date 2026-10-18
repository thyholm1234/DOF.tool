"""Live-feed: observationer samlet i 'tråde' (art + lokalitet + dag)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.services.areas import Scope

KAT_RANK = "CASE o.kategori WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 WHEN 'bemaerk' THEN 1 ELSE 0 END"
LEVELS = {"SU": 3, "SUB": 2, "bemaerk": 1, "all": 0, "local": 0}


async def threads(session: AsyncSession, day: date, *, scope: Scope, level: str = "bemaerk", local_max: int = 10,
                  limit: int = 400) -> list[dict]:
    """Dagens tråde. level: SU / SUB / bemaerk / local (lokale hits: < local_max obs historisk i området) / all."""
    key = f"feed:{day}:{scope.key}:{level}:{local_max}:{await version('feed')}"
    return await cached(key, 120, lambda: _threads(session, day, scope, level, local_max, limit))


async def _threads(session: AsyncSession, day: date, scope: Scope, level: str, local_max: int, limit: int) -> list[dict]:
    pred, params = scope.predicate("o")
    min_rank = LEVELS.get(level, 1)
    local_join = ""
    local_cond = ""
    if level == "local":
        count_key = scope.key
        local_join = "LEFT JOIN area_species_counts c ON c.area_key = :ck AND c.artnr = o.artnr"
        local_cond = ("AND ((coalesce(c.n_obs, 0) < :lmax AND s.art_type IN ('art', 'underart')) "
                      "OR o.kategori IN ('SU','SUB'))")
        params.update({"ck": count_key, "lmax": local_max})
    kat_filter = f"AND {KAT_RANK} >= :minrank" if level not in ("local", "all") else ""
    sql = f"""
        WITH sp AS (SELECT artnr, navn, klass, coalesce(list_artnr, artnr) AS list_or_artnr, art_type FROM species),
        obs AS (
            SELECT o.*, {KAT_RANK} AS rank, s.navn AS art, s.klass, s.art_type
            FROM observations o JOIN sp s ON s.artnr = o.artnr {local_join}
            WHERE o.dato = :day AND o.hemmelig = 0 AND {pred} {kat_filter} {local_cond}
        ),
        per_trip AS (
            SELECT artnr, loknr, turid, sum(antal) AS n FROM obs GROUP BY artnr, loknr, turid
        ),
        agg AS (
            SELECT artnr, loknr, max(art) AS art, max(klass) AS klass, max(rank) AS rank,
                   count(*) AS n_obs, count(DISTINCT obserkode) AS n_observers,
                   min(first_seen) AS first_seen, max(first_seen) AS last_seen,
                   (array_agg(obsid ORDER BY coalesce(obstidfra, '00:00') DESC, first_seen DESC))[1] AS last_obsid
            FROM obs GROUP BY artnr, loknr
        )
        SELECT a.*, (SELECT max(n) FROM per_trip p WHERE p.artnr = a.artnr AND p.loknr IS NOT DISTINCT FROM a.loknr) AS max_antal,
               l.navn AS lok, l.lat, l.lon, l.kommune_kode, k.navn AS kommune,
               lo.antal AS last_antal, lo.adfkode, ct.text AS adf, lo.obstidfra, t.turtidfra,
               trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS last_observer,
               lo.obserkode AS last_obserkode, lo.afdelinger
        FROM agg a
        LEFT JOIN locations l ON l.loknr = a.loknr
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        JOIN observations lo ON lo.obsid = a.last_obsid
        LEFT JOIN trips t ON t.turid = lo.turid
        LEFT JOIN observers ob ON ob.obserkode = lo.obserkode
        LEFT JOIN code_texts ct ON ct.kind = 'adf' AND ct.code = lo.adfkode
        ORDER BY a.last_seen DESC, a.rank DESC
        LIMIT :limit
    """
    rows = (await session.execute(text(sql), {"day": day, "minrank": min_rank, "limit": limit, **params})).mappings().all()
    out = []
    for r in rows:
        kat = {3: "SU", 2: "SUB", 1: "bemaerk"}.get(r["rank"], "alm")
        out.append({
            "id": f"{day.isoformat()}/{r['artnr']}/{r['loknr']}",
            "dato": day.isoformat(), "artnr": r["artnr"], "loknr": r["loknr"], "art": r["art"], "lok": r["lok"],
            "kommune": r["kommune"], "lat": r["lat"], "lon": r["lon"], "kategori": kat,
            "max_antal": int(r["max_antal"] or 0), "n_obs": r["n_obs"], "n_observers": r["n_observers"],
            "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat(),
            "last": {"obsid": r["last_obsid"], "antal": r["last_antal"], "adf": r["adf"] or r["adfkode"],
                     "tid": r["obstidfra"] or r["turtidfra"], "observer": r["last_observer"],
                     "obserkode": r["last_obserkode"]},
            "afdelinger": list(r["afdelinger"] or []),
        })
    return out


async def thread_detail(session: AsyncSession, day: date, artnr: int, loknr: int) -> dict | None:
    rows = (await session.execute(text("""
        SELECT o.obsid, o.turid, o.antal, o.koen, o.adfkode, ca.text AS adf, o.alderkode, o.dragtkode, cd.text AS dragt,
               o.obstidfra, o.obstidtil, t.turtidfra, t.turtidtil, o.fuglnoter, t.turnoter, o.kategori, o.first_seen,
               o.obserkode, trim(coalesce(ob.fornavn,'') || ' ' || coalesce(ob.efternavn,'')) AS observer, t.medobser,
               o.obs_lat, o.obs_lon, o.hemmelig
        FROM observations o
        LEFT JOIN trips t ON t.turid = o.turid
        LEFT JOIN observers ob ON ob.obserkode = o.obserkode
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = o.adfkode
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = o.dragtkode
        WHERE o.dato = :d AND o.artnr = :a AND o.loknr = :l AND o.hemmelig = 0
        ORDER BY coalesce(o.obstidfra, t.turtidfra, '99:99'), o.first_seen
    """), {"d": day, "a": artnr, "l": loknr})).mappings().all()
    if not rows:
        return None
    meta = (await session.execute(text("""
        SELECT s.navn AS art, s.latin, s.klass, l.navn AS lok, l.lat, l.lon, k.navn AS kommune
        FROM species s, locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE s.artnr = :a AND l.loknr = :l
    """), {"a": artnr, "l": loknr})).mappings().first() or {}
    return {
        "dato": day.isoformat(), "artnr": artnr, "loknr": loknr, **dict(meta),
        "observations": [{**dict(r), "first_seen": r["first_seen"].isoformat()} for r in rows],
    }


async def day_summary(session: AsyncSession, day: date) -> dict:
    async def produce():
        r = (await session.execute(text("""
            SELECT count(*) AS n_obs, count(DISTINCT turid) AS n_trips, count(DISTINCT obserkode) AS n_observers,
                   count(DISTINCT artnr) AS n_species,
                   count(*) FILTER (WHERE kategori = 'SU') AS n_su, count(*) FILTER (WHERE kategori = 'SUB') AS n_sub,
                   count(*) FILTER (WHERE kategori = 'bemaerk') AS n_bemaerk
            FROM observations WHERE dato = :d
        """), {"d": day})).mappings().first()
        return dict(r)
    return await cached(f"daysum:{day}:{await version('feed')}", 120, produce)


async def migration_today(session: AsyncSession, day: date, limit: int = 12) -> list[dict]:
    """Dagens største træklokaliteter (sum af trækkende individer)."""
    async def produce():
        rows = (await session.execute(text("""
            SELECT o.loknr, l.navn AS lok, sum(o.antal) AS n, count(DISTINCT o.artnr) AS n_species,
                   (array_agg(s.navn ORDER BY o.antal DESC))[1:3] AS top
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato = :d AND o.hemmelig = 0 AND split_part(coalesce(o.adfkode,''), ' ', 1)
                  IN ('T','TF','N','NØ','Ø','SØ','S','SV','V','NV')
            GROUP BY o.loknr, l.navn ORDER BY n DESC LIMIT :n
        """), {"d": day, "n": limit})).mappings().all()
        return [dict(r) for r in rows]
    return await cached(f"migr:{day}:{await version('feed')}", 300, produce)
