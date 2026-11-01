"""Opstartens skema-DDL må ikke stå i kø bag lange forespørgsler (dbops.create_schema): ADD COLUMN IF NOT EXISTS springes over, når
kolonnen findes, og andre ALTER TABLE får kort tid til at få deres lås og prøver igen – så hverken api'en eller sitet hænger."""

import asyncio
import os
import time

import pytest

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


@pytest.fixture
async def schema_db():
    import asyncpg

    from portal.dbrouter import router

    base = DSN.replace("+asyncpg", "")
    admin = await asyncpg.connect(base)
    await admin.execute("DROP DATABASE IF EXISTS schematest WITH (FORCE)")
    await admin.execute("CREATE DATABASE schematest")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + "/schematest"
    router._apply_legacy(dsn)
    yield dsn.replace("+asyncpg", "")
    await router.stop()


async def hold(dsn, table, seconds):
    """En «lang forespørgsel»: en åben transaktion, der har læst fra tabellen (AccessShareLock)."""
    import asyncpg

    c = await asyncpg.connect(dsn)
    tr = c.transaction()
    await tr.start()
    await c.execute(f"SELECT count(*) FROM {table}")
    await asyncio.sleep(seconds)
    await tr.rollback()
    await c.close()


async def timed_read(dsn, table, delay):
    import asyncpg

    await asyncio.sleep(delay)
    c = await asyncpg.connect(dsn)
    t = time.monotonic()
    await c.execute(f"SELECT count(*) FROM {table}")
    await c.close()
    return time.monotonic() - t


async def test_ddl_venter_ikke_bag_lange_forespoergsler(schema_db, monkeypatch):
    from portal import dbops

    monkeypatch.setattr(dbops, "LOCK_TIMEOUT", "500ms", raising=False)
    await dbops.create_schema()  # første gang: alt oprettes
    # Kolonnerne på locations findes: opstarten skal ikke vente på en læser af locations
    blocker = asyncio.create_task(hold(schema_db, "locations", 20))
    await asyncio.sleep(0.5)
    t = time.monotonic()
    await asyncio.wait_for(dbops.create_schema(), 15)
    assert time.monotonic() - t < 15
    blocker.cancel()
    # location_details får altid ALTER COLUMN … SET DEFAULT: den venter på læseren, men andre læsere står højst kort i kø bag den
    blocker = asyncio.create_task(hold(schema_db, "location_details", 4))
    await asyncio.sleep(0.5)
    reader = asyncio.create_task(timed_read(schema_db, "location_details", 1.0))
    await asyncio.wait_for(dbops.create_schema(), 30)
    assert await reader < 2.0
    await blocker
