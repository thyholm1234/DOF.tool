"""Skema, referencedata-sync og flytning af data mellem databasegrupper."""

from __future__ import annotations

import asyncio
import logging
import tempfile

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from portal import models
from portal.db import Base
from portal.dbgroups import GROUPS, OBS_TABLES, OBSERVATIONS, REFERENCE_TABLES, USER_TABLES, USERS, group_of_table, tables_in
from portal.dbrouter import router

log = logging.getLogger("portal.dbops")

# Rækkefølge hvor referencetabeller kan kopieres uden at ramme fremmednøgler
REFERENCE_ORDER = ("afdelinger", "kommuner", "locations", "species", "bemaerk_thresholds", "phenology_windows", "code_texts")


def tables_for(group: str) -> set[str]:
    return REFERENCE_TABLES | (OBS_TABLES if group == OBSERVATIONS else USER_TABLES)


def ddl_groups(ddl: str) -> tuple[str, ...]:
    """Hvilke grupper en EXTRA_DDL-sætning skal køres i (kun referencetabeller → begge)."""
    groups = {g for t in tables_in(ddl) if (g := group_of_table(t))}
    if len(groups) > 1:
        raise ValueError("DDL blander bruger- og observationstabeller: " + ddl)
    return tuple(groups) or GROUPS


async def create_schema() -> None:
    """Opret/ret tabeller på hver gruppes primary (idempotent; advisory lock mod samtidige processer)."""
    for group in GROUPS:
        tables = [t for t in Base.metadata.sorted_tables if t.name in tables_for(group)]
        async with router.primary(group).begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
            for ddl in models.EXTRA_DDL:
                if group in ddl_groups(ddl):
                    await conn.execute(text(ddl))


# ---------------------------------------------------------------- kopiering

async def _columns(conn, table: str) -> list[str]:
    rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                            "AND table_name = $1 ORDER BY ordinal_position", table)
    return [r["column_name"] for r in rows]


async def _fingerprint(conn, table: str) -> str:
    return await conn.fetchval(f"SELECT count(*)::text || ':' || coalesce(md5(string_agg(r, '' ORDER BY r)), '') "
                               f"FROM (SELECT t::text AS r FROM {table} t) q")


async def _copy(src, dst, table: str, *, delete: bool, where: str = "") -> int:
    """Kopiér en tabel (eller et udsnit, `where`) fra src til dst (asyncpg-forbindelser) via CSV i en midlertidig fil."""
    cols = await _columns(src, table)
    dcols = set(await _columns(dst, table))
    cols = [c for c in cols if c in dcols]
    quoted = ", ".join(f'"{c}"' for c in cols)
    with tempfile.TemporaryFile() as buf:
        await src.copy_from_query(f"SELECT {quoted} FROM {table} {where}", output=buf, format="csv")
        buf.seek(0)
        async with dst.transaction():
            if delete:
                await dst.execute(f"DELETE FROM {table}")
            result = await dst.copy_to_table(table, source=buf, columns=cols, format="csv")
    return int(result.split()[-1])


BIG_ROWS = 300_000  # tabeller over denne størrelse kopieres uden sekundære indekser og (hvis muligt) i parallelle udsnit
PARALLEL = 4


async def _int_pk(conn, table: str) -> str | None:
    """Primærnøglekolonnen, hvis den er ét heltal (så tabellen kan deles i intervaller)."""
    rows = await conn.fetch("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS typ FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = $1::regclass AND i.indisprimary""", table)
    return rows[0]["attname"] if len(rows) == 1 and rows[0]["typ"] in ("integer", "bigint", "smallint") else None


async def _secondary_indexes(conn, table: str) -> list[tuple[str, str]]:
    """(navn, definition) for ikke-unikke indekser – dem bygges bedre til sidst end opdateres række for række."""
    rows = await conn.fetch("""
        SELECT i.relname, pg_get_indexdef(i.oid) AS def FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
        WHERE x.indrelid = $1::regclass AND NOT x.indisprimary AND NOT x.indisunique""", table)
    return [(r["relname"], r["def"]) for r in rows]


async def _copy_big(legacy, dst_engine, src, dst, table: str, total: int, say) -> int:
    """Stor tabel: slip sekundære indekser, kopiér i parallelle intervaller på primærnøglen, byg indekserne parallelt igen."""
    indexes = await _secondary_indexes(dst, table)
    for name, _ in indexes:
        await dst.execute(f'DROP INDEX IF EXISTS "{name}"')
    pk = await _int_pk(src, table)
    await dst.execute(f"DELETE FROM {table}")
    ranges: list[str] = [""]
    if pk:
        lo, hi = await src.fetchrow(f'SELECT min("{pk}"), max("{pk}") FROM {table}')
        if lo is not None and hi > lo:
            step = (hi - lo) // PARALLEL + 1
            ranges = [f'WHERE "{pk}" >= {lo + i * step} AND "{pk}" < {lo + (i + 1) * step}' for i in range(PARALLEL)]
    await say(f"{table}: {total} rækker, kopierer i {len(ranges)} dele uden indekser")

    async def part(i: int, where: str) -> int:
        s_raw, d_raw = await legacy.raw_connection(), await dst_engine.raw_connection()
        try:
            d = d_raw.driver_connection
            await d.execute("SET session_replication_role = replica")
            await d.execute("SET synchronous_commit = off")
            n = await _copy(s_raw.driver_connection, d, table, delete=False, where=where)
            await say(f"{table}: del {i + 1} af {len(ranges)} færdig ({n} rækker)")
            return n
        finally:
            s_raw.close()
            d_raw.close()

    try:
        n = sum(await asyncio.gather(*(part(i, w) for i, w in enumerate(ranges))))
    except BaseException:
        await dst.execute(f"TRUNCATE {table}")  # ingen halvfyldt tabel, der bliver sprunget over ved næste forsøg
        raise
    if indexes:
        await say(f"{table}: bygger {len(indexes)} indekser")

        async def build(ddl: str) -> None:
            raw = await dst_engine.raw_connection()
            try:
                c = raw.driver_connection
                await c.execute("SET maintenance_work_mem = '1GB'")
                await c.execute("SET max_parallel_maintenance_workers = 4")
                await c.execute(ddl)
            finally:
                raw.close()

        await asyncio.gather(*(build(ddl) for _, ddl in indexes))
    return n


async def _reset_sequences(dst, tables) -> None:
    rows = await dst.fetch("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
                           "AND column_default LIKE 'nextval%' AND table_name = ANY($1)", list(tables))
    for r in rows:
        t, c = r["table_name"], r["column_name"]
        await dst.execute(f"SELECT setval(pg_get_serial_sequence('{t}', '{c}'), coalesce(max(\"{c}\"), 1), max(\"{c}\") IS NOT NULL) FROM {t}")


async def sync_reference() -> dict[str, int]:
    """Kopiér referencedata fra observationsgruppen (hvor DOFbasen-indlæsningen skriver dem) til brugergruppen.
    Tabeller der allerede er ens springes over. Gør ingenting når grupperne deler database."""
    if not router.separate():
        return {}
    src_engine, dst_engine = router.primary(OBSERVATIONS), router.primary(USERS)
    copied: dict[str, int] = {}
    s_raw, d_raw = await src_engine.raw_connection(), await dst_engine.raw_connection()
    try:
        s, d = s_raw.driver_connection, d_raw.driver_connection
        await d.execute("SELECT pg_advisory_lock(424245)")  # kun én proces ad gangen (api og worker starter samtidig)
        try:
            for table in REFERENCE_ORDER:
                if await _fingerprint(s, table) != await _fingerprint(d, table):
                    copied[table] = await _copy(s, d, table, delete=True)
        finally:
            await d.execute("SELECT pg_advisory_unlock(424245)")
    finally:
        s_raw.close()
        d_raw.close()
    if copied:
        log.info("Referencedata synkroniseret til brugergruppen: %s", copied)
    return copied


async def migrate(legacy_dsn: str, *, wipe: bool = False, progress=None) -> dict[str, int]:
    """Flyt data fra én samlet database (den gamle) ud på gruppernes primaries. Referencedata kopieres til begge."""
    from sqlalchemy.ext.asyncio import create_async_engine
    legacy = create_async_engine(legacy_dsn)
    out: dict[str, int] = {}

    async def say(msg: str) -> None:
        log.info(msg)
        if progress:
            await progress(msg)

    await say("Opretter tabeller på de nye databaser")
    await create_schema()
    l_raw = await legacy.raw_connection()
    try:
        src = l_raw.driver_connection
        for group in GROUPS:
            d_raw = await router.primary(group).raw_connection()
            try:
                dst = d_raw.driver_connection
                await dst.execute("SET session_replication_role = replica")  # fremmednøgler tjekkes ikke under flytning
                ordered = [t.name for t in Base.metadata.sorted_tables if t.name in tables_for(group)]
                for table in ordered:
                    if await dst.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table})") and not wipe:
                        await say(f"{group}/{table} har allerede data – springes over (brug --wipe for at erstatte)")
                        continue
                    expected = await src.fetchval(f"SELECT count(*) FROM {table}")
                    if expected >= BIG_ROWS:
                        n = await _copy_big(legacy, router.primary(group), src, dst, table, expected, say)
                    else:
                        n = await _copy(src, dst, table, delete=True)
                    if n != expected:
                        raise RuntimeError(f"{group}/{table}: kopierede {n} rækker, kilden har {expected}")
                    out[f"{group}/{table}"] = n
                    if n:
                        await say(f"{group}/{table}: {n} rækker")
                await _reset_sequences(dst, ordered)
                await dst.execute("SET session_replication_role = DEFAULT")
            finally:
                d_raw.close()
    finally:
        l_raw.close()
        await legacy.dispose()
    return out
