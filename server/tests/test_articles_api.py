"""Artikelmodulets HTTP-lag mod en rigtig app, Postgres og Valkey (se test_articles_db.py for opsætning)."""

import hashlib
import os

import httpx
import pytest

from test_articles_db import DSN, db, do_import, jpeg, make_zip, pdf, sample  # noqa: F401 – db er en fixture

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def web(db, monkeypatch):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import article_upload, auth

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(article_upload, "CHUNK", 1024)
    clients = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        async with db() as s:
            for uid, name in [(1, "a1"), (2, "a2"), (3, "ed"), (4, "mo"), (5, "su")]:
                token = await auth.create_session(await s.get(User, uid))
                clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
        clients["anon"] = anon
        yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def set_flag(db, **flags):  # noqa: F811
    from portal.services import features

    async with db() as s:
        await features.save(s, flags)


async def test_access_follows_feature_flag_and_roles(web, db):  # noqa: F811
    assert (await web["anon"].get("/api/artikler/forside")).status_code == 401
    me = (await web["a1"].get("/api/me")).json()["user"]["artikler"]
    assert me["features"]["artikler"] is False and not me["can_edit"] and not me["admin"]
    assert (await web["a1"].get("/api/artikler/forside")).status_code == 404  # preview: kun for dem med modulet
    assert (await web["ed"].get("/api/artikler/forside")).status_code == 200
    me = (await web["ed"].get("/api/me")).json()["user"]["artikler"]
    assert me["can_edit"] and me["admin"] and me["features"]["artikler_kommentarer"]
    await set_flag(db, artikler="on")
    assert (await web["a1"].get("/api/artikler/forside")).status_code == 200
    assert (await web["a1"].get("/api/me")).json()["user"]["artikler"]["features"]["artikler"] is True
    await set_flag(db, artikler="off")
    assert (await web["su"].get("/api/artikler/forside")).status_code == 404
    await set_flag(db, artikler="on", artikler_kommentarer="off")
    assert (await web["a1"].get("/api/artikler/1/kommentarer")).status_code == 404


async def test_csrf_and_permissions_on_admin_routes(web, db):  # noqa: F811
    await set_flag(db, artikler="on")
    body = {"title": "Ny", "body_md": "tekst", "status": "publiceret"}
    assert (await web["a1"].post("/api/admin/artikler", json=body)).status_code == 403  # ingen rettighed
    assert (await web["ed"].post("/api/admin/artikler", json=body, headers={"X-Requested-With": ""})).status_code == 403  # CSRF-vagten
    assert (await web["a1"].get("/api/admin/artikler/emneord")).status_code == 403
    r = await web["ed"].post("/api/admin/artikler", json=body)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    got = (await web["a1"].get(f"/api/artikler/{aid}")).json()
    assert got["title"] == "Ny" and "body_md" not in got and not got["can_edit"]
    assert "body_md" in (await web["ed"].get(f"/api/artikler/{aid}")).json()
    r = await web["ed"].put(f"/api/admin/artikler/{aid}", json={**body, "title": "Nyere", "version": 99})
    assert r.status_code == 409
    r = await web["ed"].put(f"/api/admin/artikler/{aid}", json={**body, "title": "Nyere", "version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert [x["version"] for x in (await web["ed"].get(f"/api/admin/artikler/{aid}/revisioner")).json()] == [1]
    rid = (await web["ed"].get(f"/api/admin/artikler/{aid}/revisioner")).json()[0]["id"]
    assert (await web["ed"].post(f"/api/admin/artikler/{aid}/revisioner/{rid}/gendan")).status_code == 200
    assert (await web["a1"].get(f"/api/artikler/{aid}")).json()["title"] == "Ny"
    assert (await web["ed"].post("/api/admin/artikler", json={**body, "status": "planlagt"})).status_code == 400  # planlagt uden tidspunkt
    assert (await web["a1"].get("/api/artikler/99999")).status_code == 404
    assert (await web["ed"].delete(f"/api/admin/artikler/{aid}")).status_code == 200


async def test_chunked_upload_import_and_media_delivery(web, db, tmp_path):  # noqa: F811
    from portal.services import article_import

    await set_flag(db, artikler="on")
    zp = tmp_path / "a.zip"
    sample(zp)
    data = zp.read_bytes()
    ed = web["ed"]
    r = await ed.post("/api/admin/artikler/import", json={"filename": "a.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    iid, chunk, parts = r.json()["id"], r.json()["chunk_size"], r.json()["parts"]
    assert chunk == 1024 and parts == -(-len(data) // 1024) and parts > 3
    assert (await ed.post(f"/api/admin/artikler/import/{iid}/start", json={})).status_code == 400  # dele mangler
    for n in [2, 0, 1] + list(range(3, parts)):  # i vilkårlig rækkefølge; del 1 sendes to gange (idempotent)
        r = await ed.put(f"/api/admin/artikler/import/{iid}/del/{n}", content=data[n * chunk:(n + 1) * chunk])
        assert r.status_code == 200, r.text
    assert (await ed.put(f"/api/admin/artikler/import/{iid}/del/1", content=data[chunk:2 * chunk])).status_code == 200
    assert (await ed.put(f"/api/admin/artikler/import/{iid}/del/1", content=b"for kort")).status_code == 400
    assert (await ed.put(f"/api/admin/artikler/import/{iid}/del/{parts}", content=b"x")).status_code == 400
    assert (await ed.get(f"/api/admin/artikler/import/{iid}/dele")).json()["dele"] == list(range(parts))
    assert (await web["a1"].post(f"/api/admin/artikler/import/{iid}/start", json={})).status_code == 403
    r = await ed.post(f"/api/admin/artikler/import/{iid}/start", json={"fetch_images": False})
    assert r.status_code == 200 and r.json()["status"] == "klar", r.text
    assert (await ed.post(f"/api/admin/artikler/import/{iid}/start", json={})).status_code == 409  # allerede startet
    from portal.cache import kv

    cmd = await kv().lpop("worker:commands")
    assert '"articles_import"' in cmd and f'"import_id": {iid}' in cmd
    await article_import.run_import(iid)  # det workeren gør
    st = (await ed.get(f"/api/admin/artikler/import/{iid}")).json()
    assert st["status"] == "faerdig" and st["stats"]["oprettet"] == 3 and st["finished_at"], st
    assert len((await ed.get("/api/admin/artikler/import")).json()) == 1
    # brugerfladen
    front = (await web["a1"].get("/api/artikler/forside")).json()
    assert front["total"] == 3 and front["hero"]["cover"]
    res = (await web["a1"].get("/api/artikler", params={"q": "hjejler"})).json()
    assert res["total"] == 1
    aid = res["rows"][0]["id"]
    art = (await web["a1"].get(f"/api/artikler/{aid}")).json()
    assert art["images"] and art["pdfs"] and art["tags"] and any(s["navn"] == "Hjejle" for s in art["species"]) and "media:" not in art["body_html"]
    assert "/media/art/" in art["body_html"]
    n = (await web["a1"].get("/api/artikler/n/155")).json()["id"]
    assert (await web["a1"].get(f"/api/artikler/{n}")).json()["title"].startswith("Aztekerterne")
    assert (await web["a1"].get("/api/artikler/n/9999")).status_code == 404
    assert (await web["a1"].get("/api/artikler/emneord")).json() and (await web["a1"].get("/api/artikler/arter")).json()
    assert (await web["a1"].get("/api/artikler/arter/1")).json()["total"] == 1
    assert (await web["a1"].get("/api/artikler", params={"tag": [1, 2], "art": [1], "fra": 1999, "sort": "mest_laest", "mode": "praecis", "q": "ryle"})).status_code == 200
    assert (await web["a1"].get("/api/artikler", params={"mode": "sql"})).status_code == 422
    # medier: kræver login, kun gyldige navne, PDF må vises i iframe, billeder får lang cache
    img_url = art["images"][0]["url"]
    assert (await web["anon"].get(img_url)).status_code == 401
    r = await web["a1"].get(img_url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"].startswith("private")
    assert (await web["a1"].get(img_url, headers={"Range": "bytes=0-9"})).status_code == 206
    assert (await web["a1"].get("/media/art/../etc/passwd")).status_code in (404, 422)
    assert (await web["a1"].get("/media/art/ab/not-a-hash.jpg")).status_code == 404
    r = await web["a1"].get(art["pdfs"][0]["url"])
    assert r.status_code == 200 and r.headers["x-frame-options"] == "SAMEORIGIN" and "sandbox" not in r.headers["content-security-policy"]
    r = await web["a1"].get(art["pdfs"][0]["thumb"])
    assert r.headers["content-type"] == "image/webp"
    # siden: CSP tillader indlejring fra tilladelseslisten
    page = await web["anon"].get("/")
    assert "frame-src 'self' https://dofbasen.dk" in page.headers["content-security-policy"] and "xeno-canto.org" in page.headers["content-security-policy"]
    assert (await ed.put("/api/admin/artikler/indstillinger", json={"embed_hosts": ["evil.com;script-src *"]})).status_code == 400
    assert (await ed.put("/api/admin/artikler/indstillinger", json={"embed_hosts": ["birdsound.example"]})).status_code == 200
    from portal.services import article_settings

    article_settings._frame["hosts"] = None
    assert "https://birdsound.example" in (await web["anon"].get("/")).headers["content-security-policy"]


async def test_render_preview_and_media_upload(web, db):  # noqa: F811
    await set_flag(db, artikler="on")
    r = await web["a1"].post("/api/content/render", json={"md": "**x**"})
    assert r.status_code == 403
    r = await web["ed"].post("/api/content/render", json={"md": "## Hej\n\nVi så Dværgterne <script>alert(1)</script> [[art:1]]\n\n<iframe src='https://evil.example/x'></iframe>"})
    j = r.json()
    assert r.status_code == 200 and "<script" not in j["html"] and "<h2>" in j["html"] and "evil.example" not in j["html"]
    assert [s["artnr"] for s in j["species"]] == [1] and j["warnings"] and j["words"] > 3
    assert (await web["ed"].post("/api/content/render", json={"md": "x", "profile": "kommentar"})).status_code == 422
    files = {"file": ("foto.jpg", jpeg(), "image/jpeg")}
    r = await web["ed"].post("/api/content/media", files=files, data={"owner_kind": "artikel", "alt": "Et billede"})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["kind"] == "billede" and m["url"].startswith("/media/art/") and m["alt"] == "Et billede"
    assert (await web["a1"].post("/api/content/media", files=files, data={"owner_kind": "artikel"})).status_code == 403
    assert (await web["ed"].post("/api/content/media", files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}, data={"owner_kind": "artikel"})).status_code == 400
    assert (await web["ed"].post("/api/content/media", files={"file": ("x.jpg", jpeg(), "image/jpeg")}, data={"owner_kind": "nyhed"})).status_code == 403  # ingen «indhold»
    assert [x["id"] for x in (await web["ed"].get("/api/content/media", params={"owner_kind": "artikel"})).json()] == [m["id"]]
    # indsæt billedet i en ny artikel: mediet adopteres, og forhåndsvisningen viser det
    body = {"title": "Med billede", "body_md": f"![Et billede](media:{m['id']} \"Tekst\")", "status": "publiceret", "cover_media_id": m["id"]}
    aid = (await web["ed"].post("/api/admin/artikler", json=body)).json()["id"]
    art = (await web["a1"].get(f"/api/artikler/{aid}")).json()
    assert art["images"][0]["id"] == m["id"] and "art-cap" in art["body_html"] and art["cover"]["id"] == m["id"]
    r = await web["ed"].patch(f"/api/content/media/{m['id']}", json={"alt": "Nyt alt"})
    assert r.json()["alt"] == "Nyt alt"
    assert (await web["ed"].delete(f"/api/content/media/{m['id']}")).status_code == 200


async def test_comments_reports_and_moderation_over_http(web, db):  # noqa: F811
    await set_flag(db, artikler="on")
    aid = (await web["ed"].post("/api/admin/artikler", json={"title": "Debat", "body_md": "tekst", "status": "publiceret"})).json()["id"]
    r = await web["a1"].post(f"/api/artikler/{aid}/kommentarer", json={"body": "Hej **verden**"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert (await web["a2"].post(f"/api/artikler/{aid}/kommentarer", json={"body": "Svar", "parent_id": cid})).status_code == 200
    tree = (await web["a2"].get(f"/api/artikler/{aid}/kommentarer")).json()
    assert tree["comments"][0]["mine"] is False and tree["comments"][0]["svar"][0]["mine"] is True and "<strong>" in tree["comments"][0]["html"]
    assert "report_count" not in tree["comments"][0]
    assert (await web["a1"].post(f"/api/artikler/kommentarer/{cid}/anmeld", json={"reason": "spam"})).status_code == 400  # egen
    assert (await web["a2"].post(f"/api/artikler/kommentarer/{cid}/anmeld", json={"reason": "opfundet"})).status_code == 400
    assert (await web["a2"].post(f"/api/artikler/kommentarer/{cid}/anmeld", json={"reason": "chikane"})).status_code == 200
    assert (await web["a1"].get("/api/admin/artikler/kommentarer")).status_code == 403
    q = (await web["mo"].get("/api/admin/artikler/kommentarer", params={"tab": "anmeldte"})).json()
    assert q[0]["id"] == cid and q[0]["reports"][0]["reason"] == "chikane"
    assert (await web["mo"].get(f"/api/artikler/{aid}/kommentarer")).json()["comments"][0]["report_count"] == 1
    assert (await web["mo"].post(f"/api/admin/artikler/kommentarer/{cid}/skjul", json={"reason": "regelbrud", "notify": True})).status_code == 200
    hidden = (await web["a2"].get(f"/api/artikler/{aid}/kommentarer")).json()["comments"][0]
    assert hidden["html"] == "" and hidden["status"] == "skjult" and "hidden_reason" not in hidden or hidden.get("hidden_reason") is None
    assert (await web["a1"].get(f"/api/artikler/{aid}/kommentarer")).json()["comments"][0]["hidden_reason"] == "regelbrud"  # forfatteren ser årsagen
    assert (await web["mo"].post(f"/api/admin/artikler/kommentarer/{cid}/gendan", json={})).status_code == 200
    assert (await web["a1"].patch(f"/api/artikler/kommentarer/{cid}", json={"body": "Rettet"})).status_code == 200
    assert (await web["a2"].patch(f"/api/artikler/kommentarer/{cid}", json={"body": "andres"})).status_code == 403
    assert (await web["a1"].delete(f"/api/artikler/kommentarer/{cid}")).status_code == 200
    # notifikationer og følg
    assert (await web["a1"].get("/api/artikler/notifikationer")).json()["alle"] is False
    r = await web["a1"].put("/api/artikler/notifikationer", json={"alle": True, "kommentarer": False})
    assert r.json()["alle"] is True and r.json()["kommentarer"] is False and any(f["id"] == aid for f in r.json()["foelger"])
    assert (await web["a1"].delete(f"/api/artikler/{aid}/foelg")).json() == {"foelger": False}
    assert (await web["a1"].get(f"/api/artikler/{aid}")).json()["muted"] is True
    await set_flag(db, artikler_notifikationer="off")
    assert (await web["a1"].get("/api/artikler/notifikationer")).status_code == 404


async def test_news_over_http(web, db):  # noqa: F811
    assert (await web["ed"].post("/api/admin/news", json={"title": "Nyhed", "body": "tekst"})).status_code == 403  # redaktør har ikke «indhold»
    r = await web["su"].post("/api/admin/news", json={"title": "Første nyhed", "body": "Linje 1\nLinje 2\n\n## Overskrift", "published": True})
    assert r.status_code == 200, r.text
    nid = r.json()["id"]
    pub = (await web["a1"].get("/api/news", params={"limit": 3})).json()
    assert pub[0]["id"] == nid and pub[0]["teaser"] and pub[0]["body"].startswith("Linje 1")
    one = (await web["a1"].get(f"/api/news/{nid}")).json()
    assert "<h2>" in one["html"]
    r = await web["su"].put(f"/api/admin/news/{nid}", json={"title": "Rettet", "body": "ny", "status": "kladde", "version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert (await web["a1"].get(f"/api/news/{nid}")).status_code == 404  # kladde
    assert (await web["su"].put(f"/api/admin/news/{nid}", json={"title": "x1", "body": "ny", "version": 1})).status_code == 409
    assert (await web["su"].get(f"/api/admin/news/{nid}")).json()["status"] == "kladde"
    assert len((await web["su"].get("/api/admin/news")).json()) == 1
    assert (await web["su"].delete(f"/api/admin/news/{nid}")).status_code == 200
    _ = (os, make_zip, pdf, do_import)
