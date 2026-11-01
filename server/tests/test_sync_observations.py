"""Profil → Synkronisér alle mine observationer: job_history med full henter 1900 til i år, retter ændrede observationer, giver nye
første-set på observationsdatoen og sletter brugerens observationer, der ikke længere findes i DOFbasen – men ikke ved et mistænkeligt
lille udtræk, og aldrig andres eller manuelle fund (negative obsid)."""

import asyncio
import os

import httpx
import pytest
from sqlalchemy import text

from test_core import HEADER, row
from test_schema_locks import schema_db  # noqa: F401 – fixture

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
KODE = "8200IKL"  # obserkoden i test_core.row


def client(*obsids_antal):
    body = "\n".join([HEADER, *(row(o, antal=str(n)) for o, n in obsids_antal)]).encode("latin-1")
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, content=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.fixture
async def db(schema_db, monkeypatch):  # noqa: F811
    import redis.asyncio as aioredis

    from portal import cache, dbops
    from portal.dof import jobs
    from portal.dbrouter import ObsSession

    await dbops.create_schema()
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    await kv.flushdb()
    monkeypatch.setattr(cache, "_client", kv)

    async def no_alerts(*a, **k):
        return None
    monkeypatch.setattr(jobs.gateway, "enqueue", no_alerts)
    from portal.models import User

    async with ObsSession() as s:
        s.add(User(obserkode=KODE, navn="Inger Klit", role="user", prefs={}))
        await s.commit()
    yield ObsSession
    await kv.aclose()


async def obs(S):
    async with S() as s:
        return {r[0]: (r[1], r[2].date()) for r in (await s.execute(text("SELECT obsid, antal, first_seen FROM observations ORDER BY obsid"))).all()}


async def test_fuld_synkronisering(db):
    from datetime import date

    from portal.dof import jobs

    c, seen = client((1, 6), (2, 3), (3, 1))
    out = await jobs.job_history(c, {"obserkode": KODE, "year_from": 1900, "year_to": 2026, "full": True})
    assert out == {"obserkode": KODE, "rows": 3, "deleted": 0}
    assert seen[0]["obserdata"] == KODE and seen[0]["aar_first"] == "1900" and seen[0]["aar_second"] == "2026"
    first = await obs(db)
    assert set(first) == {1, 2, 3} and first[1][1] == date(2025, 5, 31)  # nye får observationsdatoen som første-set
    async with db() as s:  # en anden observatørs observation og et manuelt fund må aldrig slettes
        await s.execute(text("UPDATE observations SET obserkode = 'ANDEN' WHERE obsid = 3"))
        await s.execute(text("INSERT INTO observations (obsid, dato, artnr, antal, obserkode, hemmelig, kategori, afdelinger) SELECT -5, dato, artnr, 1, :k, 0, 'alm', '{}' FROM observations WHERE obsid = 1"), {"k": KODE})
        await s.commit()
    # Antal rettet på 1, 2 slettet i DOFbasen, 4 ny
    c, _ = client((1, 9), (4, 2), (5, 1), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1), (11, 1), (12, 1))
    out = await jobs.job_history(c, {"obserkode": KODE, "year_from": 1900, "year_to": 2026, "full": True})
    assert out["deleted"] == 1
    after = await obs(db)
    assert 2 not in after and after[1][0] == 9 and 3 in after and -5 in after and 4 in after
    # Mistænkeligt lille udtræk (fx en fejl hos DOFbasen): intet slettes
    c, _ = client((1, 9))
    out = await jobs.job_history(c, {"obserkode": KODE, "year_from": 1900, "year_to": 2026, "full": True})
    assert out["deleted"] == 0 and len(await obs(db)) == len(after)
