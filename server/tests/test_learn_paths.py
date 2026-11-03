"""Læring 2.0, fase 5–6: forløb, oplåsning, hold, feltopgaver og beviser (kræver ARTICLES_TEST_DSN)."""

import pytest
from sqlalchemy import text

from test_learn_build import db_set, give  # noqa: F401
from test_learn_play import DSN, db, flags, web  # noqa: F401 – fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


async def setup(web, db, monkeypatch=None):  # noqa: F811
    from portal.services import learn_paths

    if monkeypatch:
        monkeypatch.setattr(learn_paths, "ObsSession", db)
    await flags(db, laering_portal="on", laering_byg="on", laering_forloeb="on", laering_felt="on")
    await give(db, 1, ["laering_undervis"])  # a1 = underviser, a2 = deltager, ad = admin
    await db_set(db, "UPDATE species SET list_artnr = artnr")


async def published_course(db, slug="k1", kind="kursus", n=2):  # noqa: F811
    from portal.models import Course, Question, Unit

    async with db() as s:
        c = Course(slug=slug, title=slug.upper(), kind=kind, published=True, visibility="alle")
        s.add(c)
        await s.flush()
        u = Unit(course_id=c.id, position=0, title="L")
        s.add(u)
        await s.flush()
        for i in range(n):
            s.add(Question(course_id=c.id, unit_id=u.id if kind == "kursus" else None, position=i, prompt=f"Q{i}", choices=[{"text": "a", "correct": True}, {"text": "b", "correct": False}], explanation="e"))
        await s.commit()
        uid = u.id
    from portal.services import learning

    async with db() as s:
        await learning.migrate_v2(s)
        c = (await s.execute(text("SELECT id FROM courses WHERE slug = :s"), {"s": slug})).scalar()
        from portal.models import Course as C
        from portal.services import learn_play

        cc = await s.get(C, c)
        cc.published_snapshot = await learn_play.make_snapshot(s, cc)
        await s.commit()
    return uid


async def build_path(web, db):  # noqa: F811
    t = web["a1"]
    slug = (await t.post("/api/learn/byg/forloeb", json={"title": "Fra nysgerrig til feltbisse"})).json()["slug"]
    assert (await t.post(f"/api/learn/byg/forloeb/{slug}/publicer")).status_code == 400  # ingen punkter
    a = (await t.post(f"/api/learn/byg/forloeb/{slug}/punkter", json={"kind": "kursus", "course": "k1", "section": "Uge 1", "min_score_pct": 50})).json()["id"]
    b = (await t.post(f"/api/learn/byg/forloeb/{slug}/punkter", json={"kind": "tekst", "section": "Uge 1", "title": "Mødested", "text_md": "Fuglekiggerhuset", "required": False})).json()["id"]
    c = (await t.post(f"/api/learn/byg/forloeb/{slug}/punkter", json={"kind": "felt", "section": "Uge 2", "unlock": {"after": "previous"},
                                                                        "field_task": {"titel": "Find en dværgterne", "godkendelse": "auto", "regel": {"arter": [1], "min_arter": 1, "periode": {"fra": "2026-10-01"}}}})).json()["id"]
    await t.put(f"/api/learn/byg/forloeb/{slug}", json={"certificate": True, "visibility": "login"})
    return slug, a, b, c


async def test_forloeb_bygges_og_oplaases(web, db, monkeypatch):  # noqa: F811
    await setup(web, db, monkeypatch)
    uid = await published_course(db)
    slug, a, b, c = await build_path(web, db)
    # uudgivet kursus blokerer publicering
    await db_set(db, "UPDATE courses SET published = false WHERE slug = 'k1'")
    r = await web["a1"].post(f"/api/learn/byg/forloeb/{slug}/publicer")
    assert r.status_code == 400 and "ikke publiceret" in r.json()["detail"]["warnings"][0]["msg"]
    await db_set(db, "UPDATE courses SET published = true WHERE slug = 'k1'")
    assert (await web["a1"].post(f"/api/learn/byg/forloeb/{slug}/publicer")).status_code == 200
    # underviseres forløb tvinges til «hold» – a2 ser det ikke uden hold
    assert (await web["a2"].get(f"/api/learn/forloeb/{slug}")).status_code == 404
    await db_set(db, f"UPDATE lr_paths SET visibility = 'login' WHERE slug = '{slug}'")
    v = (await web["a2"].get(f"/api/learn/forloeb/{slug}")).json()
    assert [s["navn"] for s in v["sections"]] == ["Uge 1", "Uge 2"] and v["fremgang"] == {"done": 0, "total": 2}
    feltet = v["sections"][1]["punkter"][0]
    assert feltet["locked"] and feltet["why"] == "Klar det forrige punkt først"
    # a2 klarer kurset (lektionen med 100 %) → feltopgaven åbner
    assert (await web["a2"].post(f"/api/learn/forloeb/{slug}/tilmeld")).status_code == 200
    assert (await web["a2"].post(f"/api/learn/units/{uid}/faerdig", json={"score": 2, "total": 2})).status_code == 200
    v = (await web["a2"].get(f"/api/learn/forloeb/{slug}")).json()
    assert v["sections"][0]["punkter"][0]["done"] and not v["sections"][1]["punkter"][0]["locked"] and v["fremgang"] == {"done": 1, "total": 2}
    # feltopgave: a2 (obserkode A1) har observeret art 1 siden 1. oktober → afkrydses automatisk, og forløbet er færdigt med bevis
    r = (await web["a2"].post(f"/api/learn/forloeb/{slug}/punkter/{c}/tjek", json={})).json()
    assert r["status"] == "godkendt"
    v = (await web["a2"].get(f"/api/learn/forloeb/{slug}")).json()
    assert v["faerdig"] and v["bevis"].startswith("DT-")
    cert = (await web["a1"].get(f"/api/learn/bevis/{v['bevis']}")).json()
    assert cert["navn"] == "Anna Andersen" and cert["title"] == "Fra nysgerrig til feltbisse"
    assert (await web["a1"].get("/api/learn/bevis/DT-FINDESIKKE")).status_code == 404


async def test_feltopgave_kraever_observationer_og_ignorerer_andres(web, db, monkeypatch):  # noqa: F811
    await setup(web, db, monkeypatch)
    await published_course(db)
    slug, a, b, c = await build_path(web, db)
    await web["a1"].post(f"/api/learn/byg/forloeb/{slug}/publicer")
    await db_set(db, f"UPDATE lr_paths SET visibility = 'login' WHERE slug = '{slug}'")
    # a1 (obserkode LB) har også observeret art 1 – men en bruger uden observationer får «mangler»
    r = (await web["ad"].post(f"/api/learn/forloeb/{slug}/punkter/{c}/tjek", json={})).json()  # admin har ingen obserkode med fund
    assert r["status"] in ("mangler", "ingen_profil")
    # kun selv-markering for 'selv'-opgaver
    assert (await web["a2"].post(f"/api/learn/forloeb/{slug}/punkter/{c}/marker", json={})).status_code == 400
    assert (await web["a2"].post(f"/api/learn/forloeb/{slug}/punkter/{b}/marker", json={"note": "læst"})).status_code == 200
    # feltopgaver slået fra → 404
    await flags(db, laering_felt="off")
    assert (await web["a2"].post(f"/api/learn/forloeb/{slug}/punkter/{c}/tjek", json={})).status_code == 404


async def test_hold_underviser_matrix_og_godkendelse(web, db, monkeypatch):  # noqa: F811
    await setup(web, db, monkeypatch)
    uid = await published_course(db)
    slug, a, b, c = await build_path(web, db)
    # feltopgaven kræver underviserens godkendelse
    await web["a1"].put(f"/api/learn/byg/forloeb/{slug}/punkter/{c}", json={"kind": "felt", "section": "Uge 2", "unlock": {"after": "none"},
                                                                             "field_task": {"titel": "Find en dværgterne", "godkendelse": "underviser", "regel": {"arter": [1], "periode": {"fra": "2026-10-01"}}}})
    await web["a1"].post(f"/api/learn/byg/forloeb/{slug}/publicer")
    r = await web["a2"].post("/api/learn/hold", json={"path": slug, "title": "Mit hold"})
    assert r.status_code == 403  # kun undervisere
    h = (await web["a1"].post("/api/learn/hold", json={"path": slug, "title": "Efterår 2026", "open_join": True})).json()
    code = h["join_code"]
    assert len(code) == 6
    assert (await web["a2"].post("/api/learn/hold/tilmeld", json={"code": "ZZZZZZ"})).status_code == 404
    assert (await web["a2"].post("/api/learn/hold/tilmeld", json={"code": code.lower()})).status_code == 200
    # nu er forløbet synligt for a2 (hold), også selv om det er 'hold'-synligt
    await db_set(db, f"UPDATE lr_paths SET visibility = 'hold' WHERE slug = '{slug}'")
    assert (await web["a2"].get(f"/api/learn/forloeb/{slug}")).status_code == 200
    # deltager ser holdlisten med fornavne, men ingen matrix eller kode
    v = (await web["a2"].get(f"/api/learn/hold/{h['id']}")).json()
    assert not v["teacher"] and "matrix" not in v and "join_code" not in v and {m["navn"] for m in v["members"]} == {"Anna", "Leif"}
    await web["a2"].post(f"/api/learn/units/{uid}/faerdig", json={"score": 1, "total": 2})
    await web["a2"].post(f"/api/learn/forloeb/{slug}/punkter/{c}/tjek", json={"hold": h["id"]})
    t = (await web["a1"].get(f"/api/learn/hold/{h['id']}")).json()
    assert t["teacher"] and t["join_code"] == code
    row = t["matrix"]["deltagere"][0]
    assert row["navn"] == "Anna Andersen" and row["punkter"][str(a)]["done"] is True  # 50 % kræves, 1 af 2 = 50 %
    assert len(t["venter"]) == 1 and t["venter"][0]["navn"] == "Anna Andersen" and t["venter"][0]["obs"]["navn"] == "Dværgterne"
    # underviseren godkender → punktet er klaret
    assert (await web["a1"].post(f"/api/learn/hold/{h['id']}/felt/{c}/2", json={"godkend": True})).status_code == 200
    t = (await web["a1"].get(f"/api/learn/hold/{h['id']}")).json()
    assert t["matrix"]["deltagere"][0]["punkter"][str(c)]["done"] is True and t["venter"] == []
    assert (await web["a2"].post(f"/api/learn/hold/{h['id']}/felt/{c}/2", json={"godkend": True})).status_code == 403
    csv = (await web["a1"].get(f"/api/learn/hold/{h['id']}/fremgang.csv")).text
    assert csv.startswith("Deltager;") and "Anna Andersen" in csv
    assert (await web["a2"].get(f"/api/learn/hold/{h['id']}/fremgang.csv")).status_code == 403
    assert (await web["a1"].post(f"/api/learn/hold/{h['id']}/besked", json={"tekst": "Husk kikkerten"})).json()["sendt"] == 1
    # en udenforstående ser ikke holdet
    assert (await web["ad"].get(f"/api/learn/hold/{h['id']}")).status_code == 200  # admin har rettigheden «laering»
    assert (await web["a1"].post(f"/api/learn/hold/{h['id']}/forlad")).status_code == 409  # sidste underviser
