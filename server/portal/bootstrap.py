"""Databaseopsætning ved opstart (idempotent; beskyttet af advisory lock mod samtidige processer)."""

from __future__ import annotations

import logging

from sqlalchemy import text

from portal import models  # noqa: F401 – registrerer tabeller
from portal.db import Base, SessionLocal, engine
from portal.dof.reference import seed_reference
from portal.services.auth import ensure_superadmin
from portal.services.learning import seed_courses
from portal.services.push import ensure_vapid

log = logging.getLogger("portal.bootstrap")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        for ddl in models.EXTRA_DDL:
            await conn.execute(text(ddl))
    async with SessionLocal() as s:
        await s.execute(text("SELECT pg_advisory_lock(424243)"))
        try:
            await seed_reference(s)
            await ensure_superadmin(s)
            await seed_courses(s)
            await ensure_vapid(s)
        finally:
            await s.rollback()
            await s.execute(text("SELECT pg_advisory_unlock(424243)"))
            await s.commit()
    log.info("Database klar")
