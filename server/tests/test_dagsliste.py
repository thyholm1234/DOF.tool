"""Dagslisten: funktionsflag, filtre (visning, afdeling, lokalitet, summering) og hemmelige observationer (kræver ARTICLES_TEST_DSN)."""

from datetime import date

import pytest
from sqlalchemy import text

from test_learn_play import DSN, db, flags, web  # noqa: F401 – web og db er fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


async def seed(db):  # noqa: F811
    from portal.models import Afdeling, DofbObsStatus, Location, Observer, Trip

    async with db() as s:
        await (await s.connection()).run_sync(lambda c: [t.__table__.create(c, checkfirst=True) for t in (Afdeling, Location, Observer, Trip, DofbObsStatus)])
        await s.execute(text("DELETE FROM observations WHERE obsid >= 7000"))
        await s.execute(text("INSERT INTO species (artnr, navn, latin, sortering, art_type, list_artnr, klass, excluded) VALUES "
                             "(3, 'Gråand', 'Anas platyrhynchos', 3, 'art', 3, 'Alm', false), (5, 'Sølvmåge', 'Larus argentatus', 5, 'art', 5, 'Alm', false) ON CONFLICT DO NOTHING"))
        ins = ("INSERT INTO observations (obsid, dato, artnr, antal, loknr, obserkode, hemmelig, kategori, afdelinger) "
               "VALUES (:o, :d, :a, :n, 100, :k, :h, :c, '{}')")
        d = date.today()
        for o, a, n, k, h_, c in [(7001, 3, 2, "LB", 0, "alm"), (7002, 3, 3, "LB", 0, "alm"), (7003, 5, 1, "LB", 0, "SU"), (7004, 5, 9, "XX", 1, "SU")]:
            await s.execute(text(ins), {"o": o, "d": d, "a": a, "n": n, "k": k, "h": h_, "c": c})
        await s.commit()


async def test_dagsliste_flag_filtre_og_hemmelige(web, db):  # noqa: F811
    await seed(db)
    await flags(db, dagsliste="off")
    assert (await web["a1"].get("/api/dagsliste")).status_code == 404
    await flags(db, dagsliste="on")
    assert (await web["anon"].get("/api/dagsliste")).status_code in (401, 403)
    alle = (await web["a1"].get("/api/dagsliste?visning=allfugle&summering=ingen")).json()
    assert {r["obsid"] for r in alle["rows"] if r["obsid"] >= 7000} == {7001, 7002, 7003}  # andres hemmelige vises ikke
    sum_ = (await web["a1"].get("/api/dagsliste?visning=allfugle&summering=dato")).json()
    gaas = [r for r in sum_["rows"] if r["artnr"] == 3]
    assert len(gaas) == 1 and gaas[0]["antal"] == 5 and gaas[0]["n_obs"] == 2  # summeret pr. observatør og lokalitet
    sjaeldne = (await web["a1"].get("/api/dagsliste?visning=sufugle&summering=ingen")).json()
    assert [r["obsid"] for r in sjaeldne["rows"] if r["obsid"] >= 7000] == [7003]
    assert (await web["a1"].get("/api/dagsliste?visning=allfugle&lokalitet=999999")).json()["rows"] == []
    assert (await web["a1"].get("/api/dagsliste?visning=patdyr")).json()["rows"] == []
    assert (await web["a1"].get("/api/dagsliste?visning=ugyldig")).status_code == 422
    assert (await web["a1"].get("/api/dagsliste?dato=2999-01-01")).status_code == 422
    assert (await web["a1"].get("/api/dagsliste/afdelinger")).status_code == 200


async def test_enkelt_observation_med_turliste(web, db):  # noqa: F811
    from portal.models.reference import Kommune

    await seed(db)
    async with db() as s:
        await (await s.connection()).run_sync(lambda c: Kommune.__table__.create(c, checkfirst=True))
        await s.execute(text("INSERT INTO trips (turid, dato, loknr, obserkode, medobser) VALUES (7100, :d, 100, 'LB', 'HO') "
                             "ON CONFLICT (turid) DO NOTHING"), {"d": date.today()})
        await s.execute(text("UPDATE observations SET turid = 7100, alderkode = '1K' WHERE obsid IN (7001, 7003, 7004)"))
        await s.commit()
    await flags(db, dagsliste="on")
    from portal.cache import kv
    import json

    await kv().set("media:7003", json.dumps({"obsid": 7003, "dku": "Til behandling hos DKU", "images": ["https://x/1.jpg"], "sounds": []}))
    rows = (await web["a1"].get("/api/dagsliste?visning=allfugle&summering=ingen")).json()["rows"]
    row = next(x for x in rows if x["obsid"] == 7003)
    assert row["dku_status"] == "Til behandling hos DKU" and row["dku_kind"] == "behandling" and row["foto"] and not row["lyd"]
    assert "obsids" not in row
    assert (await web["a1"].get("/api/dagsliste/obs/7003")).json()["obs"]["dku_kind"] == "behandling"
    await kv().delete("media:7003")
    r = (await web["a1"].get("/api/dagsliste/obs/7001")).json()
    assert r["obs"]["alderkode"] == "1K" and r["obs"]["medobser"] == "HO" and r["obs"]["art"] == "Gråand"
    assert [t["obsid"] for t in r["tur"]] == [7001, 7003]  # resten af turen, uden andres hemmelige
    assert (await web["a1"].get("/api/dagsliste/obs/7004")).status_code == 404
    await flags(db, dagsliste="off")
    assert (await web["a1"].get("/api/dagsliste/obs/7001")).status_code == 404
