"""DKU-kandidater (dku.candidates): antal over lokalt normalt, ny art lokalt og uden for lokal sæson – på lokalitet og kommune."""

import os
from datetime import timedelta

import pytest
from sqlalchemy import text

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


@pytest.fixture
async def s(monkeypatch):
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import portal.models  # noqa: F401 – registrerer tabellerne
    from portal import cache
    from portal.db import Base
    from portal.models import Species

    engine = create_async_engine(DSN)
    tables = [t for t in Base.metadata.sorted_tables if t.name in {"species", "locations", "observers", "observations", "kommuner"}]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    monkeypatch.setattr(cache, "_client", kv)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for nr in (1, 2, 3):
            session.add(Species(artnr=nr, navn=f"Art {nr}", latin=f"Testus {nr}", sortering=nr, art_type="art", list_artnr=nr))
        await session.execute(text("INSERT INTO kommuner (kode, navn) VALUES ('0101', 'Kommune A')"))
        await session.execute(text("INSERT INTO locations (loknr, navn, kommune_kode) VALUES (1, 'Sted 1', '0101'), (2, 'Sted 2', '0101')"))
        await session.commit()
        yield session
    await kv.aclose()
    await engine.dispose()


async def add(s, rows):
    await s.execute(text("""INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger)
        VALUES (:o, :d, :a, :n, :l, 'X', 0, 'alm', '{1}')"""), [dict(zip("odanl", r)) for r in rows])
    await s.commit()


async def test_kandidater_paa_lokalitet_og_kommune(s):
    from portal.services import dku
    from portal.timeutil import dk_today

    today = dk_today()
    years = [today - timedelta(days=365 * y) for y in range(1, 7)]
    await add(s, [(10 + i, d, 1, 2, 1) for i, d in enumerate(years)]  # art 1: seks år med 2 på sted 1 på samme tid af året
              + [(20 + i, d - timedelta(days=120), 3, 1, 2) for i, d in enumerate(years[:3])]  # art 3: kun set på sted 2, uden for sæsonen
              + [(1, today, 1, 20, 1), (2, today, 2, 1, 1), (3, today, 3, 1, 2), (4, today, 3, 1, 1)])
    args = dict(afd=None, days=3, criteria=["antal", "ny", "saeson"], min_match=1, hist_years=10, antal_min=5, antal_factor=2.0,
                ny_max=0, season_days=14)
    lok = {r["obsid"]: r for r in await dku.candidates(s, level="lok", **args)}
    assert lok[1]["matched"] == ["antal"] and lok[1]["historik"] == 6 and lok[1]["p95"] == 2.0 and lok[1]["i_saesonen"] == 6
    assert lok[2]["matched"] == ["ny"] and lok[2]["historik"] == 0
    assert lok[3]["matched"] == ["saeson"] and lok[3]["historik"] == 3 and lok[3]["i_saesonen"] == 0
    assert lok[4]["matched"] == ["ny"]  # art 3 er aldrig set på sted 1
    kom = {r["obsid"]: r for r in await dku.candidates(s, level="kommune", **args)}
    assert kom[4]["matched"] == ["saeson"] and kom[4]["historik"] == 3  # men i kommunen, uden for sæsonen
    only = await dku.candidates(s, level="lok", obsids=[2], **args)
    assert [r["obsid"] for r in only] == [2]
