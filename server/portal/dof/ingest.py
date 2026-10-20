"""Indlæsning af observationer i PostgreSQL (bulk via COPY til midlertidige tabeller).

Bruges af både live-hentning, månedshentning og arkivimport, så alle moduler deler samme data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.dof.classify import get_classifier
from portal.dof.parse import Row


@dataclass
class IngestResult:
    rows: int = 0
    new_obsids: list[int] = field(default_factory=list)
    changed: list[tuple[int, int, int]] = field(default_factory=list)  # obsid, gammelt antal, nyt antal
    deleted: int = 0
    deleted_obsids: list[int] = field(default_factory=list)
    unknown_species: set[int] = field(default_factory=set)
    observers: set[str] = field(default_factory=set)


async def _apg(session: AsyncSession):
    """Rå asyncpg-forbindelse INDEN FOR sessionens transaktion.

    SQLAlchemy starter først transaktionen ved første statement; uden dette ville de rå kald køre
    i autocommit, og midlertidige tabeller (ON COMMIT DELETE ROWS) ville tømmes mellem kaldene.
    """
    await session.execute(text("SELECT 1"))
    conn = await session.connection()
    raw = await conn.get_raw_connection()
    return raw.driver_connection


async def ingest(
    session: AsyncSession,
    rows: list[Row],
    *,
    mode: str = "live",
    replace_dates: tuple[date, date] | None = None,
    track_changes: bool = False,
) -> IngestResult:
    """Upsert rækker.

    mode='archive' indsætter kun nye observationer (eksisterende bevares) og sætter first_seen til
    observationsdatoen, så arkivdata aldrig opfattes som 'nye' fund.
    replace_dates=(fra, til) sletter observationer i perioden, som ikke længere findes hos DOFbasen.
    """
    result = IngestResult(rows=len(rows))
    if not rows:
        if replace_dates:
            result.deleted_obsids = await _delete_missing(session, replace_dates, [])
            result.deleted = len(result.deleted_obsids)
        return result

    clf = await get_classifier(session)
    now = datetime.now(timezone.utc)

    observers: dict[str, tuple] = {}
    trips: dict[int, tuple] = {}
    locations: dict[int, tuple] = {}
    codes: dict[tuple[str, str], str] = {}
    species: dict[int, tuple] = {}
    sorting: dict[int, int] = {}
    obs_records: list[tuple] = []

    for r in rows:
        afd_ids = [a for a in (clf.afdeling_id(n) for n in r.afdelinger) if a is not None]
        if r.obserkode:
            observers[r.obserkode] = (r.obserkode, r.fornavn, r.efternavn, r.obser_by)
            result.observers.add(r.obserkode)
        if r.turid is not None:
            trips[r.turid] = (r.turid, r.dato, r.loknr, r.obserkode, r.turtidfra, r.turtidtil, r.turnoter, r.medobser)
        if r.loknr is not None and r.loknavn:
            locations[r.loknr] = (r.loknr, r.loknavn, afd_ids[0] if afd_ids else None, r.lok_lat, r.lok_lon)
        if r.adfkode and r.adftekst:
            codes[("adf", r.adfkode)] = r.adftekst
        if r.dragtkode and r.dragttekst:
            codes[("dragt", r.dragtkode)] = r.dragttekst
        if r.sortering is not None:
            sorting[r.artnr] = r.sortering
        if r.artnr not in clf.klass_by_artnr:
            species[r.artnr] = (r.artnr, r.artnavn or f"Art {r.artnr}", r.latin, r.sortering)
        kategori = clf.kategori(r.artnr, r.artnavn, r.dato, r.antal, afd_ids)
        first_seen = datetime.combine(r.dato, time(12), tzinfo=timezone.utc) if mode == "archive" else now
        obs_records.append((
            r.obsid, r.turid, r.dato, r.artnr, r.antal, r.koen, r.adfkode, r.alderkode, r.dragtkode,
            r.obstidfra, r.obstidtil, r.fuglnoter, r.metode, r.hemmelig, r.kvalitet, r.loknr,
            r.obserkode, afd_ids, r.obs_lat, r.obs_lon, kategori, first_seen, now,
        ))

    apg = await _apg(session)

    await apg.execute("""
        CREATE TEMP TABLE IF NOT EXISTS tmp_obs (LIKE observations INCLUDING DEFAULTS) ON COMMIT DELETE ROWS;
        CREATE TEMP TABLE IF NOT EXISTS tmp_trips (LIKE trips) ON COMMIT DELETE ROWS;
        CREATE TEMP TABLE IF NOT EXISTS tmp_observers (LIKE observers) ON COMMIT DELETE ROWS;
        CREATE TEMP TABLE IF NOT EXISTS tmp_locs (loknr int, navn text, afdeling_id smallint, lat float8, lon float8) ON COMMIT DELETE ROWS;
    """)

    await apg.copy_records_to_table("tmp_observers", records=list(observers.values()),
                                    columns=["obserkode", "fornavn", "efternavn", "by"])
    await apg.copy_records_to_table("tmp_trips", records=list(trips.values()),
                                    columns=["turid", "dato", "loknr", "obserkode", "turtidfra", "turtidtil", "turnoter", "medobser"])
    await apg.copy_records_to_table("tmp_locs", records=list(locations.values()),
                                    columns=["loknr", "navn", "afdeling_id", "lat", "lon"])
    await apg.copy_records_to_table("tmp_obs", records=obs_records, columns=[
        "obsid", "turid", "dato", "artnr", "antal", "koen", "adfkode", "alderkode", "dragtkode",
        "obstidfra", "obstidtil", "fuglnoter", "metode", "hemmelig", "kvalitet", "loknr",
        "obserkode", "afdelinger", "obs_lat", "obs_lon", "kategori", "first_seen", "updated_at",
    ])

    await apg.execute("""
        INSERT INTO observers SELECT * FROM tmp_observers
        ON CONFLICT (obserkode) DO UPDATE SET fornavn = EXCLUDED.fornavn, efternavn = EXCLUDED.efternavn, by = EXCLUDED.by
        WHERE (observers.fornavn, observers.efternavn, observers.by) IS DISTINCT FROM (EXCLUDED.fornavn, EXCLUDED.efternavn, EXCLUDED.by)
    """)
    await apg.execute("""
        INSERT INTO trips SELECT * FROM tmp_trips
        ON CONFLICT (turid) DO UPDATE SET dato = EXCLUDED.dato, loknr = EXCLUDED.loknr, obserkode = EXCLUDED.obserkode,
            turtidfra = EXCLUDED.turtidfra, turtidtil = EXCLUDED.turtidtil, turnoter = EXCLUDED.turnoter, medobser = EXCLUDED.medobser
        WHERE (trips.turnoter, trips.turtidfra, trips.turtidtil, trips.medobser, trips.loknr)
              IS DISTINCT FROM (EXCLUDED.turnoter, EXCLUDED.turtidfra, EXCLUDED.turtidtil, EXCLUDED.medobser, EXCLUDED.loknr)
    """)
    new_locs = await apg.fetch("""
        INSERT INTO locations (loknr, navn, afdeling_id, lat, lon, geom)
        SELECT loknr, navn, afdeling_id, lat, lon,
               CASE WHEN lat IS NOT NULL AND lon IS NOT NULL THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326) END
        FROM tmp_locs
        ON CONFLICT (loknr) DO UPDATE SET navn = EXCLUDED.navn, lat = EXCLUDED.lat, lon = EXCLUDED.lon, geom = EXCLUDED.geom,
            afdeling_id = COALESCE(EXCLUDED.afdeling_id, locations.afdeling_id),
            kommune_kode = CASE WHEN locations.lat IS DISTINCT FROM EXCLUDED.lat OR locations.lon IS DISTINCT FROM EXCLUDED.lon
                                THEN NULL ELSE locations.kommune_kode END
        WHERE (locations.navn, locations.lat, locations.lon) IS DISTINCT FROM (EXCLUDED.navn, EXCLUDED.lat, EXCLUDED.lon)
        RETURNING loknr
    """)
    if new_locs:
        await assign_kommuner(session, [r["loknr"] for r in new_locs])

    if codes:
        await apg.executemany(
            "INSERT INTO code_texts (kind, code, text) VALUES ($1, $2, $3) ON CONFLICT (kind, code) DO UPDATE SET text = EXCLUDED.text",
            [(k, c, t) for (k, c), t in codes.items()],
        )
    if sorting:
        await apg.execute(
            "UPDATE species s SET sortering = v.sortering FROM unnest($1::int[], $2::int[]) AS v(artnr, sortering) "
            "WHERE s.artnr = v.artnr AND s.sortering IS DISTINCT FROM v.sortering",
            list(sorting), list(sorting.values()),
        )
    if species:
        await apg.executemany(
            """INSERT INTO species (artnr, navn, latin, sortering, klass, art_type, list_artnr, excluded)
               VALUES ($1, $2, $3, $4, 'Alm', 'art', NULL, false) ON CONFLICT (artnr) DO NOTHING""",
            list(species.values()),
        )
        result.unknown_species = set(species)

    if track_changes:
        existing = await apg.fetch("SELECT o.obsid, o.antal FROM observations o JOIN tmp_obs t USING (obsid)")
        old = {r["obsid"]: r["antal"] for r in existing}
        for rec in obs_records:
            obsid, antal = rec[0], rec[4]
            if obsid not in old:
                result.new_obsids.append(obsid)
            elif old[obsid] != antal:
                result.changed.append((obsid, old[obsid], antal))

    if mode == "archive":
        await apg.execute("""
            INSERT INTO observations SELECT * FROM tmp_obs ON CONFLICT (obsid) DO NOTHING
        """)
    else:
        await apg.execute("""
            INSERT INTO observations SELECT * FROM tmp_obs
            ON CONFLICT (obsid) DO UPDATE SET
                turid = EXCLUDED.turid, dato = EXCLUDED.dato, artnr = EXCLUDED.artnr, antal = EXCLUDED.antal,
                koen = EXCLUDED.koen, adfkode = EXCLUDED.adfkode, alderkode = EXCLUDED.alderkode,
                dragtkode = EXCLUDED.dragtkode, obstidfra = EXCLUDED.obstidfra, obstidtil = EXCLUDED.obstidtil,
                fuglnoter = EXCLUDED.fuglnoter, metode = EXCLUDED.metode, hemmelig = EXCLUDED.hemmelig,
                kvalitet = EXCLUDED.kvalitet, loknr = EXCLUDED.loknr, obserkode = EXCLUDED.obserkode,
                afdelinger = EXCLUDED.afdelinger, obs_lat = EXCLUDED.obs_lat, obs_lon = EXCLUDED.obs_lon,
                kategori = EXCLUDED.kategori, updated_at = EXCLUDED.updated_at
            WHERE (observations.antal, observations.artnr, observations.adfkode, observations.fuglnoter,
                   observations.kategori, observations.loknr, observations.afdelinger, observations.obstidfra)
               IS DISTINCT FROM
                  (EXCLUDED.antal, EXCLUDED.artnr, EXCLUDED.adfkode, EXCLUDED.fuglnoter,
                   EXCLUDED.kategori, EXCLUDED.loknr, EXCLUDED.afdelinger, EXCLUDED.obstidfra)
        """)

    if replace_dates:
        result.deleted_obsids = await _delete_missing(session, replace_dates, [rec[0] for rec in obs_records])
        result.deleted = len(result.deleted_obsids)
    return result


async def _delete_missing(session: AsyncSession, period: tuple[date, date], keep: list[int]) -> list[int]:
    res = await session.execute(
        # Negative obsid'er er manuelle fund (manual_finds) – de står ikke i DOFbasen og skal blive
        text("DELETE FROM observations WHERE dato BETWEEN :a AND :b AND obsid > 0 AND NOT (obsid = ANY(:keep)) RETURNING obsid"),
        {"a": period[0], "b": period[1], "keep": keep},
    )
    return list(res.scalars().all())


async def assign_kommuner(session: AsyncSession, loknrs: list[int] | None = None) -> None:
    """Kommune for lokaliteter via punkt-i-polygon; kystnære punkter får nærmeste kommune (< ~3 km)."""
    where = "AND l.loknr = ANY(:ids)" if loknrs is not None else ""
    params = {"ids": loknrs} if loknrs is not None else {}
    await session.execute(text(f"""
        UPDATE locations l SET kommune_kode = k.kode FROM kommuner k
        WHERE l.kommune_kode IS NULL AND l.geom IS NOT NULL AND ST_Contains(k.geom, l.geom) {where}
    """), params)
    await session.execute(text(f"""
        UPDATE locations l SET kommune_kode = (
            SELECT k.kode FROM kommuner k WHERE ST_DWithin(k.geom, l.geom, 0.04) ORDER BY k.geom <-> l.geom LIMIT 1)
        WHERE l.kommune_kode IS NULL AND l.geom IS NOT NULL {where}
    """), params)
    # 10 km-kvadrat i Det Danske Kvadratnet (nøgle til vejrarkivet)
    await session.execute(text(f"""
        UPDATE locations l SET cell10 = floor(ST_Y(ST_Transform(l.geom, 25832)) / 10000)::int * 1000
                                        + floor(ST_X(ST_Transform(l.geom, 25832)) / 10000)::int
        WHERE l.cell10 IS NULL AND l.geom IS NOT NULL {where}
    """), params)
