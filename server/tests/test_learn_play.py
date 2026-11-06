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
    k1 = (await web["a1"].get("/api/learn/courses/k1/units")).json()
    uid0 = k1["units"][0]["id"]
    ind = await web["a1"].get(f"/api/learn/units/{uid0}/indhold")  # lektionens indhold hentes for sig (og caches for den publicerede version)
    assert ind.status_code == 200 and set(ind.json()) == {"html", "blocks"}
    assert (await web["a1"].get(f"/api/learn/units/{uid0}/indhold")).json() == ind.json()
    assert (await web["a1"].get("/api/learn/units/999999/indhold")).status_code == 404
    async with db() as s:
        await s.execute(text("UPDATE courses SET visibility = 'hold' WHERE slug = 'k1'"))
        await s.commit()
    assert (await web["a1"].get("/api/learn/courses/k1/units")).status_code == 404  # kun for medlemmer af et hold
    assert (await web["a1"].get(f"/api/learn/units/{uid0}/indhold")).status_code == 404
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


# ---------------------------------------------------------------- fase 2: generatorer

async def seed_obs(db, month):  # noqa: F811
    """40 ture på lokalitet 100 i en måned (de seneste 4 år): art 1 ses på 30, art 3 kun som hemmelig observation."""
    from datetime import date

    from portal import cache

    from portal.models import Trip

    this = date.today().year
    async with db() as s:
        await (await s.connection()).run_sync(lambda c: Trip.__table__.create(c, checkfirst=True))
        await s.execute(text("DELETE FROM trips"))
        await s.execute(text("DELETE FROM observations WHERE obsid >= 9000"))
        await s.execute(text("INSERT INTO species (artnr, navn, latin, sortering, art_type, list_artnr, klass, excluded) VALUES "
                             "(3, 'Gråand', 'Anas platyrhynchos', 3, 'art', 3, 'Alm', false), (5, 'Sølvmåge', 'Larus argentatus', 5, 'art', 5, 'Alm', false), "
                             "(6, 'Rødhals', 'Erithacus rubecula', 6, 'art', 6, 'Alm', false), (7, 'Musvit', 'Parus major', 7, 'art', 7, 'Alm', false) ON CONFLICT DO NOTHING"))
        await s.execute(text("UPDATE species SET list_artnr = artnr WHERE artnr IN (1, 2)"))
        i = 0
        for year in range(this - 4, this):
            for d in range(10):
                i += 1
                await s.execute(text("INSERT INTO trips (turid, dato, loknr, obserkode) VALUES (:t, :d, 100, 'LB')"), {"t": 8000 + i, "d": date(year, month, 10 + d)})
                if i <= 30:
                    await s.execute(text("INSERT INTO observations (obsid, turid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger) VALUES (:o, :t, :d, 1, 1, 100, 'LB', 0, 'alm', '{}')"),
                                    {"o": 9000 + i, "t": 8000 + i, "d": date(year, month, 10 + d)})
                await s.execute(text("INSERT INTO observations (obsid, turid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger) VALUES (:o, :t, :d, 3, 1, 100, 'LB', 1, 'alm', '{}')"),
                                {"o": 9500 + i, "t": 8000 + i, "d": date(year, month, 10 + d)})
        await s.commit()
    await cache.set_json("pheno:weekly", {str(a): {"navn": n, "rr": [0.25] * 52} for a, n in [(1, "Dværgterne"), (3, "Gråand"), (5, "Sølvmåge"), (6, "Rødhals"), (7, "Musvit")]})


async def test_lokalitetsspoergsmaal_ignorerer_hemmelige(db, monkeypatch):  # noqa: F811
    import random
    from datetime import date

    from portal.services import learn_gen

    monkeypatch.setattr(learn_gen, "ObsSession", db)
    await seed_obs(db, date.today().month)
    q = await learn_gen.loc_question("obs_habitat", {"loknr": 100, "maaned": date.today().month, "n_valg": 3}, random.Random(1))
    assert q["correct"] == "Dværgterne" and len(q["options"]) == 3
    # Gråand er kun set som hemmelig observation: den kan aldrig være det rigtige svar, men må gerne være en forkert mulighed
    for seed in range(8):
        q = await learn_gen.loc_question("obs_habitat", {"loknr": 100, "maaned": date.today().month, "n_valg": 4}, random.Random(seed))
        assert q["correct"] == "Dværgterne" and len(set(q["options"])) == 4
        assert "30 af 40" not in q["explanation"] and "75 %" in q["explanation"] and "40 ture" in q["explanation"]
    # for få ture → fejl
    with pytest.raises(learn_gen.GenError):
        await learn_gen.loc_question("obs_habitat", {"loknr": 100, "maaned": (date.today().month % 12) + 1}, random.Random(1))


async def test_generator_i_kursus_uden_facit_til_klienten(web, db, monkeypatch):  # noqa: F811
    from datetime import date

    from portal.models import Course, Question, Unit
    from portal.services import learn_gen, learning

    monkeypatch.setattr(learn_gen, "ObsSession", db)
    await seed_obs(db, date.today().month)
    async with db() as s:
        c = Course(slug="data", title="Data", kind="kursus", published=True)
        s.add(c)
        await s.flush()
        u = Unit(course_id=c.id, position=0, title="L1")
        s.add(u)
        await s.flush()
        s.add(Question(course_id=c.id, unit_id=u.id, position=0, type="obs_habitat", prompt="", choices=[], config={"loknr": 100, "maaned": date.today().month, "antal": 2}))
        s.add(Question(course_id=c.id, unit_id=u.id, position=1, type="foto_art", prompt="", choices=[], config={"gruppe": "drosler"}))
        await s.commit()
        uid = u.id
        await learning.migrate_v2(s)
    await flags(db, laering_portal="on", artsquiz="off")
    res = (await web["ad"].get(f"/api/learn/units/{uid}/session")).json()
    qs = res["questions"]
    assert len(qs) == 3
    obs = [q for q in qs if q["id"].startswith("lr:")]
    assert len(obs) == 2 and all("correct" not in q and "map" in q for q in obs)
    assert [q for q in qs if q.get("locked")][0]["locked"] == "Fotoquizzen er ikke slået til"
    q = obs[0]
    right = next(c["id"] for c in q["choices"] if c["text"] == "Dværgterne")
    r = (await web["ad"].post("/api/learn/svar", json={"id": q["id"], "chosen": [right], "confidence": 2})).json()
    assert r["correct"] and "Dværgterne" in r["explanation"]
    assert (await web["ad"].post("/api/learn/svar", json={"id": q["id"], "chosen": [right]})).json()["correct"]  # token kan genbruges i TTL
    wrong = next(c["id"] for c in obs[1]["choices"] if c["text"] != "Dværgterne")
    assert not (await web["ad"].post("/api/learn/svar", json={"id": obs[1]["id"], "chosen": [wrong]})).json()["correct"]
    assert (await web["ad"].post("/api/learn/svar", json={"id": "lr:findesikke", "chosen": [0]})).status_code == 410
    # uden data-flag låses datagenerator
    await flags(db, laering_data="off")
    qs = (await web["ad"].get(f"/api/learn/units/{uid}/session")).json()["questions"]
    assert all(q.get("locked") for q in qs)


# ---------------------------------------------------------------- fase 3: træning, repetition, forbered en tur

async def test_forbered_en_tur(web, db, monkeypatch):  # noqa: F811
    from datetime import date

    from portal.services import learn_gen

    monkeypatch.setattr(learn_gen, "ObsSession", db)
    m = date.today().month
    await seed_obs(db, m)
    await flags(db, laering_portal="on", laering_data="on")
    d = (await web["a1"].get(f"/api/learn/forbered?loknr=100&dato={date.today().year}-{m:02d}-15")).json()
    assert d["ture"] == 40 and d["sted"]["navn"] == "Tisvildekysten"
    sikker = [a["navn"] for g in d["grupper"] if g["key"] == "sikker" for a in g["arter"]]
    assert sikker == ["Dværgterne"]
    assert "Gråand" not in [a["navn"] for g in d["grupper"] for a in g["arter"]]  # kun set som hemmelig observation
    # for få ture → hele landet
    d2 = (await web["a1"].get(f"/api/learn/forbered?loknr=101&dato={date.today().year}-{m:02d}-15")).json()
    assert "hele landet" in d2["note"] and d2["ture"] == 0
    await flags(db, laering_data="off")
    assert (await web["a1"].get("/api/learn/forbered")).status_code == 404


async def test_gemte_traeninger_og_deling(web, db):  # noqa: F811
    await flags(db, laering_portal="on")
    r = await web["a1"].post("/api/learn/traeninger", json={"title": "Mine drosler", "kilde": "gruppe", "gruppe": "drosler", "m": "lyd", "metode": "tekst", "n": 10})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    lst = (await web["a1"].get("/api/learn/traeninger")).json()
    assert [t["title"] for t in lst] == ["Mine drosler"] and lst[0]["config"]["m"] == "lyd"
    assert (await web["a2"].get("/api/learn/traeninger")).json() == []
    assert (await web["a2"].post(f"/api/learn/traeninger/{tid}/del")).status_code == 404  # kun ejeren kan dele
    tok = (await web["a1"].post(f"/api/learn/traeninger/{tid}/del")).json()["token"]
    assert (await web["a1"].post(f"/api/learn/traeninger/{tid}/del")).json()["token"] == tok
    got = (await web["a2"].get(f"/api/learn/traeninger/delt/{tok}")).json()
    assert got["title"] == "Mine drosler" and got["config"]["gruppe"] == "drosler"
    assert (await web["a2"].delete(f"/api/learn/traeninger/{tid}")).status_code == 404
    assert (await web["a1"].delete(f"/api/learn/traeninger/{tid}")).status_code == 200
    assert (await web["a2"].get(f"/api/learn/traeninger/delt/{tok}")).status_code == 404


async def test_traening_kraever_flag_og_repetition(web, db):  # noqa: F811
    from portal.services import learning

    await flags(db, laering_portal="on")
    r = await web["a1"].post("/api/learn/traen", json={"kilde": "gruppe", "gruppe": "drosler", "m": "foto"})
    assert r.status_code == 409 and "ikke slået til" in r.json()["detail"]
    await seed_course(db)
    async with db() as s:
        await learning.migrate_v2(s)
        await s.execute(text("INSERT INTO question_progress (user_id, question_id, box, correct, wrong, due_at) SELECT 1, id, 0, 0, 1, now() - interval '1 hour' FROM questions WHERE type = 'valg'"))
        await s.commit()
    d = (await web["a1"].get("/api/learn/repetition")).json()
    assert d["antal"]["spoergsmaal"] == 1 and len(d["questions"]) == 1 and "correct" not in str(d)
    assert (await web["a2"].get("/api/learn/repetition")).json()["questions"] == []


# ---------------------------------------------------------------- fase 7: fremgang, mål, dagens art, færdighedstest

async def test_fremgang_og_maal(web, db):  # noqa: F811
    from datetime import datetime, timezone

    from portal.models import LrAnswer, LrSpeciesMastery

    await flags(db, laering_portal="on")
    now = datetime.now(timezone.utc)
    async with db() as s:
        for i in range(12):  # høj sikkerhed, men kun halvdelen rigtige → dårligt kalibreret
            s.add(LrAnswer(user_id=1, source="kursus", modality="tekst", correct=i % 2 == 0, confidence=2, skills=["Bemærk"], created_at=now))
        for i in range(4):
            s.add(LrAnswer(user_id=1, source="lyd", artnr=1, modality="lyd", correct=True, confidence=0, skills=["Lyt", "Genkald"], created_at=now))
        s.add(LrSpeciesMastery(user_id=1, artnr=1, modality="lyd", box=5, correct=5, wrong=0, due_at=now))
        s.add(LrSpeciesMastery(user_id=1, artnr=2, modality="lyd", box=1, correct=1, wrong=2, due_at=now))
        s.add(LrSpeciesMastery(user_id=1, artnr=1, modality="foto", box=2, correct=2, wrong=0, due_at=now))
        await s.commit()
    d = (await web["a1"].get("/api/learn/fremgang")).json()
    sk = {x["skill"]: x for x in d["faerdigheder"]}
    assert sk["Bemærk"]["n"] == 12 and sk["Bemærk"]["pct"] == 50 and sk["Lyt"]["pct"] == 100 and sk["Opdatér"]["pct"] is None
    cal = {x["niveau"]: x for x in d["kalibrering"]}
    assert cal["Høj"]["n"] == 12 and cal["Høj"]["pct"] == 50 and cal["Høj"]["godt"] is False and cal["Lav"]["godt"] is None  # for få svar til at dømme
    assert d["mestring"] == {"foto": {"set": 1, "mestret": 0}, "lyd": {"set": 2, "mestret": 1}} and d["streak"]["traenet_i_dag"] and d["streak"]["dage_i_traek"] == 1
    arter = {a["artnr"]: a for a in (await web["a1"].get("/api/learn/fremgang/arter")).json()}
    assert arter[1]["lyd"]["box"] == 5 and arter[1]["foto"]["box"] == 2 and arter[2]["foto"] is None
    assert (await web["a2"].get("/api/learn/fremgang")).json()["mestring"]["lyd"]["set"] == 0  # privat
    # mål
    assert (await web["a1"].put("/api/learn/maal", json={"n_species": 40, "modality": "lyd", "deadline": "2027-04-01"})).status_code == 200
    g = (await web["a1"].get("/api/learn/fremgang")).json()["maal"]
    assert g["n_species"] == 40 and g["mestret"] == 1 and g["deadline"].startswith("2027-04-01")
    await web["a1"].put("/api/learn/maal", json={"n_species": None})
    assert (await web["a1"].get("/api/learn/fremgang")).json()["maal"] is None


async def test_dagens_art_afsloerer_en_ledetraad_ad_gangen(web, db, monkeypatch):  # noqa: F811
    from portal import cache
    from portal.services import learn_daily

    async def fixed(db_, day):
        return [1]

    monkeypatch.setattr(learn_daily, "candidates", fixed)
    await cache.set_json("pheno:weekly", {"1": {"navn": "Dværgterne", "rr": [0.2] * 52}})
    await flags(db, laering_portal="on", laering_data="on")
    c0 = (await web["a1"].get("/api/learn/dagens?trin=0")).json()
    assert c0["ledetraad"]["type"] == "faenologi" and len(c0["ledetraad"]["uger"]) == 52 and "Dværgterne" not in str(c0) and c0["point"] == 100
    c1 = (await web["a1"].get("/api/learn/dagens?trin=1")).json()
    assert c1["ledetraad"]["tekst"] == "Slægt: Sternula" and "Dværgterne" not in str(c1)
    r = (await web["a1"].post("/api/learn/dagens/gaet", json={"trin": 1, "text": "Ravn"})).json()
    assert not r["correct"] and r["art"] is None
    r = (await web["a1"].post("/api/learn/dagens/gaet", json={"trin": 1, "text": "dværgterne"})).json()
    assert r["correct"] and r["point"] == 70 and r["art"]["navn"] == "Dværgterne"
    async with db() as s:
        assert (await s.execute(text("SELECT count(*) FROM lr_answers WHERE user_id = 1 AND artnr = 1"))).scalar() == 1  # kun det afsluttende svar logges
    await flags(db, laering_data="off")
    assert (await web["a1"].get("/api/learn/dagens")).status_code == 404


async def test_faerdighedstest_bruger_publicerede_aktiviteter(web, db):  # noqa: F811
    from portal.services import learning

    await flags(db, laering_portal="on")
    assert (await web["a1"].get("/api/learn/faerdighedstest")).json()["questions"] == []
    await seed_course(db)
    async with db() as s:
        await learning.migrate_v2(s)
    qs = (await web["a1"].get("/api/learn/faerdighedstest")).json()["questions"]
    assert len(qs) == 2 and all(q["skills"] == ["Bemærk"] for q in qs) and "correct" not in str(qs)


async def test_gdpr_udtraek_og_sletning_dækker_læring(web, db):  # noqa: F811
    from portal.models import LrAnswer, LrGoal, LrTraining, User
    from portal.services import account

    async with db() as s:
        s.add(LrAnswer(user_id=2, source="kursus", modality="tekst", correct=True, skills=["Bemærk"]))
        s.add(LrTraining(user_id=2, title="Min", config={"m": "foto"}))
        s.add(LrGoal(user_id=2, n_species=10))
        await s.commit()
        u = await s.get(User, 2)
        data = (await account.export(s, u))["data"]
        assert {"lr_answers", "lr_trainings", "lr_goals"} <= set(data)
        await account.delete(s, u)
    async with db() as s:
        for t in ("lr_answers", "lr_trainings", "lr_goals"):
            assert (await s.execute(text(f"SELECT count(*) FROM {t} WHERE user_id = 2"))).scalar() == 0
