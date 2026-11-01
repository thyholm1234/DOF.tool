"""Se som bruger (§13): kun superadmin, kun læsning, udløber, logges, og SSE oprettes ikke. Kræver ARTICLES_TEST_DSN."""

import os

import httpx
import pytest
from sqlalchemy import text

from test_photo_import import db  # noqa: F401 – fixture

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def web(db):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    clients = {}
    async with db() as s:
        for uid, name in [(1, "bruger"), (4, "admin"), (5, "su")]:
            token = await auth.create_session(await s.get(User, uid))
            clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
    yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def test_kun_superadmin_kan_starte(web):
    body = {"user_id": 1, "grund": "supportsag 12"}
    assert (await web["bruger"].post("/api/admin/se-som", json=body)).status_code == 403
    assert (await web["admin"].post("/api/admin/se-som", json=body)).status_code == 403  # en almindelig administrator er ikke nok
    assert (await web["su"].post("/api/admin/se-som", json={"user_id": 1, "grund": "x"})).status_code == 422  # grund kræves
    assert (await web["su"].post("/api/admin/se-som", json={"user_id": 5, "grund": "mig selv"})).status_code == 400
    assert (await web["su"].post("/api/admin/se-som", json={"user_id": 999, "grund": "ukendt"})).status_code == 404


async def test_se_som_er_kun_laesning_og_logges(web, db):  # noqa: F811
    import time

    from portal.cache import kv

    me = (await web["su"].get("/api/me")).json()["user"]
    assert me["id"] == 5 and me["view_as"] is None and me["role"] == "superadmin"
    r = await web["su"].post("/api/admin/se-som", json={"user_id": 1, "grund": "supportsag 12"})
    assert r.status_code == 200 and r.json()["until"] > time.time() + 800
    me = (await web["su"].get("/api/me")).json()["user"]
    assert me["id"] == 1 and me["obserkode"] == "LB" and me["role"] == "user" and me["view_as"]["by"] == "Super Bruger" and me["view_as"]["grund"] == "supportsag 12"
    assert "drift" not in me["modules"]  # målbrugerens rettigheder, ikke superadmins
    assert (await web["su"].get("/api/fotos/mine")).status_code in (200, 404)  # læsning virker som målbrugeren
    # alle ændrende kald afvises, også admin-kald
    for method, url, kw in [("POST", "/api/fotos/1/like", {}), ("PUT", "/api/fotos/mine/indstillinger", {"json": {"standard_rettighed": 1, "dof_samtykke": False}}),
                            ("DELETE", "/api/fotos/1", {}), ("PATCH", "/api/fotos/kommentarer/1", {"json": {"body": "x"}}), ("POST", "/api/admin/se-som", {"json": {"user_id": 2, "grund": "ny"}})]:
        r = await web["su"].request(method, url, **kw)
        assert r.status_code == 403 and "kun læsning" in r.json()["detail"], (method, url)
    assert (await web["su"].get("/api/stream")).status_code == 403  # SSE oprettes ikke
    async with db() as s:
        rows = (await s.execute(text("SELECT user_id, kind, detail FROM activity WHERE kind LIKE 'se_som%'"))).mappings().all()
    assert len(rows) == 1 and rows[0]["user_id"] == 5 and rows[0]["detail"]["bruger"] == 1 and rows[0]["detail"]["grund"] == "supportsag 12"
    # afslut
    assert (await web["su"].delete("/api/admin/se-som")).status_code == 200
    me = (await web["su"].get("/api/me")).json()["user"]
    assert me["id"] == 5 and me["view_as"] is None
    async with db() as s:
        assert (await s.execute(text("SELECT count(*) FROM activity WHERE kind = 'se_som_slut'"))).scalar() == 1


async def test_tilstanden_udloeber(web):
    from portal.api.deps import COOKIE
    from portal.cache import kv

    await web["su"].post("/api/admin/se-som", json={"user_id": 1, "grund": "udløb"})
    token = web["su"].cookies.get(COOKIE)
    assert (await web["su"].get("/api/me")).json()["user"]["id"] == 1
    import json

    raw = json.loads(await kv().get(f"viewas:{token}"))
    raw["until"] -= 3600  # udløbet (i tilfælde af at Valkey-nøglen ikke er væk endnu)
    await kv().set(f"viewas:{token}", json.dumps(raw), ex=60)
    assert (await web["su"].get("/api/me")).json()["user"]["id"] == 5
    # udløbet: skrivning er tilladt igen (fotomodulet er slået fra → 404 i stedet for 403)
    assert (await web["su"].put("/api/fotos/mine/indstillinger", json={"standard_rettighed": 1, "dof_samtykke": False})).status_code != 403


async def test_visninger_taelles_ikke_i_se_som(web, db):  # noqa: F811
    from portal.services import features

    from test_photo_import import run, sample, write_dir  # noqa: F401

    async with db() as s:
        await features.save(s, {"fotos": "on"})
        await s.execute(text("INSERT INTO gal_countries (id, navn) VALUES (1, 'Danmark')"))
        await s.execute(text("INSERT INTO gal_photos (id, status, source, views, land_id) VALUES (1, 'publiceret', 'upload', 0, 1)"))
        await s.commit()
    await web["su"].post("/api/admin/se-som", json={"user_id": 1, "grund": "tæller ikke"})
    assert (await web["su"].get("/api/fotos/1")).status_code == 200
    async with db() as s:
        assert (await s.execute(text("SELECT views FROM gal_photos WHERE id = 1"))).scalar() == 0
    await web["su"].delete("/api/admin/se-som")
    assert (await web["su"].get("/api/fotos/1")).status_code == 200
    async with db() as s:
        assert (await s.execute(text("SELECT views FROM gal_photos WHERE id = 1"))).scalar() == 1
