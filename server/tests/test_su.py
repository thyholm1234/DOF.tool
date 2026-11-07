"""SU-modulet: kladde → dokumentation → indsendelse, adgang, beskeder og statusskift, afgørelse til det åbne arkiv og
årsrapporter (kræver ARTICLES_TEST_DSN)."""

import pytest

from test_learn_play import DSN, db, flags, web  # noqa: F401 – web og db er fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")

CASE = {"up_navn": "Leif Bolding", "up_email": "leif@example.dk", "art": "Kattedrossel", "artnr": 901, "dato_fra": "2026-01-03",
        "loknr": 777, "lokalitet": "", "region": "NJ", "land": "Danmark", "antal": 2, "observatorer": "*Leif Bolding", "beskrivelse": ""}


async def unthrottle():
    """Beskeder er begrænset til én pr. 3 sekunder pr. bruger."""
    from portal.cache import kv

    for uid in (1, 2, 4):
        await kv().delete(f"su:msg:{uid}")


@pytest.fixture
async def su_art(db):  # noqa: F811
    """En SU-art (Kattedrossel) og en almindelig art i artslisten."""
    from sqlalchemy import text

    async with db() as s:
        await s.execute(text("""INSERT INTO species (artnr, navn, latin, sortering, art_type, list_artnr, klass, excluded) VALUES
            (901, 'Kattedrossel', 'Dumetella carolinensis', 901, 'art', 901, 'SU', false),
            (902, 'Solsort', 'Turdus merula', 902, 'art', 902, 'Alm', false) ON CONFLICT (artnr) DO UPDATE SET klass = EXCLUDED.klass"""))
        await s.execute(text("INSERT INTO locations (loknr, navn, origin) VALUES (777, 'Sorring', 'dofbasen') ON CONFLICT (loknr) DO NOTHING"))
        await s.commit()


@pytest.fixture
def sent(monkeypatch, su_art):
    from portal.services import su

    out = []

    async def notify(case_id, *, to, title, body, exclude=None):
        out.append((to, title))

    monkeypatch.setattr(su, "notify", notify)
    return out


async def test_su_sagsgang_arkiv_og_rapporter(web, db, sent):  # noqa: F811
    a1, a2, su = web["a1"], web["a2"], web["ad"]  # ad er administrator (rollen har admin-modulet su)
    await flags(db, su="off")
    assert (await a1.get("/api/su/status")).status_code == 404
    await flags(db, su="on")
    st = (await a1.get("/api/su/status")).json()
    assert st["member"] is False and "NJ" in st["regions"]
    assert (await su.get("/api/su/status")).json()["member"] is True

    # trin 1–2: kladde; kun SU-arter (dansk navn fra artslisten); ugyldige felter afvises
    assert [(a["artnr"], a["navn"]) for a in (await a1.get("/api/su/species")).json()] == [(901, "Kattedrossel")]
    assert (await a1.post("/api/su/cases", json={**CASE, "artnr": 902})).status_code == 400
    assert (await a1.post("/api/su/cases", json={**CASE, "artnr": None})).status_code == 400
    assert (await a1.post("/api/su/cases", json={**CASE, "loknr": None, "lokalitet": "Et sted"})).status_code == 400  # i DK: DOFbasens lokaliteter
    assert (await a1.post("/api/su/cases", json={**CASE, "loknr": 999999})).status_code == 400
    assert (await a1.post("/api/su/cases", json={**CASE, "region": "XX"})).status_code == 400
    assert (await a1.post("/api/su/cases", json={**CASE, "up_email": "ikke-en-mail"})).status_code == 422
    cid = (await a1.post("/api/su/cases", json=CASE)).json()["id"]
    assert (await a1.get(f"/api/su/cases/{cid}")).json()["lokalitet"] == "Sorring"  # navnet fra lokaliteten
    assert (await su.get(f"/api/su/cases/{cid}")).status_code == 404  # SU ser ikke kladder
    assert (await a1.post(f"/api/su/cases/{cid}/submit")).status_code == 400  # hverken beskrivelse eller dokument

    # trin 3: dokumentation – filtypen skal passe til kategorien
    bad = await a1.post(f"/api/su/cases/{cid}/files", data={"kind": "dokument"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400
    doc = (await a1.post(f"/api/su/cases/{cid}/files", data={"kind": "dokument"}, files={"file": ("beskrivelse.pdf", b"%PDF-1.4 test", "application/pdf")})).json()
    foto = (await a1.post(f"/api/su/cases/{cid}/files", data={"kind": "foto", "info": "Set fra dæmningen"},
                          files={"file": ("fugl.jpg", b"\xff\xd8\xff\xe0jpeg", "image/jpeg")})).json()
    assert foto["info"] == "Set fra dæmningen"
    r = await a1.get(doc["url"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "frame-ancestors" in r.headers["content-security-policy"]
    assert (await a2.get(doc["url"])).status_code == 404

    # trin 4: indsend → sagsnummer, SU får besked, beskrivelsen er låst
    sub = (await a1.post(f"/api/su/cases/{cid}/submit")).json()
    assert sub["sagsnr"].endswith("-001") and ("su", f"ny SU-beskrivelse ({sub['sagsnr']})") in sent
    assert (await a1.put(f"/api/su/cases/{cid}", json=CASE)).status_code == 400
    assert (await a1.delete(f"/api/su/cases/{cid}/files/{foto['id']}")).status_code == 403
    assert (await a2.get(f"/api/su/cases/{cid}")).status_code == 404  # andre ser ikke en åben sag
    assert any(c["id"] == cid and c["unread"] for c in (await su.get("/api/su/cases?view=su")).json())
    assert (await a1.get("/api/su/cases?view=su")).status_code == 403

    # beskeder: SU spørger → afventer; svar → behandles; interne noter ses ikke af indsenderen
    assert (await su.post(f"/api/su/cases/{cid}/actions", json={"action": "take"})).json()["status"] == "behandles"
    await unthrottle()
    assert (await su.post(f"/api/su/cases/{cid}/messages", json={"body": "Intern vurdering", "internal": True})).status_code == 200
    await unthrottle()
    assert (await su.post(f"/api/su/cases/{cid}/messages", json={"body": "Har du flere fotos?"})).json()["status"] == "afventer"
    assert (await a1.get("/api/su/status")).json()["unread"] == 1
    mine = (await a1.get(f"/api/su/cases/{cid}")).json()
    assert [m["body"] for m in mine["messages"] if m["role"] == "su"] == ["Har du flere fotos?"] and mine["messages"][-1]["author"] == "SU"
    assert (await a1.get("/api/su/status")).json()["unread"] == 0  # læst
    await unthrottle()
    assert (await a1.post(f"/api/su/cases/{cid}/messages", json={"body": "Ja, her", "internal": True})).status_code == 403
    await unthrottle()
    assert (await a1.post(f"/api/su/cases/{cid}/messages", json={"body": "Ja, her"})).json()["status"] == "behandles"
    sukopi = (await su.get(f"/api/su/cases/{cid}")).json()
    assert any(m["internal"] for m in sukopi["messages"]) and sukopi["uploader_info"]["up_email"] == "leif@example.dk"

    # afgørelse: godkendt → åbent arkiv for alle, uden kontaktoplysninger og beskeder
    assert (await su.post(f"/api/su/cases/{cid}/actions", json={"action": "close"})).status_code == 400
    assert (await su.post(f"/api/su/cases/{cid}/actions", json={"action": "close", "outcome": "godkendt", "note": "Fin beskrivelse"})).status_code == 200
    arkiv = (await a2.get("/api/su/archive")).json()
    assert [r["id"] for r in arkiv["rows"]] == [cid] and arkiv["years"] == [2026]
    assert (await a2.get("/api/su/archive?q=gråand")).json()["rows"] == []
    pub = (await a2.get(f"/api/su/cases/{cid}")).json()
    assert pub["role"] == "public" and "uploader_info" not in pub and "messages" not in pub and pub["outcome_note"] == "Fin beskrivelse"
    assert (await a2.get(doc["url"])).status_code == 200
    await unthrottle()
    assert (await a2.post(f"/api/su/cases/{cid}/messages", json={"body": "hej"})).status_code == 400

    # årsrapporter: kun SU uploader; kladder ses kun af SU, publicerede af alle
    pdf = {"file": ("SU_Rapport_2024.pdf", b"%PDF-1.7 rapport", "application/pdf")}
    assert (await a1.post("/api/su/reports", data={"label": "2024"}, files=pdf)).status_code == 403
    assert (await su.post("/api/su/reports", data={"label": "24"}, files=pdf)).status_code == 400
    assert (await su.post("/api/su/reports", data={"label": "2024"}, files={"file": ("x.pdf", b"ikke pdf", "application/pdf")})).status_code == 400
    rep = (await su.post("/api/su/reports", data={"label": "2024", "title": "Sjældne fugle i Danmark 2024"}, files=pdf)).json()
    old = (await su.post("/api/su/reports", data={"label": "1995-1996"}, files=pdf)).json()
    assert (await a1.get("/api/su/reports")).json() == [] and (await a1.get(rep["url"])).status_code == 404
    await su.patch(f"/api/su/reports/{rep['id']}", json={"published": True})
    await su.patch(f"/api/su/reports/{old['id']}", json={"published": True})
    assert [r["label"] for r in (await a1.get("/api/su/reports")).json()] == ["2024", "1995-1996"]
    assert (await a1.get(rep["url"])).content.startswith(b"%PDF")
    assert (await su.delete(f"/api/su/reports/{old['id']}")).status_code == 200


async def test_su_kladde_kan_afbrydes(web, db, sent):  # noqa: F811
    await flags(db, su="on")
    a1 = web["a1"]
    cid = (await a1.post("/api/su/cases", json={**CASE, "beskrivelse": "Tekst"})).json()["id"]
    assert (await a1.put(f"/api/su/cases/{cid}", json={**CASE, "antal": 3})).status_code == 200
    assert (await a1.get(f"/api/su/cases/{cid}")).json()["antal"] == 3
    assert (await a1.delete(f"/api/su/cases/{cid}")).status_code == 200
    assert (await a1.get(f"/api/su/cases/{cid}")).status_code == 404


async def test_su_godkendelse_af_fund(web, db, sent):  # noqa: F811
    from datetime import date

    from sqlalchemy import text

    from test_dagsliste import seed

    await seed(db)  # 7003 er en SU-art (LB, lokalitet 100), 7004 en andens hemmelige SU-observation samme sted
    async with db() as s:
        from portal.models import DkuCase

        await (await s.connection()).run_sync(lambda c: DkuCase.__table__.create(c, checkfirst=True))
        await s.execute(text("DELETE FROM su_obs_approvals"))
        # endnu en indtastning af samme fugl samme sted og dag: samme fund
        await s.execute(text("""INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger)
            VALUES (7005, :d, 5, 1, 100, 'A1', 0, 'SU', '{}') ON CONFLICT DO NOTHING"""), {"d": date.today()})
        await s.commit()
    await flags(db, su="on", dagsliste="on")
    a1, su = web["a1"], web["ad"]
    today = date.today().isoformat()
    key = f"{today}/5/100"
    assert (await a1.get("/api/su/obs")).status_code == 403
    q = (await su.get(f"/api/su/obs?aar={date.today().year}")).json()
    fund = [r for r in q["rows"] if r["key"] == key]
    assert len(fund) == 1 and fund[0]["n_obs"] == 2  # to indtastninger (den hemmelige tæller ikke) = ét fund

    def rows(d, *ids):
        return [r for r in d["rows"] if r["obsid"] in ids]

    liste = lambda: a1.get("/api/dagsliste?visning=allfugle&summering=ingen")  # noqa: E731
    assert not any(r["su_ok"] for r in rows((await liste()).json(), 7003, 7005))  # intet mærke før godkendelse
    # en indsendt SU-beskrivelse af arten på dagen vises ved fundet (lokaliteten er fri tekst i formularen)
    async with db() as s_:
        await s_.execute(text("""INSERT INTO su_cases (sagsnr, user_id, artnr, art, dato_fra, dato_til, lokalitet, region, land, status, up_navn,
            up_adresse, up_postnr, up_by, up_email, up_telefon, dragt, adfaerd, observatorer, bemaerkninger, beskrivelse, created_at, updated_at)
            VALUES ('T-1', 1, 5, 'Sølvmåge', :d, :d, 'Et andet sted', 'NJ', 'Danmark', 'indsendt', 'Leif', '', '', '', 'l@x.dk', '', '', '', '', '', 'x', now(), now())"""),
                         {"d": date.today()})
        await s_.commit()
    rep = [r for r in (await su.get("/api/su/obs")).json()["rows"] if r["key"] == key][0]["reports"]
    assert len(rep) == 1 and rep[0]["sagsnr"] == "T-1" and rep[0]["same_place"] is False and rep[0]["unread"] is True
    assert (await su.post("/api/su/obs/decide", json={"keys": [key, f"{today}/3/100"], "decision": "godkendt"})).json()["n"] == 1  # gråand er ikke en SU-art
    assert all(r["su_ok"] for r in rows((await liste()).json(), 7003, 7005))  # mærket på alle fundets observationer
    assert not any(r["su_ok"] for r in rows((await liste()).json(), 7001))
    assert (await a1.get("/api/dagsliste/obs/7005")).json()["obs"]["su_ok"] is True
    assert [r["key"] for r in (await su.get("/api/su/obs?state=godkendt")).json()["rows"]] == [key]
    assert (await su.post("/api/su/obs/decide", json={"keys": ["x"], "decision": "godkendt"})).status_code == 400
    await su.post("/api/su/obs/decide", json={"keys": [key], "decision": ""})  # fortryd
    assert not any(r["su_ok"] for r in rows((await liste()).json(), 7003, 7005))

    # åben DKU-sag på observationen → lyseblåt (dku_kind «behandling»)
    async with db() as s:
        await s.execute(text("""INSERT INTO dku_cases (source, origin, obsid, artnr, dato, snapshot, reason, status, observer_involved, afdelinger)
            VALUES ('dofbasen', 'dku', 7001, 3, :d, '{}', 'andet', 'behandles', false, '{}')"""), {"d": date.today()})
        await s.commit()
    r1 = rows((await liste()).json(), 7001)[0]
    assert r1["dku_kind"] == "behandling" and r1["dku_status"]
    async with db() as s:
        await s.execute(text("DELETE FROM dku_cases WHERE obsid = 7001"))
        await s.execute(text("DELETE FROM observations WHERE obsid = 7005"))
        await s.commit()


async def test_su_medlem_som_indsender_faar_ikke_evigt_ulaest(web, db, sent):  # noqa: F811
    """Et SU-medlem, der selv har indsendt, skal kunne læse sin afgjorte sag væk fra badget."""
    await flags(db, su="on")
    su = web["ad"]
    cid = (await su.post("/api/su/cases", json={**CASE, "beskrivelse": "Tekst"})).json()["id"]
    await su.post(f"/api/su/cases/{cid}/submit")
    await su.post(f"/api/su/cases/{cid}/actions", json={"action": "close", "outcome": "godkendt"})
    await su.get(f"/api/su/cases/{cid}")
    assert (await su.get("/api/su/status")).json()["unread"] == 0
