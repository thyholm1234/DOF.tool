"""Databaseopsætning ved opstart (idempotent; beskyttet af advisory lock mod samtidige processer)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from portal import models  # noqa: F401 – registrerer tabeller
from portal.api.mobgang import seed_presets
from portal.db import ObsSession, SessionLocal
from portal.dbgroups import OBSERVATIONS
from portal.dbops import create_schema, sync_reference
from portal.dbrouter import router
from portal.dof.reference import fix_list_species, seed_reference
from portal.services.auth import ensure_roles, ensure_superadmin, seed_roles
from portal.services.categories import seed_categories
from portal.services.danish_list import seed as seed_danish_list
from portal.services.dofbasen import seed as seed_dofbasen
from portal.services.learning import seed_courses
from portal.services.manual_finds import seed as seed_manual_finds
from portal.services.netfugl import seed as seed_netfugl
from portal.services.push import ensure_vapid

log = logging.getLogger("portal.bootstrap")


async def init_db() -> None:
    await router.start()
    await create_schema()
    # Referencedata (arter, lokaliteter …) skrives i observationsgruppen og kopieres derfra til brugergruppen
    async with ObsSession() as s:
        await s.execute(text("SELECT pg_advisory_lock(424244)"))
        try:
            await seed_reference(s)
            await fix_list_species(s)
        finally:
            await s.rollback()
            await s.execute(text("SELECT pg_advisory_unlock(424244)"))
            await s.commit()
    await sync_reference()
    async with SessionLocal() as s:
        await s.execute(text("SELECT pg_advisory_lock(424243)"))
        try:
            await seed_categories(s)
            await seed_danish_list(s)
            await seed_manual_finds(s)
            await seed_presets(s)
            await seed_roles(s)
            await ensure_roles(s, force=True)
            await ensure_superadmin(s)
            await seed_courses(s)
            await seed_dofbasen(s)
            await seed_netfugl(s)
            await ensure_vapid(s)
        finally:
            await s.rollback()
            await s.execute(text("SELECT pg_advisory_unlock(424243)"))
            await s.commit()
    log.info("Database klar")


# Store indekser på observations bygges CONCURRENTLY i baggrunden af workeren (tager minutter og må ikke låse tabellen
# under opstart). Et halvbygget (ugyldigt) indeks fra en afbrudt bygning droppes og bygges igen.
BACKGROUND_INDEXES = {
    # Lokalitetssiderne (lokalitet, artsliste, art på lokalitet) læser kun disse kolonner – index-only scan i stedet
    # for at hente hver observation fra tabellen (Grenen: ~165.000 sider pr. forespørgsel).
    "ix_obs_loknr_cover": "ON observations (loknr, dato, artnr) INCLUDE (antal, kategori, hemmelig, turid)",
}


async def build_background_indexes(delay: int = 60, attempts: int = 3) -> None:
    # Vent til de andre processers opstart (create_all og EXTRA_DDL på observations) er færdig – ellers kan
    # CREATE INDEX CONCURRENTLY gå i deadlock med dem.
    await asyncio.sleep(delay)
    async with router.primary(OBSERVATIONS).connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name, ddl in BACKGROUND_INDEXES.items():
            for attempt in range(1, attempts + 1):
                valid = (await conn.execute(text("""
                    SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid WHERE c.relname = :n
                """), {"n": name})).scalar()
                if valid:
                    break
                if valid is False:
                    await conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
                log.info("Bygger indeks %s i baggrunden", name)
                try:
                    await conn.execute(text(f"CREATE INDEX CONCURRENTLY {name} {ddl}"))
                    # Synlighedskortet skal være opdateret, før Postgres kan svare fra indekset alene
                    await conn.execute(text("VACUUM (ANALYZE) observations"))
                except Exception:  # noqa: BLE001 – workeren skal køre videre; prøv igen lidt senere
                    log.exception("Kunne ikke bygge indeks %s (forsøg %s af %s)", name, attempt, attempts)
                    await asyncio.sleep(300)
                    continue
                log.info("Indeks %s bygget", name)
                break
