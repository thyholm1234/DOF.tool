"""Skema og referencedata-sync mellem databasegrupper."""

from __future__ import annotations

import logging
import tempfile

from sqlalchemy import text

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


# ---------------------------------------------------------------- kopiering af referencedata

async def _columns(conn, table: str) -> list[str]:
    rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                            "AND table_name = $1 ORDER BY ordinal_position", table)
    return [r["column_name"] for r in rows]


async def _fingerprint(conn, table: str) -> str:
    return await conn.fetchval(f"SELECT count(*)::text || ':' || coalesce(md5(string_agg(r, '' ORDER BY r)), '') "
                               f"FROM (SELECT t::text AS r FROM {table} t) q")


async def _copy(src, dst, table: str, *, delete: bool, where: str = "", timeout: float | None = None) -> int:
    """Kopiér en tabel (eller et udsnit, `where`) fra src til dst (asyncpg-forbindelser) via CSV i en midlertidig fil."""
    cols = await _columns(src, table)
    dcols = set(await _columns(dst, table))
    cols = [c for c in cols if c in dcols]
    quoted = ", ".join(f'"{c}"' for c in cols)
    with tempfile.TemporaryFile() as buf:
        await src.copy_from_query(f"SELECT {quoted} FROM {table} {where}", output=buf, format="csv", timeout=timeout)
        buf.seek(0)
        async with dst.transaction():
            if delete:
                await dst.execute(f"DELETE FROM {table} {where}")
            result = await dst.copy_to_table(table, source=buf, columns=cols, format="csv", timeout=timeout)
    return int(result.split()[-1])


async def sync_reference(tables: tuple[str, ...] = REFERENCE_ORDER) -> dict[str, int]:
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
            for table in tables:
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
