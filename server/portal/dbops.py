"""Skema, referencedata-sync og flytning af data mellem databasegrupper."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time

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


BIG_ROWS = 300_000  # tabeller over denne størrelse kopieres i bidder uden sekundære indekser
BATCH_ROWS = 100_000  # ca. rækker pr. bid (hver bid er sin egen transaktion, så en afbrudt migrering kan genoptages)
PARALLEL = 4
BATCH_TIMEOUT = 900  # sekunder; en bid der hænger længere giver en tydelig fejl i stedet for stilhed
STATE = "_migration"  # hjælpetabel på målet: færdige tabeller og de indekser der er slettet midlertidigt


async def _int_pk(conn, table: str) -> str | None:
    """Primærnøglekolonnen, hvis den er ét heltal (så tabellen kan deles i intervaller)."""
    rows = await conn.fetch("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS typ FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = $1::regclass AND i.indisprimary""", table)
    return rows[0]["attname"] if len(rows) == 1 and rows[0]["typ"] in ("integer", "bigint", "smallint") else None


async def _secondary_indexes(conn, table: str) -> list[list[str]]:
    """[navn, definition] for ikke-unikke indekser – de bygges hurtigere til sidst end de opdateres række for række."""
    rows = await conn.fetch("""
        SELECT i.relname, pg_get_indexdef(i.oid) AS def FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
        WHERE x.indrelid = $1::regclass AND NOT x.indisprimary AND NOT x.indisunique""", table)
    return [[r["relname"], r["def"]] for r in rows]


def _fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


async def _copy_big(legacy, dst_engine, src, dst, table: str, total: int, say, status) -> int:
    """Stor tabel i små bidder på primærnøglen, PARALLEL ad gangen. Sekundære indekser slettes først og bygges til sidst.
    Bidder der allerede er kopieret (samme antal rækker i intervallet som i kilden) springes over, så en afbrudt
    migrering fortsætter hvor den slap."""
    row = await dst.fetchrow(f"SELECT indexes FROM {STATE} WHERE tbl = $1", table)
    if row is None:
        indexes = await _secondary_indexes(dst, table)
        await dst.execute(f"INSERT INTO {STATE} (tbl, indexes) VALUES ($1, $2::jsonb)", table, json.dumps(indexes))
    else:
        indexes = row["indexes"] if isinstance(row["indexes"], list) else json.loads(row["indexes"])
        await say(f"{table}: genoptager tidligere migrering")
    for name, _ in indexes:
        await dst.execute(f'DROP INDEX IF EXISTS "{name}"')
    if indexes:
        await say(f"{table}: {len(indexes)} indekser slettet midlertidigt (bygges igen til sidst)")

    pk = await _int_pk(src, table)
    ranges: list[str] = []
    if pk:
        # Grænser efter den faktiske fordeling (huller og negative id'er giver ellers skæve bidder)
        n_batches = max(1, -(-total // BATCH_ROWS))
        await say(f"{table}: finder grænser for {n_batches} bidder")
        cuts = await src.fetchval(f'SELECT percentile_disc($1::float8[]) WITHIN GROUP (ORDER BY "{pk}") FROM {table}',
                                  [i / n_batches for i in range(1, n_batches)]) if n_batches > 1 else []
        bounds = sorted(set(cuts or []))
        edges = [None, *bounds, None]
        for a, b in zip(edges, edges[1:]):
            cond = [f'"{pk}" >= {a}' if a is not None else None, f'"{pk}" < {b}' if b is not None else None]
            ranges.append("WHERE " + " AND ".join(c for c in cond if c) if any(cond) else "WHERE TRUE")
    if not ranges:  # ingen heltalsnøgle: én bid (stadig uden indekser)
        await dst.execute(f"DELETE FROM {table}")
        ranges = [""]
    await say(f"{table}: {_fmt(total)} rækker i {len(ranges)} bidder, {PARALLEL} ad gangen")

    done = {"rows": 0, "batches": 0, "skipped": 0, "active": 0}
    started = time.monotonic()
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for i, w in enumerate(ranges):
        queue.put_nowait((i, w))

    async def report() -> None:
        el = time.monotonic() - started
        rate = done["rows"] / el if el > 0 else 0
        left = (total - done["rows"]) / rate if rate > 0 else None
        await status({"table": table, "done": done["rows"], "total": total, "batches": done["batches"], "of": len(ranges),
                      "rate": round(rate), "eta_seconds": round(left) if left is not None else None, "active": done["active"]})

    async def worker() -> None:
        s_raw, d_raw = await legacy.raw_connection(), await dst_engine.raw_connection()
        try:
            s, d = s_raw.driver_connection, d_raw.driver_connection
            await d.execute("SET session_replication_role = replica")
            await d.execute("SET synchronous_commit = off")
            while True:
                try:
                    i, where = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                done["active"] += 1
                try:
                    want = await s.fetchval(f"SELECT count(*) FROM {table} {where}")
                    have = await d.fetchval(f"SELECT count(*) FROM {table} {where}") if where else 0
                    if have == want:
                        n = want
                        done["skipped"] += 1
                    else:
                        try:
                            n = await _copy(s, d, table, delete=bool(where), where=where, timeout=BATCH_TIMEOUT)
                        except asyncio.TimeoutError:
                            raise RuntimeError(f"{table}: bid {i + 1} ({where}) gik i stå i over {BATCH_TIMEOUT} s") from None
                finally:
                    done["active"] -= 1
                done["rows"] += n
                done["batches"] += 1
                if done["batches"] % max(1, len(ranges) // 10) == 0:  # ca. hver 10. % i loggen
                    await say(f"{table}: {_fmt(done['rows'])} af {_fmt(total)} ({done['rows'] * 100 // max(total, 1)} %)")
                await report()
        finally:
            s_raw.close()
            d_raw.close()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(5)
            await report()

    beat = asyncio.create_task(heartbeat())
    try:
        async with asyncio.TaskGroup() as tg:  # fejler én bid, stoppes de andre
            for _ in range(min(PARALLEL, len(ranges))):
                tg.create_task(worker())
    except* Exception as eg:
        raise eg.exceptions[0] from None
    finally:
        beat.cancel()
    if done["skipped"]:
        await say(f"{table}: {done['skipped']} bidder var allerede kopieret")

    for name, ddl in indexes:
        t = time.monotonic()
        await say(f"{table}: bygger indeks {name}")
        raw = await dst_engine.raw_connection()
        try:
            c = raw.driver_connection
            await c.execute("SET maintenance_work_mem = '1GB'")
            await c.execute("SET max_parallel_maintenance_workers = 4")
            await c.execute(ddl.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
        finally:
            raw.close()
        await say(f"{table}: indeks {name} bygget på {round(time.monotonic() - t)} s")
    return await dst.fetchval(f"SELECT count(*) FROM {table}")


async def _ensure_indexes(dst, tables) -> list[str]:
    """Opret indekser fra modellerne, der mangler (fx slettet af en afbrudt migrering). Returnerer navnene der blev bygget."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex
    existing = {r["indexname"] for r in await dst.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    built = []
    await dst.execute("SET maintenance_work_mem = '1GB'")
    await dst.execute("SET max_parallel_maintenance_workers = 4")
    for t in Base.metadata.sorted_tables:
        if t.name in tables:
            for idx in t.indexes:
                if idx.name not in existing:
                    await dst.execute(str(CreateIndex(idx, if_not_exists=True).compile(dialect=postgresql.dialect())))
                    built.append(idx.name)
    return built


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


async def migrate(legacy_dsn: str, *, wipe: bool = False, progress=None, status=None) -> dict[str, int]:
    """Flyt data fra én samlet database (den gamle) ud på gruppernes primaries. Referencedata kopieres til begge.
    Kan køres igen efter en afbrydelse: færdige tabeller springes over, store tabeller fortsætter bid for bid."""
    from sqlalchemy.ext.asyncio import create_async_engine
    legacy = create_async_engine(legacy_dsn, pool_size=PARALLEL + 2)
    out: dict[str, int] = {}

    async def say(msg: str) -> None:
        log.info(msg)
        if progress:
            await progress(msg)

    async def set_status(st: dict | None) -> None:
        if status:
            await status(st)

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
                await dst.execute(f"CREATE TABLE IF NOT EXISTS {STATE} (tbl text PRIMARY KEY, done boolean NOT NULL DEFAULT false, "
                                  "indexes jsonb NOT NULL DEFAULT '[]')")
                if wipe:
                    await dst.execute(f"DELETE FROM {STATE}")
                ordered = [t.name for t in Base.metadata.sorted_tables if t.name in tables_for(group)]
                for table in ordered:
                    if await dst.fetchval(f"SELECT done FROM {STATE} WHERE tbl = $1", table):
                        await say(f"{group}/{table}: allerede kopieret – springes over")
                        continue
                    t = time.monotonic()
                    expected = await src.fetchval(f"SELECT count(*) FROM {table}")
                    await set_status({"table": f"{group}/{table}", "done": 0, "total": expected})
                    if expected >= BIG_ROWS:
                        n = await _copy_big(legacy, router.primary(group), src, dst, table, expected, say, set_status)
                    else:
                        n = await _copy(src, dst, table, delete=True)
                    if n != expected:
                        raise RuntimeError(f"{group}/{table}: målet har {n} rækker, kilden har {expected}")
                    await dst.execute(f"INSERT INTO {STATE} (tbl, done) VALUES ($1, true) ON CONFLICT (tbl) DO UPDATE SET done = true",
                                      table)
                    out[f"{group}/{table}"] = n
                    if n:
                        secs = round(time.monotonic() - t)
                        await say(f"{group}/{table}: {_fmt(n)} rækker" + (f" på {secs} s" if secs >= 2 else ""))
                await say(f"{group}: kontrollerer indekser")
                built = await _ensure_indexes(dst, set(ordered))
                for ddl in models.EXTRA_DDL:  # partielle/GIN-indekser der ikke står i modellerne
                    if group in ddl_groups(ddl) and ddl.startswith("CREATE INDEX IF NOT EXISTS"):
                        await dst.execute(ddl)
                if built:
                    await say(f"{group}: manglende indekser bygget: {', '.join(built)}")
                await _reset_sequences(dst, ordered)
                await dst.execute(f"DROP TABLE {STATE}")
                await dst.execute("SET session_replication_role = DEFAULT")
            finally:
                d_raw.close()
    finally:
        await set_status(None)
        l_raw.close()
        await legacy.dispose()
    return out
