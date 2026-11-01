"""Fotomodulets HTTP-lag: funktionsflag, rettigheder, galleri, fotoside, kommentarer og medieadgang (se test_photo_import.py for opsætning)."""

import httpx
import pytest
from sqlalchemy import text

from test_photo_import import DSN, db, run, sample, write_dir  # noqa: F401 – db er en fixture

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def web(db, tmp_path):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    write_dir(tmp_path / "imports" / "a", sample())
    assert (await run(db, "a")).status == "faerdig"
    clients = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        async with db() as s:
            for uid, name in [(1, "a1"), (2, "a2"), (4, "ad"), (5, "su")]:
                token = await auth.create_session(await s.get(User, uid))
                clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
        clients["anon"] = anon
        yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def forget_media():
    from portal.cache import kv

    for k in [k async for k in kv().scan_iter("photo:media:*")]:
        await kv().delete(k)


async def flags(db, **kw):  # noqa: F811
    from portal.services import features

    async with db() as s:
        await features.save(s, kw)


async def test_flag_og_roller(web, db):  # noqa: F811
    assert (await web["anon"].get("/api/fotos")).status_code == 401
    assert (await web["a1"].get("/api/fotos")).status_code == 404  # preview: kun for modulets administratorer
    assert (await web["ad"].get("/api/fotos")).status_code == 200
    me = (await web["ad"].get("/api/me")).json()["user"]["fotos"]
    assert me["can_admin"] and me["can_moderate"] and me["features"]["fotos"] and me["privilegie"] == "standard"
    await flags(db, fotos="on")
    assert (await web["a1"].get("/api/fotos")).status_code == 200
    me = (await web["a1"].get("/api/me")).json()["user"]["fotos"]
    assert me["features"]["fotos"] and not me["can_admin"] and not me["can_moderate"]
    await flags(db, fotos="off")
    assert (await web["su"].get("/api/fotos")).status_code == 404
    assert (await web["su"].get("/api/admin/fotos/import")).status_code == 404


async def test_galleri_og_fotoside(web, db):  # noqa: F811
    await flags(db, fotos="on")
    d = (await web["a1"].get("/api/fotos?q=Anna&per=24")).json()
    assert d["total"] == 1 and d["rows"][0]["titel"] == "Foto 3" and d["rows"][0]["fotograf"]["navn"] == "Anna Andersen"
    assert (await web["a1"].get("/api/fotos?per=7")).json()["per"] == 24  # ugyldig sidestørrelse → standard
    pid = (await web["a1"].get("/api/fotos/n/3")).json()["id"]
    p = (await web["a1"].get(f"/api/fotos/{pid}")).json()
    assert p["titel"] == "Foto 3" and p["kan"] == {"rette": False, "moderere": False, "kommentere": True, "like": True, "bannere": False}
    assert (await web["a1"].get("/api/fotos/n/999999")).status_code == 404
    assert (await web["a1"].get("/api/fotos/9999999")).status_code == 404
    k = (await web["a1"].get("/api/fotos/koder")).json()
    assert any(x["text"] == "Han" for x in k["koen"]) and k["lande"][0]["navn"] == "Danmark" and len(k["rettigheder"]) == 3


async def test_upubliceret_foto_og_medie_er_skjult(web, db):  # noqa: F811
    await flags(db, fotos="on")
    async with db() as s:
        pid, key = (await s.execute(text("SELECT p.id, m.thumb_key FROM gal_photos p JOIN content_media m ON m.id = p.media_id WHERE p.netfugl_id = 3"))).one()
        key = key or (await s.execute(text("SELECT storage_key FROM content_media m JOIN gal_photos p ON p.media_id = m.id WHERE p.id = :i"), {"i": pid})).scalar()
    assert (await web["a1"].get(f"/media/art/{key}")).status_code == 200
    async with db() as s:
        await s.execute(text("UPDATE gal_photos SET status = 'afventer', user_id = 2 WHERE id = :i"), {"i": pid})
        await s.commit()
    await forget_media()  # medie-opslaget caches i 60 s
    assert (await web["a1"].get(f"/api/fotos/{pid}")).status_code == 404
    assert (await web["a1"].get(f"/media/art/{key}")).status_code == 404
    assert (await web["a2"].get(f"/media/art/{key}")).status_code == 200  # ejeren
    assert (await web["ad"].get(f"/media/art/{key}")).status_code == 200  # moderator
    assert (await web["a1"].get("/api/fotos?q=Anna")).json()["total"] == 0
    await flags(db, fotos="off")
    await forget_media()
    assert (await web["a2"].get(f"/media/art/{key}")).status_code == 404  # modulet er slået fra


async def test_kommentarer_over_http(web, db):  # noqa: F811
    await flags(db, fotos="on")
    pid = (await web["a1"].get("/api/fotos/n/3")).json()["id"]
    r = await web["a1"].post(f"/api/fotos/{pid}/kommentarer", json={"body": "Flot!"})
    assert r.status_code == 200
    r = await web["a1"].post(f"/api/fotos/{pid}/kommentarer", json={"body": "snyd", "intern": True})
    assert r.status_code == 403
    assert (await web["ad"].post(f"/api/fotos/{pid}/kommentarer", json={"body": "intern note", "intern": True})).status_code == 200
    d = (await web["a1"].get(f"/api/fotos/{pid}/kommentarer")).json()
    assert d["interne"] == [] and len(d["archive"]) == 1 and len(d["comments"]) == 1  # arkivkommentaren fra importen er adskilt
    d = (await web["ad"].get(f"/api/fotos/{pid}/kommentarer")).json()
    assert len(d["interne"]) == 1
    assert (await web["a1"].post(f"/api/fotos/{pid}/like")).json() == {"liked": True, "likes": 5}  # 4 fra netfugl + 1
    await flags(db, fotos_kommentarer="off")
    assert (await web["a1"].get(f"/api/fotos/{pid}/kommentarer")).status_code == 404
    assert (await web["a1"].post(f"/api/fotos/{pid}/like")).status_code == 404


async def test_admin_import_api_kraever_rettighed(web, db):  # noqa: F811
    await flags(db, fotos="on")
    assert (await web["a1"].get("/api/admin/fotos/import/mapper")).status_code == 403
    r = await web["ad"].get("/api/admin/fotos/import/mapper")
    assert r.status_code == 200 and [f["sti"] for f in r.json()] == ["a"] and r.json()[0]["fotos"] == 3
    assert (await web["ad"].post("/api/admin/fotos/import/mappe", json={"sti": "../"})).status_code == 400
    assert (await web["a1"].post("/api/admin/fotos/import/mappe", json={"sti": "a"})).status_code == 403


async def test_upload_ret_slet_over_http(web, db):  # noqa: F811
    import json

    from test_photo_upload import exif_jpeg, fields

    await flags(db, fotos="on")
    send = lambda c, **kw: c.post("/api/fotos", files={"file": ("a.jpg", exif_jpeg(), "image/jpeg")}, data={"data": json.dumps({**fields(), **kw})})  # noqa: E731
    r = await send(web["a1"])
    assert r.status_code == 200 and r.json()["status"] == "afventer"
    pid = r.json()["id"]
    assert (await send(web["a1"], fugle=[])).status_code == 422
    assert (await send(web["a1"], dato="2999-01-01")).status_code == 400
    me = (await web["a1"].get("/api/fotos/mine")).json()
    assert pid in [f["id"] for f in me["fotos"]] and len(me["fotos"]) == 3 and me["kan_uploade"] is True  # + de to importerede fotos, fotografen er matchet til brugeren
    assert (await web["a1"].get(f"/api/fotos/{pid}")).json()["status"] == "afventer"  # ejeren ser det
    assert (await web["a2"].get(f"/api/fotos/{pid}")).status_code == 404
    assert (await web["a2"].put(f"/api/fotos/{pid}", json=fields())).status_code == 404
    f = (await web["a1"].get(f"/api/fotos/{pid}/felter")).json()
    assert f["lokalitet"] == "Tisvildekysten" and [b["artnr"] for b in f["fugle"]] == [1, 2] and f["rettighed"] == 3
    assert (await web["a1"].put(f"/api/fotos/{pid}", json={**fields(), "titel": "Rettet"})).status_code == 200
    assert (await web["a2"].get(f"/api/fotos/{pid}/felter")).status_code == 404
    # udelukket: afvises før filen læses
    r = await web["ad"].put("/api/admin/fotos/privilegier/2", json={"niveau": "udelukket", "note": "test"})
    assert r.status_code == 200
    assert (await send(web["a2"])).status_code == 403
    assert (await web["a2"].get("/api/fotos/mine")).json()["kan_uploade"] is False
    await flags(db, fotos_upload="off")
    assert (await send(web["a1"])).status_code == 404
    assert (await web["a1"].delete(f"/api/fotos/{pid}")).status_code == 200
    assert (await web["a1"].get(f"/api/fotos/{pid}")).status_code == 404


async def test_mine_observationer_kraever_flag(web, db):  # noqa: F811
    await flags(db, fotos="on")
    r = await web["a1"].get("/api/fotos/mine-observationer?artnr=1&dato=2026-10-27")
    assert r.status_code == 200 and [o["obsid"] for o in r.json()] == [5001]  # kun egne (a1 er obserkode LB)
    assert (await web["a1"].get("/api/fotos/til-observationer?obsid=5001,5002")).json() == {}
    await flags(db, fotos_dofbasen="off")
    assert (await web["a1"].get("/api/fotos/mine-observationer?artnr=1&dato=2026-10-27")).status_code == 404
