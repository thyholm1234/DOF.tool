"""Læring 2.0, fase 4: kursusbyggeren – redigering, advarsler, publicering/låst kopi, import/eksport og rettigheder (kræver ARTICLES_TEST_DSN)."""

import pytest
from sqlalchemy import text

from test_learn_play import DSN, db, flags, web  # noqa: F401 – fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


async def give(db, uid, perms):  # noqa: F811
    from portal.models import User

    async with db() as s:
        (await s.get(User, uid)).permissions = perms
        await s.commit()


async def setup(web, db):  # noqa: F811
    await flags(db, laering_portal="on", laering_byg="on")
    await give(db, 1, ["laering_undervis"])  # a1 = underviser
    await give(db, 2, ["laering_undervis"])  # a2 = anden underviser


def ch(*items):
    return [{"text": t, "correct": c} for t, c in items]


async def test_bygger_kraever_rettighed_og_flag(web, db):  # noqa: F811
    await flags(db, laering_portal="on", laering_byg="off")
    assert (await web["ad"].get("/api/learn/byg/oversigt")).status_code == 404
    await flags(db, laering_byg="on")
    assert (await web["a2"].get("/api/learn/byg/oversigt")).status_code == 404  # ingen rettighed
    await give(db, 1, ["laering_undervis"])
    assert (await web["a1"].get("/api/learn/byg/oversigt")).status_code == 200


async def test_byg_publicer_og_laast_kopi(web, db):  # noqa: F811
    await setup(web, db)
    t = web["a1"]
    slug = (await t.post("/api/learn/byg/kurser", json={"title": "Mit kursus"})).json()["slug"]
    full = (await t.get(f"/api/learn/byg/kurser/{slug}")).json()
    uid = full["units"][0]["id"]
    assert full["course"]["visibility"] == "hold" and full["changed"]
    # tomt kursus: publicering blokeres af fejl (ingen aktiviteter er ok, men ingen titel-fejl) – tilføj en fejlbehæftet aktivitet
    aid = (await t.post(f"/api/learn/byg/kurser/{slug}/aktiviteter", json={"unit_id": uid, "type": "valg", "prompt": "Hvad?", "choices": ch(("A", False), ("B", False))})).json()["id"]
    r = await t.post(f"/api/learn/byg/kurser/{slug}/publicer")
    assert r.status_code == 400 and any("Intet svar" in w["msg"] for w in r.json()["detail"]["warnings"])
    await t.put(f"/api/learn/byg/kurser/{slug}/aktiviteter/{aid}", json={"unit_id": uid, "type": "valg", "prompt": "Hvad?", "choices": ch(("A", True), ("B", False)), "explanation": "Fordi."})
    await t.put(f"/api/learn/byg/kurser/{slug}/enheder/{uid}", json={"title": "Start", "lesson_md": "Hej **verden**"})
    r = await t.post(f"/api/learn/byg/kurser/{slug}/publicer")
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["visibility"] == "hold"
    # kun holdets medlemmer ser kurset (visibility=hold): ingen hold endnu → andre ser det ikke
    assert (await web["a2"].get(f"/api/learn/courses/{slug}/units")).status_code == 404
    assert (await t.get(f"/api/learn/courses/{slug}/units")).status_code == 200
    await db_set(db, f"UPDATE courses SET visibility = 'login' WHERE slug = '{slug}'")
    u = (await web["a2"].get(f"/api/learn/courses/{slug}/units")).json()
    assert u["units"][0]["title"] == "Start" and "<strong>verden</strong>" in u["units"][0]["html"]
    # ændring efter publicering ses ikke af brugerne, før der publiceres igen
    await t.put(f"/api/learn/byg/kurser/{slug}/aktiviteter/{aid}", json={"unit_id": uid, "type": "valg", "prompt": "ÆNDRET", "choices": ch(("A", True), ("B", False))})
    assert (await t.get(f"/api/learn/byg/kurser/{slug}")).json()["changed"]
    sess = (await web["a2"].get(f"/api/learn/units/{uid}/session")).json()
    assert sess["questions"][0]["prompt"] == "Hvad?"
    pv = (await t.get(f"/api/learn/units/{uid}/session?preview=true")).json()
    assert pv["questions"][0]["prompt"] == "ÆNDRET"
    # fortryd lægger den låste kopi tilbage
    assert (await t.post(f"/api/learn/byg/kurser/{slug}/fortryd")).status_code == 200
    full = (await t.get(f"/api/learn/byg/kurser/{slug}")).json()
    assert full["units"][0]["acts"][0]["prompt"] == "Hvad?" and not full["changed"]


async def db_set(db, sql):  # noqa: F811
    async with db() as s:
        await s.execute(text(sql))
        await s.commit()


async def test_rettigheder_og_optimistisk_laas(web, db):  # noqa: F811
    await setup(web, db)
    slug = (await web["a1"].post("/api/learn/byg/kurser", json={"title": "Privat"})).json()["slug"]
    assert (await web["a2"].get(f"/api/learn/byg/kurser/{slug}")).status_code == 403
    assert slug not in [c["slug"] for c in (await web["a2"].get("/api/learn/byg/oversigt")).json()]
    assert slug in [c["slug"] for c in (await web["ad"].get("/api/learn/byg/oversigt")).json()]  # redaktører ser alt
    # medforfatter
    assert (await web["a1"].post(f"/api/learn/byg/kurser/{slug}/medforfattere", json={"user_id": 2})).status_code == 200
    assert (await web["a2"].get(f"/api/learn/byg/kurser/{slug}")).status_code == 200
    # lås
    full = (await web["a1"].get(f"/api/learn/byg/kurser/{slug}")).json()
    stamp = full["course"]["updated_at"]
    assert (await web["a1"].put(f"/api/learn/byg/kurser/{slug}", json={"stamp": stamp, "title": "Nyt navn"})).status_code == 200
    r = await web["a2"].put(f"/api/learn/byg/kurser/{slug}", json={"stamp": stamp, "title": "Andet navn"})
    assert r.status_code == 409
    # undervisere kan ikke fremhæve eller gøre synlig for alle
    await web["a1"].put(f"/api/learn/byg/kurser/{slug}", json={"featured": True, "visibility": "alle"})
    c = (await web["a1"].get(f"/api/learn/byg/kurser/{slug}")).json()["course"]
    assert c["featured"] is False and c["visibility"] == "hold"
    # ugyldige felter afvises
    assert (await web["a1"].put(f"/api/learn/byg/kurser/{slug}", json={"hue": "pink"})).status_code == 400
    assert (await web["a1"].post(f"/api/learn/byg/kurser/{slug}/aktiviteter", json={"type": "findes_ikke"})).status_code == 400
    assert (await web["a1"].post(f"/api/learn/byg/kurser/{slug}/aktiviteter", json={"type": "valg", "media_url": "http://x.dk/a.jpg"})).status_code == 400


async def test_orden_og_flyt(web, db):  # noqa: F811
    await setup(web, db)
    t = web["a1"]
    slug = (await t.post("/api/learn/byg/kurser", json={"title": "Orden"})).json()["slug"]
    u1 = (await t.get(f"/api/learn/byg/kurser/{slug}")).json()["units"][0]["id"]
    u2 = (await t.post(f"/api/learn/byg/kurser/{slug}/enheder", json={"title": "To"})).json()["id"]
    a = [(await t.post(f"/api/learn/byg/kurser/{slug}/aktiviteter", json={"unit_id": u1, "type": "refleksion", "prompt": f"Q{i}"})).json()["id"] for i in range(3)]
    r = await t.post(f"/api/learn/byg/kurser/{slug}/orden", json={"units": [u2, u1], "acts": {str(u1): [a[2], a[0]], str(u2): [a[1]]}})
    assert r.status_code == 200
    full = (await t.get(f"/api/learn/byg/kurser/{slug}")).json()
    assert [u["id"] for u in full["units"]] == [u2, u1]
    assert [x["prompt"] for x in full["units"][0]["acts"]] == ["Q1"] and [x["prompt"] for x in full["units"][1]["acts"]] == ["Q2", "Q0"]


async def test_import_eksport_og_traekrute_format(web, db):  # noqa: F811
    await setup(web, db)
    traekrute = {"courses": [{"slug": "iagttag", "title": "Iagttag", "stage": "Grundlæggende", "skills": ["Bemærk", "Ugyldig"], "hue": "sø", "sil": "rovfugl", "number": 1,
                              "units": [{"title": "Gør en iagttagelse", "lesson": "Tekst", "questions": [
                                  {"type": "valg", "prompt": "Q1", "choices": [{"text": "a", "correct": True}, {"text": "b", "correct": False}], "explanation": "e", "media": {"kind": "silhouette", "value": "hejre"}},
                                  {"type": "tekst", "prompt": "Q2", "accept": ["solsort"], "choices": [], "explanation": ""}]}]}],
                 "drills": [{"slug": "glimt", "kind": "ovelse", "category": "Opdagelse", "title": "Glimt", "summary": "s", "short": "kort", "skills": ["Bemærk"], "hue": "eng", "sil": "svale",
                             "questions": [{"type": "valg", "prompt": "D1", "choices": [{"text": "x", "correct": True}, {"text": "y", "correct": False}], "explanation": ""}]}]}
    r = (await web["a1"].post("/api/learn/byg/import", json={"data": traekrute, "dry_run": True})).json()["rapport"]
    assert [(x["slug"], x["status"], x["n_acts"]) for x in r] == [("iagttag", "ny", 2), ("glimt", "ny", 1)]
    assert (await web["a1"].get("/api/learn/byg/oversigt")).json() == []  # tørkørsel gemmer intet
    await web["a1"].post("/api/learn/byg/import", json={"data": traekrute})
    ov = {c["slug"]: c for c in (await web["a1"].get("/api/learn/byg/oversigt")).json()}
    assert set(ov) == {"iagttag", "glimt"} and not ov["iagttag"]["published"] and ov["glimt"]["kind"] == "ovelse"
    full = (await web["a1"].get("/api/learn/byg/kurser/iagttag")).json()
    acts = full["units"][0]["acts"]
    assert [a["type"] for a in acts] == ["silhuet", "tekst"] and acts[0]["config"]["sil"] == "hejre" and acts[1]["accept"] == ["solsort"]
    assert full["course"]["skills"] == ["Bemærk"] and full["course"]["stage"] == "Grundlæggende"
    # øvelsen har ingen lektioner, kun løse aktiviteter
    g = (await web["a1"].get("/api/learn/byg/kurser/glimt")).json()
    assert [u["id"] for u in g["units"]] == [None] and len(g["units"][0]["acts"]) == 1
    # eksport → import (kopi) giver samme indhold igen
    exp = (await web["a1"].get("/api/learn/byg/eksport?slug=iagttag,glimt")).json()
    assert exp["format"] == "laering-v2" and len(exp["courses"]) == 2
    r = (await web["a1"].post("/api/learn/byg/import", json={"data": exp})).json()["rapport"]
    assert {x["status"] for x in r} == {"kopi"}
    assert len((await web["a1"].get("/api/learn/byg/oversigt")).json()) == 4
    # gammelt DOF.tool-format
    old = {"courses": [{"slug": "gammel", "title": "Gammel", "questions": [{"prompt": "P", "choices": [{"text": "a", "correct": True}, {"text": "b"}], "explanation": ""}]}]}
    assert (await web["a1"].post("/api/learn/byg/import", json={"data": old})).status_code == 200
    # en underviser kan ikke overskrive en andens kursus
    r = await web["a2"].post("/api/learn/byg/import", json={"data": traekrute, "mode": "overskriv"})
    assert r.status_code == 403


async def test_traekrute_startindhold_som_kladder(web, db):  # noqa: F811
    from portal.services import learning

    async with db() as s:
        n = await learning.seed_traekrute(s)
    assert n == 11  # 1 kursus + 10 øvelser (de fire indbyggede funktioner og de fem metodekurser, som «Se på fugle» afløser, importeres ikke)
    async with db() as s:
        assert await learning.seed_traekrute(s) == 0  # kun én gang
        rows = (await s.execute(text("SELECT slug, kind, published, (SELECT count(*) FROM lr_units u WHERE u.course_id = c.id) AS u, (SELECT count(*) FROM questions q WHERE q.course_id = c.id) AS q FROM courses c ORDER BY slug"))).mappings().all()
        assert len(rows) == 11 and not any(r["published"] for r in rows)
        by = {r["slug"]: r for r in rows}
        assert by["selvstaendigt-feltarbejde"]["u"] == 3 and by["selvstaendigt-feltarbejde"]["q"] == 3 and by["glimt"]["u"] == 0
        assert "iagttag-foer-du-navngiver" not in by
        assert "fugl-for-fugl" not in by and "mine-fejl" not in by
        gen = (await s.execute(text("SELECT c.slug, q.type FROM questions q JOIN courses c ON c.id = q.course_id WHERE q.type LIKE 'obs_%' ORDER BY 1"))).all()
        assert gen == [("habitatdetektiv", "obs_habitat"), ("observationsodds", "obs_odds"), ("saesonskift", "obs_saeson")]
        paths = {r[0]: r[1] for r in (await s.execute(text("SELECT slug, (SELECT count(*) FROM lr_path_items i WHERE i.path_id = p.id) FROM lr_paths p"))).all()}
        assert paths == {"fra-nysgerrig-til-feltbisse": 6, "laer-50-almindelige-fugle": 10, "vadefugle-i-traekket": 4}  # uden pakken mangler «Se på fugle»
    # kladdekurserne har ingen fejl (kun advarsler om manglende forklaring m.m.), så de kan publiceres af en redaktør
    await flags(db, laering_portal="on", laering_byg="on", laering_forloeb="on")
    ov = {c["slug"]: c for c in (await web["ad"].get("/api/learn/byg/oversigt")).json()}
    assert ov["selvstaendigt-feltarbejde"]["fejl"] == 0 and ov["habitatdetektiv"]["fejl"] == 0
    assert (await web["ad"].post("/api/learn/byg/kurser/selvstaendigt-feltarbejde/publicer")).status_code == 200
    # forløbet kan ikke publiceres, før alle kurserne er det
    r = await web["ad"].post("/api/learn/byg/forloeb/fra-nysgerrig-til-feltbisse/publicer")
    assert r.status_code == 400 and "ikke publiceret" in r.json()["detail"]["warnings"][0]["msg"]
