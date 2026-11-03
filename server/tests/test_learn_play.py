"""Læring 2.0, fase 0–1: migrering, afspilning, bedømmelse af statiske typer, ingen facit til klienten (kræver ARTICLES_TEST_DSN)."""

import httpx
import pytest
from sqlalchemy import text

from test_photo_import import DSN, db  # noqa: F401 – db er en fixture

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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=H) as anon:
        async with db() as s:
            for uid, name in [(1, "a1"), (2, "a2"), (4, "ad"), (5, "su")]:
                token = await auth.create_session(await s.get(User, uid))
                clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
        clients["anon"] = anon
        yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def flags(db, **kw):  # noqa: F811
    from portal.services import features

    async with db() as s:
        await features.save(s, kw)


async def seed_course(db, published=True, visibility="alle"):  # noqa: F811
    from portal.models import Course, Question, Unit

    async with db() as s:
        c = Course(slug="k1", title="Kursus", kind="kursus", published=published, visibility=visibility, skills=["Bemærk"], hue="sø")
        s.add(c)
        await s.flush()
        s.add_all([Question(course_id=c.id, position=0, prompt="Hvad er en iagttagelse?", choices=[{"text": "Det man så", "correct": True}, {"text": "Et gæt", "correct": False}], explanation="Forklaring")])
        s.add(Question(course_id=c.id, position=1, prompt="Navnet på en sangfugl?", type="tekst", accept=["solsort"], config={"fuzzy": True}, choices=[], explanation="Solsort"))
        s.add(Question(course_id=c.id, position=2, prompt="Sorter efter størrelse", type="raekkefoelge", config={"items": ["Gærdesmutte", "Solsort", "Ravn"]}, choices=[]))
        await s.commit()
        return c.id


async def test_flag_og_migrering(web, db):  # noqa: F811
    from portal.services import learning

    await seed_course(db)
    assert (await web["anon"].get("/api/learn/katalog")).status_code == 401  # hele portalen kræver login
    assert (await web["a1"].get("/api/learn/katalog")).status_code == 404  # preview: kun redaktører/superadmin
    async with db() as s:
        assert await learning.migrate_v2(s) == 1
        assert await learning.migrate_v2(s) == 0  # idempotent
    await flags(db, laering_portal="on")
    d = (await web["a1"].get("/api/learn/katalog")).json()
    assert [c["slug"] for c in d["kurser"]] == ["k1"] and d["kurser"][0]["n_units"] == 1 and d["kurser"][0]["n_acts"] == 3
    info = (await web["a1"].get("/api/learn/portal")).json()
    assert info["portal"] and not info["byg"] and not info["editor"]


async def test_afspilning_uden_facit_og_bedoemmelse(web, db):  # noqa: F811
    from portal.services import learning

    await seed_course(db)
    async with db() as s:
        await learning.migrate_v2(s)
    await flags(db, laering_portal="on")
    u = (await web["a1"].get("/api/learn/courses/k1/units")).json()
    uid = u["units"][0]["id"]
    assert not u["units"][0]["locked"]
    sess = (await web["a1"].get(f"/api/learn/units/{uid}/session")).json()
    qs = {q["prompt"]: q for q in sess["questions"]}
    assert len(qs) == 3 and "correct" not in str(sess)
    valg = qs["Hvad er en iagttagelse?"]
    right = next(c["id"] for c in valg["choices"] if c["text"] == "Det man så")
    r = (await web["a1"].post("/api/learn/svar", json={"id": valg["id"], "chosen": [right], "confidence": 2})).json()
    assert r["correct"] and r["explanation"] == "Forklaring"
    wrong = next(c["id"] for c in valg["choices"] if c["text"] != "Det man så")
    assert not (await web["a1"].post("/api/learn/svar", json={"id": valg["id"], "chosen": [wrong]})).json()["correct"]
    t = qs["Navnet på en sangfugl?"]
    assert (await web["a1"].post("/api/learn/svar", json={"id": t["id"], "text": "Solsorten"})).json()["correct"]  # fuzzy
    assert not (await web["a1"].post("/api/learn/svar", json={"id": t["id"], "text": "Ravn"})).json()["correct"]
    o = qs["Sorter efter størrelse"]
    assert sorted(i["id"] for i in o["items"]) == [0, 1, 2]
    assert (await web["a1"].post("/api/learn/svar", json={"id": o["id"], "order": [0, 1, 2]})).json()["correct"]
    assert not (await web["a1"].post("/api/learn/svar", json={"id": o["id"], "order": [2, 1, 0]})).json()["correct"]
    async with db() as s:
        n, k = (await s.execute(text("SELECT count(*), count(*) FILTER (WHERE confidence = 2) FROM lr_answers WHERE user_id = 1"))).one()
        assert n == 6 and k == 1
    done = (await web["a1"].post(f"/api/learn/units/{uid}/faerdig", json={"score": 2, "total": 3})).json()
    assert done["best"] == [2, 3]


async def test_synlighed_og_snapshot(web, db):  # noqa: F811
    from portal.models import Course
    from portal.services import learning

    await seed_course(db, published=True, visibility="login")
    async with db() as s:
        await learning.migrate_v2(s)
    await flags(db, laering_portal="on")
    assert (await web["a1"].get("/api/learn/courses/k1/units")).status_code == 200
    async with db() as s:
        await s.execute(text("UPDATE courses SET visibility = 'hold' WHERE slug = 'k1'"))
        await s.commit()
    assert (await web["a1"].get("/api/learn/courses/k1/units")).status_code == 404  # kun for medlemmer af et hold
    assert (await web["ad"].get("/api/learn/courses/k1/units")).status_code == 200  # redaktører ser alt
    async with db() as s:
        await s.execute(text("UPDATE courses SET visibility = 'login' WHERE slug = 'k1'"))
        await s.commit()
    # rettelse efter publicering påvirker ikke brugerne, før der publiceres igen
    async with db() as s:
        await s.execute(text("UPDATE questions SET prompt = 'ÆNDRET' WHERE prompt LIKE 'Hvad er%'"))
        await s.commit()
    uid = (await web["a1"].get("/api/learn/courses/k1/units")).json()["units"][0]["id"]
    prompts = [q["prompt"] for q in (await web["a1"].get(f"/api/learn/units/{uid}/session")).json()["questions"]]
    assert "ÆNDRET" not in prompts
    async with db() as s:
        c = (await s.execute(text("SELECT id FROM courses WHERE slug='k1'"))).scalar()
        assert await s.get(Course, c)
