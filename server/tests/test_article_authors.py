"""Artikler: forfatterregister (fletning, opdeling, CSV), sprog og link-artikler (spec §12). Kræver ARTICLES_TEST_DSN (se test_articles_db.py)."""

import httpx
import pytest
from sqlalchemy import select, text

from test_articles_db import DSN, db, do_import, make_zip, sample  # noqa: F401 – db er en fixture

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


async def imported(db, tmp_path):  # noqa: F811
    zp = tmp_path / "a.zip"
    sample(zp)
    assert (await do_import(db, tmp_path, zp)).status == "faerdig"


async def test_import_opretter_forfattere_og_backfill_er_idempotent(db, tmp_path):  # noqa: F811
    from portal.models import ArtAuthor
    from portal.services import article_authors

    await imported(db, tmp_path)
    async with db() as s:
        a = (await s.execute(select(ArtAuthor))).scalar_one()
        assert (a.navn, a.netfugl_forfatter_id) == ("Pia Pil", 7)
        assert (await s.execute(text("SELECT count(*) FROM art_article_authors"))).scalar() == 3
        # artikler kun med JSON (som før migrationen) kobles; allerede koblede røres ikke
        await s.execute(text("DELETE FROM art_article_authors WHERE article_id = (SELECT min(id) FROM art_articles)"))
        await s.execute(text("UPDATE art_articles SET authors = '[{\"navn\": \"pia  pil\"}, {\"navn\": \"Ny Forfatter\"}]' WHERE id = (SELECT min(id) FROM art_articles)"))
        await s.commit()
        assert await article_authors.backfill(s) == 1 and await article_authors.backfill(s) == 0
        names = [r[0] for r in (await s.execute(text("SELECT navn FROM art_authors ORDER BY id"))).all()]
        assert names == ["Pia Pil", "Ny Forfatter"]  # «pia  pil» er samme forfatter (normaliseret navn)


async def test_gem_artikel_med_forfattere_fra_registret(db, tmp_path):  # noqa: F811
    from portal.models import ArtArticle, User
    from portal.services import article_edit, articles

    await imported(db, tmp_path)
    async with db() as s:
        ed = await s.get(User, 3)
        pia = (await s.execute(text("SELECT id FROM art_authors WHERE navn = 'Pia Pil'"))).scalar()
        art, _ = await article_edit.save(s, ed, {"title": "Ny", "body_md": "tekst", "status": "publiceret", "authors": [{"id": pia, "navn": "x"}, {"navn": "Per Peter"}, {"navn": "per  peter"}],
                                                  "sprog": "en"})
        await s.commit()
        d = await articles.detail(s, art.id, can_edit=True, user_id=3)
        assert [(x["navn"], x["id"] == pia) for x in d["authors"]] == [("Pia Pil", True), ("Per Peter", False)]  # rækkefølge bevaret, dublet fjernet
        assert d["sprog"] == "en" and (await s.get(ArtArticle, art.id)).authors == [{"navn": "Pia Pil", "netfugl_id": 7}, {"navn": "Per Peter"}]  # JSON-kopien
        assert (await articles.search(s, sprog="en"))["total"] == 1 and (await articles.search(s, sprog="da"))["total"] == 3
        assert (await articles.search(s, forfatter_id=pia))["total"] == 4 and (await articles.search(s, forfatter="per peter"))["total"] == 1
        ov = await articles.authors_overview(s, False)
        assert [(o["navn"], o["n"]) for o in ov] == [("Per Peter", 1), ("Pia Pil", 4)]


async def test_omdoeb_flet_skjul_og_del(db, tmp_path):  # noqa: F811
    from portal.models import User
    from portal.services import article_authors, article_edit, articles

    await imported(db, tmp_path)
    async with db() as s:
        ed = await s.get(User, 3)
        for t, au in (("A", "Lars Witting og Sakari Kauppinen"), ("B", "Sakari Kauppinen"), ("C", "L. Witting")):
            await article_edit.save(s, ed, {"title": t, "body_md": "x", "status": "publiceret", "authors": [{"navn": au}]})
        await s.commit()
        ids = {r[1]: r[0] for r in (await s.execute(text("SELECT id, navn FROM art_authors"))).all()}
        # omdøbning til et eksisterende navn fletter; rækkens bio overskriver
        r = await article_authors.save_many(s, [{"id": ids["L. Witting"], "navn": "lars  witting og sakari kauppinen", "bio_md": "Ornitolog"}])
        assert r["flettet"] == [{"id": ids["L. Witting"], "into": ids["Lars Witting og Sakari Kauppinen"], "flyttet": 1}]
        combo = ids["Lars Witting og Sakari Kauppinen"]
        assert (await s.get(type(await s.get(article_authors.ArtAuthor, combo)), combo)).bio_md == "Ornitolog"
        # to forfattere kan ikke få samme nye navn
        with pytest.raises(article_authors.Invalid):
            await article_authors.save_many(s, [{"id": ids["Pia Pil"], "navn": "Samme"}, {"id": ids["Sakari Kauppinen"], "navn": "samme"}])
        with pytest.raises(article_authors.Invalid):
            await article_authors.save_many(s, [{"id": 99999, "navn": "x"}])
        # skjult forfatter vises ikke i oversigten, men står stadig på artiklen
        await article_authors.save_many(s, [{"id": ids["Pia Pil"], "skjult": True}])
        assert "Pia Pil" not in [o["navn"] for o in await articles.authors_overview(s, False)]
        # del «A og B» i to; artiklens rækkefølge bevares, og Sakari Kauppinen (findes i forvejen) genbruges
        new = await article_authors.split(s, combo, [])
        assert len(new) == 2 and ids["Sakari Kauppinen"] in new
        names = {x["navn"]: x["n"] for x in await articles.authors_overview(s, False)}
        assert names == {"Lars Witting": 2, "Sakari Kauppinen": 3}
        art = (await articles.search(s, forfatter="lars witting"))["rows"][0]
        assert art["authors"] == ["Lars Witting", "Sakari Kauppinen"] and art["author_ids"][1] == ids["Sakari Kauppinen"]
        with pytest.raises(article_authors.Invalid):
            await article_authors.split(s, ids["Pia Pil"], [])  # kan ikke deles uden «og» eller komma
        assert await article_authors.merge(s, ids["Pia Pil"], new[0]) == 3
        assert (await s.execute(text("SELECT authors FROM art_articles WHERE title = 'Aztekerterne på Langli'"))).scalar()[0]["navn"] == "Lars Witting"  # JSON-kopien følger med


async def test_csv_eksport_og_import(db, tmp_path):  # noqa: F811
    from portal.services import article_authors

    await imported(db, tmp_path)
    async with db() as s:
        rows = await article_authors.admin_list(s)
        csv = article_authors.to_csv(rows)
        assert csv.startswith("﻿id;navn;skjult;artikler;netfugl_id;bruger;bio") and "Pia Pil;;3;7" in csv
        edited = csv.replace("Pia Pil;;3;7", "Pia Pilgaard;ja;3;7")
        out = await article_authors.parse_import(s, edited.encode())
        assert out["changes"] == [{"id": rows[0]["id"], "navn": "Pia Pilgaard", "skjult": True}]
        with pytest.raises(article_authors.Invalid):
            await article_authors.parse_import(s, b"navn\nx\n")


async def test_link_artikel(db, tmp_path):  # noqa: F811
    from portal.models import User
    from portal.services import article_edit, articles

    await imported(db, tmp_path)
    async with db() as s:
        ed = await s.get(User, 3)
        base = {"title": "Spændende nyhed", "status": "publiceret", "kind": "link", "authors": [{"navn": "Kilde"}]}
        for bad in ({}, {"source_url": "ikke en adresse", "lead": "x"}, {"source_url": "https://eksempel.dk/a"}):
            with pytest.raises(article_edit.Invalid):
                await article_edit.save(s, ed, {**base, **bad})
        art, _ = await article_edit.save(s, ed, {**base, "source_url": "https://eksempel.dk/a", "lead": "Kort resumé", "body_md": ""})
        await s.commit()
        hit = (await articles.search(s, kind="link"))["rows"]
        assert [(r["title"], r["source_url"], r["kind"]) for r in hit] == [("Spændende nyhed", "https://eksempel.dk/a", "link")]
        assert (await articles.search(s, kind="artikel"))["rows"][0]["source_url"] is None  # kun link-artikler eksponerer kilden på kortet
        d = await articles.detail(s, art.id, can_edit=False, user_id=1)
        assert d["lead"] == "Kort resumé" and d["source_url"] == "https://eksempel.dk/a"


@pytest.fixture
async def web(db, tmp_path):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth, features

    await imported(db, tmp_path)

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    async with db() as s:
        await features.save(s, {"artikler": "on"})
    clients = {}
    async with db() as s:
        for uid, name in [(1, "a1"), (3, "ed")]:
            token = await auth.create_session(await s.get(User, uid))
            clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
    yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def test_forfatter_api(web):
    pub = (await web["a1"].get("/api/artikler/forfattere")).json()
    assert pub == [{"id": 1, "navn": "Pia Pil", "n": 3, "har_bio": False}]
    assert (await web["a1"].get("/api/admin/artikler/forfattere")).status_code == 403
    assert (await web["a1"].get("/api/artikler?forfatter_id=1")).json()["total"] == 3
    assert (await web["a1"].get("/api/artikler?sprog=en")).json()["total"] == 0
    assert (await web["a1"].get("/api/artikler?sprog=xx")).status_code == 422
    adm = (await web["ed"].get("/api/admin/artikler/forfattere")).json()
    assert adm[0]["navn"] == "Pia Pil" and adm[0]["n"] == 3 and len(adm[0]["eksempler"]) == 3
    assert (await web["ed"].get("/api/admin/artikler/forfattere/soeg?q=pia")).json()[0]["id"] == 1
    r = await web["ed"].put("/api/admin/artikler/forfattere", json={"changes": [{"id": 1, "navn": "Pia P.", "bio_md": "Bio"}]})
    assert r.status_code == 200 and r.json() == {"gemt": 1, "flettet": []}
    assert (await web["ed"].put("/api/admin/artikler/forfattere", json={"changes": [{"id": 77, "navn": "x"}]})).status_code == 400
    assert "Pia P." in (await web["ed"].get("/api/admin/artikler/forfattere/eksport")).text
    assert (await web["ed"].post("/api/admin/artikler/forfattere/1/del", json={"navne": []})).status_code == 400  # «Pia P.» kan ikke deles
    assert (await web["ed"].post("/api/admin/artikler/forfattere/migrer")).json() == {"artikler": 0}
    body = {"title": "Link", "status": "publiceret", "kind": "link", "source_url": "https://eksempel.dk", "lead": "Resumé", "authors": [{"navn": "Pia P."}], "sprog": "en"}
    r = await web["ed"].post("/api/admin/artikler", json=body)
    assert r.status_code == 200, r.text
    got = (await web["a1"].get("/api/artikler?kind=link")).json()["rows"][0]
    assert got["source_url"] == "https://eksempel.dk" and got["sprog"] == "en" and got["authors"] == ["Pia P."] and got["author_ids"] == [1]
