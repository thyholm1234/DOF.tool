"""Krydslistemodulets HTTP-lag: funktionsflag, rettigheder, visninger, mine kryds og admin (se test_krydslister.py for opsætning)."""

import httpx
import pytest

from test_krydslister import DSN, Y, s  # noqa: F401 – s er en fixture

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
H = {"X-Requested-With": "fetch"}


@pytest.fixture
async def web(s):  # noqa: F811
    from portal.db import get_session
    from portal.main import app
    from portal.models import User
    from portal.services import auth

    factory = s.info["factory"]

    async def session():
        async with factory() as x:
            yield x

    app.dependency_overrides[get_session] = session
    clients = {}
    async with factory() as x:
        for uid, name in [(1, "lb"), (2, "a1"), (4, "mo")]:
            token = await auth.create_session(await x.get(User, uid))
            clients[name] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"dt_session": token}, headers=H)
        clients["anon"] = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    yield clients
    for c in clients.values():
        await c.aclose()
    app.dependency_overrides.clear()


async def flags(s, **kw):  # noqa: F811
    from portal.services import features

    async with s.info["factory"]() as x:
        await features.save(x, kw)


async def test_flag_og_adgang(web, s):  # noqa: F811
    assert (await web["anon"].get("/api/krydslister/lister")).status_code == 401
    assert (await web["lb"].get("/api/krydslister/lister")).status_code == 404  # forhåndsvisning: kun for modulets administratorer
    assert (await web["mo"].get("/api/krydslister/lister")).status_code == 200
    me = (await web["mo"].get("/api/me")).json()["user"]["krydslister"]
    assert me["can_admin"] and me["features"]["krydslister"]
    await flags(s, krydslister="on")
    assert (await web["lb"].get("/api/krydslister/lister")).status_code == 200
    assert (await web["lb"].get("/api/admin/krydslister/listetyper")).status_code == 403
    await flags(s, krydslister_wbl="off")
    assert (await web["lb"].get("/api/krydslister/wbl")).status_code == 404 and (await web["lb"].get("/api/krydslister/kryds/dk/ranking")).status_code == 200
    await flags(s, krydslister="off")
    assert (await web["mo"].get("/api/admin/krydslister/listetyper")).status_code == 404


async def test_visninger_og_mine_kryds_over_http(web, s):  # noqa: F811
    from portal.services import krydslister

    await flags(s, krydslister="on")
    d = (await web["lb"].get("/api/krydslister/lister")).json()
    assert [x["key"] for x in d["lister"]][:2] == ["dk", "aar"] and d["years"][0] == 2026 and len(d["months"]) == 12
    await krydslister.refresh_auto(s)
    r = (await web["lb"].get("/api/krydslister/kryds/dk/ranking")).json()
    assert r["name"] == "Danske Arter" and [(x["kode"], x["antal"]) for x in r["rows"]][0] == ("LB", 3) and r["deltagere"] == 4 and r["auto"] is True
    for view in ("seneste", "hvem", "blockers", "blockerstat", "tophits"):
        assert (await web["lb"].get(f"/api/krydslister/kryds/dk/{view}")).status_code == 200, view
    assert (await web["lb"].get("/api/krydslister/kryds/dk/ukendt")).status_code == 404
    assert (await web["lb"].get("/api/krydslister/kryds/aar/ranking")).status_code == 400  # kræver år
    assert (await web["lb"].get("/api/krydslister/kryds/findes/ranking")).status_code == 404
    art = (await web["lb"].get("/api/krydslister/kryds/dk/art/a101")).json()
    assert art["art"] == "Urfugl" and sorted(x["person"] for x in art["rows"]) == ["Anna Andersen", "Bo Bent", "Leif Bolding"] and art["rows"][0]["dato"] <= art["rows"][-1]["dato"]
    oversigt = (await web["lb"].get("/api/krydslister/oversigt")).json()
    assert len(oversigt["dk"]) == 7 and oversigt["dk350"] == [] and "vp" in oversigt
    # mine kryds
    hits = (await web["lb"].get("/api/krydslister/arter?q=masaistruds&liste=vp")).json()
    assert hits and hits[0]["navn"] == "Masaistruds"
    r = await web["lb"].post("/api/krydslister/mine", json={"liste": "vp", "taxon_id": hits[0]["id"], "dato": f"{Y}-01-01", "lokalitet": "Zoo", "land": "Afrika"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    mine = (await web["lb"].get("/api/krydslister/mine/vp")).json()
    assert any(x["navn"] == "Masaistruds" and x["kilde"] == "manuel" for x in mine["rows"]) and mine["kan_krydse"] and mine["participant"]
    kid = next(x["id"] for x in mine["rows"] if x["kilde"] == "manuel")
    assert (await web["a1"].delete(f"/api/krydslister/mine/{kid}")).status_code == 404  # andres kryds
    assert (await web["lb"].delete(f"/api/krydslister/mine/{kid}")).status_code == 200
    await flags(s, krydslister_manuel="off")
    assert (await web["lb"].post("/api/krydslister/mine", json={"liste": "vp", "taxon_id": hits[0]["id"]})).status_code == 404
    # artsside og artslister
    t = (await web["lb"].get("/api/krydslister/arter?q=urfugl")).json()[0]
    page = (await web["lb"].get(f"/api/krydslister/art/{t['id']}")).json()
    assert page["navn"] == "Urfugl" and page["su"] and page["dofb"]["obs"] == 4 and any(k["liste"] == "dk" and k["antal"] == 3 for k in page["kryds"])
    sl = (await web["lb"].get("/api/krydslister/artslister/dk?q=urfugl")).json()
    assert sl["liste"]["key"] == "dk" and sl["rows"][0]["su"] is True and sl["can_edit"] is False
    assert (await web["lb"].get("/api/krydslister/artslister/xx")).status_code == 404
    w = (await web["lb"].get("/api/krydslister/wbl?q=urfugl")).json()
    assert w["rows"][0]["latin"] == "Lyrurus tetrix"


async def test_admin_over_http(web, s):  # noqa: F811
    import io

    await flags(s, krydslister="on")
    ld = (await web["mo"].get("/api/admin/krydslister/listetyper")).json()
    assert len(ld["defs"]) == 10 and {a["key"] for a in ld["artslister"]} == {"dk", "vp"}
    ref = next(a["id"] for a in ld["artslister"] if a["key"] == "dk")
    body = {"key": "ny", "navn": "Ny liste", "ref_list_id": ref, "auto": True, "manuel": True, "param": None, "regler": {"kategorier": ["A"]}, "sort": 5, "aktiv": True}
    r = await web["mo"].post("/api/admin/krydslister/listetyper", json=body)
    assert r.status_code == 200
    did = r.json()["id"]
    assert (await web["mo"].post("/api/admin/krydslister/listetyper", json=body)).status_code == 400  # nøglen findes
    assert (await web["mo"].put(f"/api/admin/krydslister/listetyper/{did}", json={**body, "navn": "Omdøbt"})).json()["navn"] == "Omdøbt"
    assert (await web["lb"].get("/api/krydslister/kryds/ny/ranking")).status_code == 200  # listetypen findes uden kodeændring
    assert (await web["mo"].delete(f"/api/admin/krydslister/listetyper/{did}")).status_code == 200
    csv = (await web["mo"].get(f"/api/admin/krydslister/artslister/{ref}/csv")).text
    assert csv.startswith("﻿latin;")
    up = await web["mo"].post(f"/api/admin/krydslister/artslister/{ref}/csv", files={"file": ("x.csv", csv.encode(), "text/csv")}, data={"anvend": "false"})
    assert up.status_code == 200 and up.json()["aendrede"] == [] and up.json()["uaendret"] > 450
    imp = await web["mo"].post("/api/admin/krydslister/wbl/import", files={"file": ("a.csv", ("Scientific_name,English_name\n" + "\n".join(f"Testus arten{i},Bird {i}" for i in range(60))).encode(), "text/csv")},
                               data={"version": "AviList test"})
    assert imp.status_code == 200 and imp.json()["diff"]["antal_nye"] == 60
    ap = await web["mo"].post(f"/api/admin/krydslister/wbl/import/{imp.json()['id']}/anvend")
    assert ap.status_code == 200 and ap.json()["nye"] == 60
    st = (await web["mo"].put("/api/admin/krydslister/indstillinger", json={"year_lists": 3, "tekster": {"regler": "## Regler\n\nKryds ærligt."}})).json()
    assert st["year_lists"] == 3
    assert "<h2" in (await web["lb"].get("/api/krydslister/sider/regler")).json()["html"]
    assert (await web["lb"].put("/api/admin/krydslister/indstillinger", json={})).status_code == 403
    assert (await web["lb"].get("/api/krydslister/lister")).json()["years"] == [2026, 2025, 2024]


async def test_artsside_kryds_og_redaktion(web, s):  # noqa: F811
    r = await web["mo"].get("/api/krydslister/dofart/101")
    assert r.status_code == 200 and {"lister", "kryds"} <= r.json().keys() and "dofb" not in r.json() and "fotos" not in r.json()
    assert (await web["mo"].get("/api/krydslister/dofart/99999")).json() == {"id": None, "lister": [], "kryds": []}
    assert (await web["lb"].get("/api/krydslister/dofart/101")).status_code == 404  # forhåndsvisning
    koeer = {k["key"]: k for k in (await web["mo"].get("/api/admin/redaktion")).json()["koeer"]}
    assert koeer["kryds"]["href"] == "#/lister/admin/godkendelse" and koeer["kryds"]["antal"] >= 0
    assert (await web["lb"].get("/api/admin/redaktion")).json()["koeer"] == []  # ingen moderationsrettigheder
    assert (await web["anon"].get("/api/admin/redaktion")).status_code == 401


async def test_livslisten_er_krydslisten_dk(web, s):  # noqa: F811
    """Lister → Mine lister (Danmark, alle år) viser de manuelle DK-kryds og skjuler automatiske kryds, deltageren har skjult."""
    await flags(s, krydslister="on")
    before = (await web["lb"].get("/api/lists/user/LB?scope=dk&year=all")).json()
    assert sorted(i["artnr"] for i in before["items"]) == [101, 102, 103]
    fasan = (await web["lb"].get("/api/krydslister/arter?q=fasan&liste=dk")).json()[0]
    assert (await web["lb"].post("/api/krydslister/mine", json={"liste": "dk", "taxon_id": fasan["id"], "lokalitet": "Haven"})).json()["status"] == "ok"
    assert (await web["lb"].post("/api/krydslister/mine/skjul", json={"liste": "dk", "artnr": 102, "hidden": True})).status_code == 200
    after = (await web["lb"].get("/api/lists/user/LB?scope=dk&year=all")).json()
    assert sorted(i["artnr"] for i in after["items"]) == [101, 103, 104] and after["count"] == 3
    assert next(i for i in after["items"] if i["artnr"] == 104)["kilde"] == "manuel"
    assert sorted(i["artnr"] for i in (await web["lb"].get(f"/api/lists/user/LB?scope=dk&year={Y}")).json()["items"]) == [101, 102]  # årslister: kun DOFbasen
    await flags(s, krydslister="off")
    assert sorted(i["artnr"] for i in (await web["lb"].get("/api/lists/user/LB?scope=dk&year=all")).json()["items"]) == [101, 102, 103]
