"""Snak mod en rigtig Postgres og Valkey: flag, feed, kommentarer med svar, reaktioner og moderation (docs/snak-modul-spec.md §12).

Springes over, medmindre ARTICLES_TEST_DSN er sat (se tests/test_articles_db.py for engangscontainerne). Testen sletter og
genopretter brugergruppens tabeller i den database, den peger på – brug aldrig en rigtig database."""

import asyncio
import os

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def db(monkeypatch):
    from portal import cache
    from portal.db import Base
    from portal.dbgroups import USER_TABLES
    from portal.models import EXTRA_DDL, Species, User
    from portal.dbops import ddl_groups
    from portal.services import snak

    engine = create_async_engine(DSN)
    tables = [t for t in Base.metadata.sorted_tables if t.name in USER_TABLES or t.name == "species"]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        for ddl in EXTRA_DDL:
            if "users" in ddl_groups(ddl) and "ALTER TABLE users" in ddl:
                await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    await kv.flushdb()
    monkeypatch.setattr(cache, "_client", kv)
    async with factory() as s:
        for i, (kode, role) in enumerate([("A1", "user"), ("A2", "user"), ("ED", "editor"), ("MO", "moderator"), ("SU", "superadmin")], 1):
            s.add(User(id=i, obserkode=kode, navn=f"Bruger {kode}", role=role))
        await s.commit()
        await snak.seed_categories(s)
    yield factory
    await kv.aclose()
    await engine.dispose()


@pytest.fixture
async def web(db):
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth, features

    async def session():
        async with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    async with db() as s:
        await features.save(s, {"snak": "on"})
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


async def new_post(c, cat=3, **kw):
    r = await c.post("/api/snak", json={"category_id": cat, "body": "Dagens tur til Amager", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_flag_og_adgang(web, db):
    from portal.services import features

    assert (await web["anon"].get("/api/snak/feed")).status_code == 401
    assert (await web["a1"].get("/api/snak/feed")).status_code == 200
    async with db() as s:
        await features.save(s, {"snak": "off"})
    assert (await web["su"].get("/api/snak/feed")).status_code == 404
    async with db() as s:
        await features.save(s, {"snak": "preview"})
    assert (await web["a1"].get("/api/snak/feed")).status_code == 404
    assert (await web["su"].get("/api/snak/feed")).status_code == 200
    me = (await web["su"].get("/api/me")).json()["user"]["snak"]
    assert me["can_admin"] and me["can_moderate"] and me["features"]["snak"]


async def test_opret_regler(web):
    cats = {c["slug"]: c for c in (await web["a1"].get("/api/snak/kategorier")).json()["categories"]}
    assert len(cats) == 8
    diskussion, meddelelser = cats["diskussion"]["id"], cats["meddelelser"]["id"]
    assert (await web["a1"].post("/api/snak", json={"category_id": diskussion, "body": "uden titel"})).status_code == 400
    assert (await web["a1"].post("/api/snak", json={"category_id": diskussion, "titel": "En rigtig titel", "body": "ok"})).status_code == 200
    assert (await web["a1"].post("/api/snak", json={"category_id": cats["fund"]["id"], "body": "   "})).status_code == 400
    assert (await web["a1"].post("/api/snak", json={"category_id": meddelelser, "body": "hej"})).status_code == 403
    assert (await web["mo"].post("/api/snak", json={"category_id": meddelelser, "body": "Nyt"})).status_code == 200
    assert (await web["a1"].post("/api/snak", json={"category_id": 999, "body": "x"})).status_code == 400
    r = await web["a1"].post("/api/snak", json={"category_id": cats["fund"]["id"], "body": "<script>alert(1)</script> **fed**"})
    p = (await web["a1"].get(f"/api/snak/{r.json()['id']}")).json()
    assert "<script" not in p["html"] and "<strong>fed</strong>" in p["html"]


async def test_feed_sortering_cursor_og_fastgjort(web, db):
    from portal.services import snak

    async with db() as s:
        await snak.save_settings(s, {"opslag_pr_time": 1000})
    ids = [await new_post(web["a1"], body=f"Opslag {i}") for i in range(20)]
    first = (await web["a2"].get("/api/snak/feed")).json()
    assert [p["id"] for p in first["posts"]] == ids[::-1][:15] and first["next"]
    second = (await web["a2"].get("/api/snak/feed", params={"cursor": first["next"]})).json()
    assert [p["id"] for p in second["posts"]] == ids[::-1][15:] and not second["next"]
    # en ny kommentar løfter tråden
    assert (await web["a2"].post(f"/api/snak/{ids[0]}/kommentarer", json={"body": "Hej"})).status_code == 200
    assert (await web["a2"].get("/api/snak/feed")).json()["posts"][0]["id"] == ids[0]
    # nyeste opslag ændrer ikke rækkefølgen
    assert (await web["a2"].get("/api/snak/feed", params={"sort": "nyeste"})).json()["posts"][0]["id"] == ids[-1]
    assert (await web["a2"].get("/api/snak/feed", params={"sort": "populaere"})).status_code == 200
    # fastgjorte kun på første side og kun af admin
    assert (await web["a1"].post(f"/api/snak/{ids[5]}/fastgoer", json={"pinned": True})).status_code == 403
    assert (await web["su"].post(f"/api/snak/{ids[5]}/fastgoer", json={"pinned": True})).status_code == 200
    page1 = (await web["a2"].get("/api/snak/feed")).json()
    assert page1["posts"][0]["id"] == ids[5] and page1["posts"][0]["pinned"]
    page2 = (await web["a2"].get("/api/snak/feed", params={"cursor": page1["next"]})).json()
    assert ids[5] not in [p["id"] for p in page2["posts"]]
    assert (await web["a2"].get("/api/snak/feed", params={"cursor": "ikke-en-cursor"})).status_code == 400


async def test_kommentarer_forhaandsvisning_og_svar(web):
    pid = await new_post(web["a1"])
    ks = []
    for i in range(5):
        r = await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": f"Kommentar {i}"})
        ks.append(r.json()["id"])
    # svar på et svar lægges under topniveau-kommentaren
    s1 = (await web["a1"].post(f"/api/snak/{pid}/kommentarer", json={"body": "Svar 1", "parent_id": ks[0]})).json()["id"]
    s2 = (await web["ed"].post(f"/api/snak/{pid}/kommentarer", json={"body": "Svar 2", "parent_id": s1})).json()["id"]
    feed = (await web["a1"].get("/api/snak/feed")).json()["posts"][0]
    assert feed["n_comments"] == 7 and feed["comments"]["total_top"] == 5
    assert [c["id"] for c in feed["comments"]["items"]] == [ks[4], ks[3], ks[2]]  # nyeste øverst, 3 stk.
    t = (await web["a1"].get(f"/api/snak/{pid}/kommentarer")).json()
    assert [c["id"] for c in t["items"]] == ks[::-1] and t["total"] == 5
    first = next(c for c in t["items"] if c["id"] == ks[0])
    assert [x["id"] for x in first["svar"]] == [s1, s2] and first["n_svar"] == 2
    assert first["svar"][1]["reply_to_navn"] == "Bruger A1" and first["svar"][0]["reply_to_navn"] is None
    assert [c["id"] for c in (await web["a1"].get(f"/api/snak/{pid}/kommentarer", params={"sort": "aeldste"})).json()["items"]] == ks
    # fremmed opslags parent_id afvises
    other = await new_post(web["a1"])
    assert (await web["su"].post(f"/api/snak/{other}/kommentarer", json={"body": "x", "parent_id": ks[0]})).status_code == 400
    # ret og slet egen
    assert (await web["a1"].patch(f"/api/snak/kommentarer/{ks[0]}", json={"body": "ændret"})).status_code == 403
    assert (await web["a2"].patch(f"/api/snak/kommentarer/{ks[0]}", json={"body": "ændret"})).status_code == 200
    assert (await web["a2"].delete(f"/api/snak/kommentarer/{ks[1]}")).status_code == 200
    assert (await web["a1"].get(f"/api/snak/{pid}")).json()["n_comments"] == 6


async def test_laast_skjult_og_udelukket(web, db):
    pid = await new_post(web["a1"])
    assert (await web["a1"].post(f"/api/snak/{pid}/moderer", json={"action": "laas"})).status_code == 403
    assert (await web["mo"].post(f"/api/snak/{pid}/moderer", json={"action": "laas"})).status_code == 200
    assert (await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": "x"})).status_code == 403
    assert (await web["mo"].post(f"/api/snak/{pid}/kommentarer", json={"body": "moderator må"})).status_code == 200
    cid = (await web["mo"].post(f"/api/snak/{pid}/moderer", json={"action": "aabn"})).status_code
    assert cid == 200
    c = (await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": "stødende"})).json()["id"]
    assert (await web["mo"].post(f"/api/snak/kommentarer/{c}/moderer", json={"action": "skjul", "reason": "uhøfligt"})).status_code == 200
    t = (await web["a1"].get(f"/api/snak/{pid}/kommentarer")).json()
    assert c not in [x["id"] for x in t["items"]] or next(x for x in t["items"] if x["id"] == c)["html"] == ""
    assert (await web["a1"].get(f"/api/snak/{pid}")).json()["n_comments"] == 1
    # skjult opslag: andre ser ingenting, forfatteren ser grunden
    assert (await web["mo"].post(f"/api/snak/{pid}/moderer", json={"action": "skjul", "reason": "regler"})).status_code == 200
    assert (await web["a2"].get(f"/api/snak/{pid}")).status_code == 404
    mine = (await web["a1"].get(f"/api/snak/{pid}")).json()
    assert mine["status"] == "skjult" and mine["hidden_reason"] == "regler"
    assert pid not in [p["id"] for p in (await web["a2"].get("/api/snak/feed")).json()["posts"]]
    assert (await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": "x"})).status_code == 404
    # udelukket bruger
    async with db() as s:
        await s.execute(text("INSERT INTO comment_bans (user_id, reason, hide_comments, created_by) VALUES (2, 'spam', false, 4)"))
        await s.commit()
    other = await new_post(web["a1"])
    r = await web["a2"].post(f"/api/snak/{other}/kommentarer", json={"body": "x"})
    assert r.status_code == 403 and "spam" in r.json()["detail"]


async def test_rate_limit(web):
    codes = [(await web["a1"].post("/api/snak", json={"category_id": 3, "body": f"p{i}"})).status_code for i in range(8)]
    assert codes[:6] == [200] * 6 and codes[6:] == [429, 429]
    pid = await new_post(web["a2"])
    codes = [(await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": f"k{i}"})).status_code for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


async def test_reaktioner(web, db):
    pid = await new_post(web["a1"])
    cid = (await web["a2"].post(f"/api/snak/{pid}/kommentarer", json={"body": "Flot"})).json()["id"]

    async def counts(kind="opslag"):
        if kind == "opslag":
            return (await web["a1"].get(f"/api/snak/{pid}")).json()
        return (await web["a1"].get(f"/api/snak/{pid}/kommentarer")).json()["items"][0]

    put = lambda c, t: web[c].put(f"/api/snak/{pid}/reaktion", json={"type": t})  # noqa: E731
    r = (await put("a2", "like")).json()
    assert r["count"] == 1 and r["mine"] == "like" and r["counts"] == {"like": 1}
    r = (await put("a2", "love")).json()  # skift
    assert r["count"] == 1 and r["counts"] == {"love": 1} and r["mine"] == "love"
    await put("a1", "haha")
    await put("ed", "love")
    p = await counts()
    assert p["n_reactions"] == 3 and p["reactions"]["counts"] == {"love": 2, "haha": 1} and p["reactions"]["top"] == ["love", "haha"]
    assert p["reactions"]["mine"] == "haha"  # mine er kun for den, der kalder
    r = (await put("a2", "love")).json()  # samme type igen fjerner
    assert r["mine"] is None and r["count"] == 2
    r = (await web["a1"].delete(f"/api/snak/{pid}/reaktion")).json()
    assert r["count"] == 1
    assert (await put("a1", "kaffe")).status_code == 422
    assert (await web["a1"].put("/api/snak/99999/reaktion", json={"type": "like"})).status_code == 404
    # samtidige kald giver ingen dublet
    await asyncio.gather(*[web["su"].put(f"/api/snak/{pid}/reaktion", json={"type": t}) for t in ("like", "wow", "sad")])
    async with db() as s:
        n = (await s.execute(text("SELECT count(*) FROM sn_reactions WHERE user_id = 5 AND kind = 'post' AND target_id = :p"), {"p": pid})).scalar()
        stored = (await s.execute(text("SELECT n_reactions FROM sn_posts WHERE id = :p"), {"p": pid})).scalar()
        real = (await s.execute(text("SELECT count(*) FROM sn_reactions WHERE kind = 'post' AND target_id = :p"), {"p": pid})).scalar()
    assert n <= 1 and stored == real
    # kommentarer
    r = (await web["a1"].put(f"/api/snak/kommentarer/{cid}/reaktion", json={"type": "wow"})).json()
    assert r["counts"] == {"wow": 1}
    assert (await counts("kommentar"))["n_reactions"] == 1
    who = (await web["a1"].get(f"/api/snak/kommentarer/{cid}/reaktioner")).json()
    assert who["users"][0]["type"] == "wow" and who["counts"] == {"wow": 1}
    who = (await web["a1"].get(f"/api/snak/{pid}/reaktioner", params={"type": "love"})).json()
    assert [u["type"] for u in who["users"]] == ["love"] * len(who["users"]) and who["users"][0]["navn"] == "Bruger ED"
    # skjult opslag kan ikke reageres på
    await web["mo"].post(f"/api/snak/{pid}/moderer", json={"action": "skjul"})
    assert (await put("a2", "like")).status_code == 404


async def test_visninger_og_foelg(web):
    pid = await new_post(web["a1"])
    for _ in range(2):
        await web["a2"].post(f"/api/snak/{pid}/visning")
    assert (await web["a1"].get(f"/api/snak/{pid}")).json()["views"] == 1  # én gang pr. bruger pr. døgn
    p = (await web["a1"].get(f"/api/snak/{pid}")).json()
    assert p["following"]  # egne opslag følges automatisk
    assert (await web["a1"].delete(f"/api/snak/{pid}/foelg")).json() == {"following": False}
    await web["a1"].post(f"/api/snak/{pid}/kommentarer", json={"body": "igen"})
    assert not (await web["a1"].get(f"/api/snak/{pid}")).json()["following"]  # «følg ikke» respekteres
