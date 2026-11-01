"""Fotomoderation: kø, godkend/afvis med besked og notifikation, afpublicér, lås, fotografer og koder (fase 4). Kræver ARTICLES_TEST_DSN."""

import os

import httpx
import pytest
from sqlalchemy import text

from test_photo_import import db, run, sample, write_dir  # noqa: F401
from test_photo_upload import exif_jpeg, fields

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def web(db, tmp_path):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth, features

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    write_dir(tmp_path / "imports" / "a", sample())
    assert (await run(db, "a")).status == "faerdig"
    async with db() as s:
        await features.save(s, {"fotos": "on"})
    clients = {}
    async with db() as s:
        for uid, name in [(1, "a1"), (2, "a2"), (4, "ad"), (5, "su")]:
            token = await auth.create_session(await s.get(User, uid))
            clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
    yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def upload(c, **kw):
    import json

    r = await c.post("/api/fotos", files={"file": ("a.jpg", exif_jpeg(), "image/jpeg")}, data={"data": json.dumps({**fields(), **kw})})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_forloeb_afventer_afvist_ret_godkendt(web, db):  # noqa: F811
    pid = await upload(web["a2"])
    q = (await web["ad"].get("/api/admin/fotos/koe?status=afventer")).json()
    assert q["total"] == 1 and q["antal"] == {"afventer": 1, "afvist": 0, "afpubliceret": 0} and q["rows"][0]["id"] == pid
    assert q["rows"][0]["n_art"] == 3 and q["rows"][0]["graense"] == 30 and q["rows"][0]["over_graense"] is False  # arten Dværgterne har tre publicerede fotos (arkivet)
    assert (await web["a1"].get("/api/admin/fotos/koe")).status_code == 403
    # afvis → besked i brugerens support (ikke bare en notifikation)
    r = await web["ad"].put(f"/api/admin/fotos/{pid}/status", json={"status": "afvist", "grund": "Tvivl om artsbestemmelsen"})
    assert r.status_code == 200
    async with db() as s:
        t = (await s.execute(text("SELECT id, subject, kind, unread_user FROM support_threads WHERE user_id = 2"))).mappings().one()
        assert t["kind"] == "moderation" and t["unread_user"] and "blev ikke godkendt" in t["subject"] and "Dværgterne" in t["subject"]
        m = (await s.execute(text("SELECT body, from_admin, context FROM support_messages WHERE thread_id = :t"), {"t": t["id"]})).mappings().one()
        assert "Tvivl om artsbestemmelsen" in m["body"] and m["from_admin"] and m["context"]["path"] == f"#/fotos/{pid}"
    me = (await web["a2"].get("/api/fotos/mine")).json()["fotos"][0]
    assert me["status"] == "afvist" and me["afvist_grund"] == "Tvivl om artsbestemmelsen"
    assert (await web["ad"].get("/api/admin/fotos/koe?status=afvist")).json()["total"] == 1
    # ejeren retter → tilbage i køen
    assert (await web["a2"].put(f"/api/fotos/{pid}", json={**fields(), "titel": "Rettet"})).json()["status"] == "afventer"
    assert (await web["ad"].get("/api/admin/fotos/koe")).json()["total"] == 1
    # godkend → notifikation (ikke support-tråd)
    assert (await web["ad"].put(f"/api/admin/fotos/{pid}/status", json={"status": "publiceret"})).status_code == 200
    async with db() as s:
        n = (await s.execute(text("SELECT title, url FROM notifications WHERE user_id = 2"))).mappings().all()
        titles = [x["title"] for x in n]
        assert "Dit foto af Dværgterne er nu i galleriet" in titles and "Besked fra moderator: Dit foto af Dværgterne blev ikke godkendt" in titles and len(titles) == 2
        assert next(x["url"] for x in n if x["title"].startswith("Dit foto")) == f"/#/fotos/{pid}"
        assert (await s.execute(text("SELECT count(*) FROM support_threads WHERE user_id = 2"))).scalar() == 1
        assert (await s.execute(text("SELECT behandlet_af, published_at IS NOT NULL FROM gal_photos WHERE id = :i"), {"i": pid})).one() == (4, True)
    assert (await web["a1"].get(f"/api/fotos/{pid}")).status_code == 200  # nu offentligt
    # afpublicér med grund
    await web["ad"].put(f"/api/admin/fotos/{pid}/status", json={"status": "afpubliceret", "grund": "Findes allerede"})
    assert (await web["a1"].get(f"/api/fotos/{pid}")).status_code == 404
    assert (await web["ad"].get("/api/admin/fotos/koe?status=afpubliceret")).json()["total"] == 1
    # arkivfotos (uden bruger) giver ingen besked
    async with db() as s:
        arch = (await s.execute(text("SELECT id FROM gal_photos WHERE netfugl_id = 3"))).scalar()  # Anna Andersen er tvetydig → ingen bruger
        before = (await s.execute(text("SELECT count(*) FROM support_threads"))).scalar()
    await web["ad"].put(f"/api/admin/fotos/{arch}/status", json={"status": "afvist", "grund": "x"})
    async with db() as s:
        assert (await s.execute(text("SELECT count(*) FROM support_threads"))).scalar() == before  # ingen bruger → ingen besked


async def test_over_graensen_men_ikke_for_sjaeldne(web, db):  # noqa: F811
    from portal.services import photo_settings

    async with db() as s:
        await photo_settings.save(s, {"max_pr_art": 3})
    pid = await upload(web["a2"], fugle=[{"artnr": 1}])
    row = (await web["ad"].get("/api/admin/fotos/koe")).json()["rows"][0]
    assert row["id"] == pid and row["n_art"] == 3 and row["over_graense"] is True
    async with db() as s:
        await s.execute(text("UPDATE species SET klass = 'SU' WHERE artnr = 1"))
        await s.commit()
    row = (await web["ad"].get("/api/admin/fotos/koe")).json()["rows"][0]
    assert row["over_graense"] is False and row["sjaelden"] is True  # 30-grænsen gælder ikke sjældne arter


async def test_laas_og_gruppering_af_moderator(web, db):  # noqa: F811
    pid = await upload(web["a2"])
    other = await upload(web["a2"])
    assert (await web["ad"].get(f"/api/admin/fotos/{pid}/gruppeforslag")).json() == {"ids": [other]}
    assert (await web["a1"].get(f"/api/admin/fotos/{pid}/gruppeforslag")).status_code == 403
    assert (await web["ad"].post(f"/api/fotos/{pid}/gruppe", json={"ids": [other]})).status_code == 200
    assert (await web["ad"].put(f"/api/admin/fotos/{pid}/laas", json={"locked": True})).status_code == 200
    assert (await web["a2"].put(f"/api/fotos/{pid}", json=fields())).status_code == 403
    assert (await web["a2"].delete(f"/api/fotos/{pid}")).status_code == 403
    p = (await web["ad"].get(f"/api/fotos/{pid}")).json()
    assert p["laast"]["af"] == "Admin Adminsen" and p["kan"]["moderere"] and len(p["gruppe"]) == 2
    assert (await web["ad"].put(f"/api/admin/fotos/{pid}/laas", json={"locked": False})).status_code == 200
    assert (await web["a2"].put(f"/api/fotos/{pid}", json=fields())).status_code == 200
    assert (await web["ad"].delete(f"/api/fotos/{other}")).status_code == 200  # moderator må slette andres


async def test_fotografer_koble_og_flette(web, db):  # noqa: F811
    r = (await web["ad"].get("/api/admin/fotos/fotografer?match=tvetydig")).json()
    assert [x["navn"] for x in r["rows"]] == ["Anna Andersen"] and r["antal"]["auto"] == 1
    anna = r["rows"][0]["id"]
    leif = (await web["ad"].get("/api/admin/fotos/fotografer?q=Leif")).json()["rows"][0]
    assert leif["user_id"] == 1 and leif["fotos"] == 2
    assert (await web["ad"].patch(f"/api/admin/fotos/fotografer/{anna}", json={"user_id": 1})).status_code == 400  # brugeren er allerede en anden fotograf
    assert (await web["ad"].patch(f"/api/admin/fotos/fotografer/{anna}", json={"user_id": 2})).status_code == 200
    async with db() as s:
        assert (await s.execute(text("SELECT user_id, match FROM gal_photographers WHERE id = :i"), {"i": anna})).one() == (2, "manuel")
        assert (await s.execute(text("SELECT user_id FROM gal_photos WHERE netfugl_id = 3"))).scalar() == 2
    # fjern koblingen igen; manuel røres ikke af næste automatiske kørsel
    assert (await web["ad"].patch(f"/api/admin/fotos/fotografer/{anna}", json={"unlink": True})).status_code == 200
    assert (await web["ad"].post("/api/admin/fotos/fotografer/match")).status_code == 200
    async with db() as s:
        assert (await s.execute(text("SELECT user_id, match FROM gal_photographers WHERE id = :i"), {"i": anna})).one() == (None, "manuel")
    # flet: Anna ind i Leif (Leif har en bruger) → fotos flyttes, Anna bevares som flettet
    assert (await web["ad"].post("/api/admin/fotos/fotografer/flet", json={"from_id": anna, "into_id": leif["id"]})).status_code == 200
    async with db() as s:
        assert (await s.execute(text("SELECT count(*) FROM gal_photos WHERE photographer_id = :i"), {"i": leif["id"]})).scalar() == 3
        assert (await s.execute(text("SELECT merged_into FROM gal_photographers WHERE id = :i"), {"i": anna})).scalar() == leif["id"]
    assert (await web["ad"].post("/api/admin/fotos/fotografer/flet", json={"from_id": leif["id"], "into_id": leif["id"]})).status_code == 400
    # en senere import af Annas netfugl-profil finder den flettede fotograf
    from portal.services import photo_photographers

    async with db() as s:
        found = await photo_photographers.find_or_create(s, "Anna Andersen", netfugl_bruger_id=77)
        assert found.id == leif["id"]


async def test_koder_og_indstillinger(web, db):  # noqa: F811
    codes = (await web["ad"].get("/api/admin/fotos/koder")).json()
    sidd = next(c for c in codes if c["code"] == "siddende")
    assert sidd["brugt"] == 3
    r = await web["ad"].put("/api/admin/fotos/koder", json={"items": [{"kind": "adfaerd", "code": "siddende", "text": "Sidder", "sort": 5, "dofbasen_code": "SI"},
                                                                   {"kind": "adfaerd", "code": "", "text": "Svømmende", "sort": 50}]})
    assert r.status_code == 200
    codes = (await web["ad"].get("/api/admin/fotos/koder")).json()
    assert next(c for c in codes if c["code"] == "siddende")["dofbasen_code"] == "SI" and any(c["code"] == "svoemmende" for c in codes)
    r = await web["ad"].post("/api/admin/fotos/koder/flet", json={"kind": "adfaerd", "from_code": "siddende", "into_code": "flyvende"})
    assert r.json() == {"ok": True, "fugle": 3}
    assert not any(c["code"] == "siddende" for c in (await web["ad"].get("/api/admin/fotos/koder")).json())
    assert (await web["ad"].put("/api/admin/fotos/koder", json={"items": [{"kind": "xx", "text": "a"}]})).status_code == 422
    s = (await web["ad"].put("/api/admin/fotos/indstillinger", json={"max_pr_art": 12, "afvis_grunde": ["a", " ", "b"], "tekster": {"retningslinjer": "## Regler\n\nVær venlig."}})).json()
    assert s["max_pr_art"] == 12 and s["afvis_grunde"] == ["a", "b"]
    assert (await web["ad"].put("/api/admin/fotos/indstillinger", json={"default_per_side": 10})).status_code == 400
    t = (await web["a1"].get("/api/fotos/sider/retningslinjer")).json()
    assert "<h2" in t["html"] and t["max_pr_art"] == 12
    assert (await web["a1"].put("/api/admin/fotos/indstillinger", json={})).status_code == 403
