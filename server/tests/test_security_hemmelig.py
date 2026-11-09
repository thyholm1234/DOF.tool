"""Sikkerhedsregressioner fra anden runde af gennemgangen 2026-11-08 (docs/sikkerhed/gennemgang-2026-11-08-runde2.md).

R-01: hemmelige DOF.tool-observationer («kun synlig for dig») må ikke indgå i det samlede datasæt, som andre ser.
R-02: SSRF-værnet gælder også artikelimportens billedhentning. R-05: testbrugeres adgangskoder er lange.
Kører uden database og Valkey.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from portal.services import dataset, egress

OBS = [
    {"obsid": -1_000_000_001, "turid": -1_000_000_001, "dato": "2026-05-01", "artnr": 100, "loknr": 7, "obserkode": "AAA", "hemmelig": 0},
    {"obsid": -1_000_000_002, "turid": -1_000_000_002, "dato": "2026-05-01", "artnr": 200, "loknr": 7, "obserkode": "AAA", "hemmelig": 1},
    {"obsid": -1_000_000_003, "turid": -1_000_000_003, "dato": "2026-05-02", "artnr": 300, "loknr": 8, "obserkode": "BBB", "hemmelig": 1},
]
TRIPS = [{"turid": o["turid"], "dato": o["dato"], "loknr": o["loknr"], "obserkode": o["obserkode"]} for o in OBS]


@pytest.fixture
def tool_rows(monkeypatch):
    async def fake():
        return {"obs": [dict(o) for o in OBS], "trips": [dict(t) for t in TRIPS]}

    monkeypatch.setattr(dataset, "tool_data", fake)


def _ids(params: dict) -> set[int]:
    return {o["obsid"] for o in json.loads(params.get("_tool_observations", "[]"))}


# ---------------------------------------------------------------- R-01: dataset.include


async def test_include_udelader_hemmelige_som_standard(tool_rows):
    sql, params = await dataset.include("SELECT count(*) FROM observations o", {})
    assert _ids(params) == {-1_000_000_001}
    # turene følger kun de synlige observationer
    assert {t["turid"] for t in json.loads(params["_tool_trips"])} == {-1_000_000_001}


async def test_include_ejeren_ser_kun_egne_hemmelige(tool_rows):
    _, params = await dataset.include("SELECT * FROM observations o", {}, secret_for="AAA")
    assert _ids(params) == {-1_000_000_001, -1_000_000_002}  # BBB's hemmelige er stadig udeladt


async def test_include_secret_all_til_kaldere_der_selv_skjuler_stedet(tool_rows):
    _, params = await dataset.include("SELECT * FROM observations o", {}, secret_all=True)
    assert _ids(params) == {o["obsid"] for o in OBS}


async def test_include_kun_hemmelige_giver_uaendret_forespoergsel(tool_rows):
    sql = "SELECT * FROM observations o WHERE o.obserkode = :k"
    out, params = await dataset.include(sql, {"k": "BBB"}, obserkode="BBB")
    assert out == sql and "_tool_observations" not in params


def test_visible():
    assert dataset.visible({"hemmelig": 0, "obserkode": "X"})
    assert not dataset.visible({"hemmelig": 1, "obserkode": "X"})
    assert not dataset.visible({"hemmelig": 1, "obserkode": "X"}, secret_for="Y")
    assert dataset.visible({"hemmelig": 1, "obserkode": "X"}, secret_for="X")
    assert dataset.visible({"hemmelig": 1, "obserkode": "X"}, secret_all=True)


class _Empty:
    def mappings(self):
        return self

    def all(self):
        return []


async def test_year_list_hemmelige_kun_naar_ejeren_selv_ser(monkeypatch):
    """Offentlige lister (andres og ranglister) må ikke vise art, dato og lokalitet for hemmelige observationer."""
    from portal.services import lists

    hints: list[dict] = []

    async def fake_execute(session, sql, params=None, **kw):
        hints.append(kw)
        return _Empty()

    async def prefix(_):
        return "#"

    async def where(_s, _spec, _prefix, _windows=None):
        return "TRUE", {}

    monkeypatch.setattr(dataset, "execute", fake_execute)
    monkeypatch.setattr(lists, "matrikel_prefix", prefix)
    monkeypatch.setattr(lists, "_where", where)
    spec = lists.parse_list_spec("dk", 2026)
    await lists.year_list(None, "AAA", spec)
    await lists.year_list(None, "AAA", spec, own=True)
    assert hints[0].get("secret_for") is None
    assert hints[1].get("secret_for") == "AAA"


async def test_fund_viser_ikke_hemmelige_steder(monkeypatch):
    """Fundberegningen tæller hemmelige observationer, men hverken fundets sted eller members-nøglen (dato|lokalitet) afslører dem."""
    from types import SimpleNamespace

    from portal.services import finds

    rows = [  # samme dag, to lokaliteter langt fra hinanden: to fund
        SimpleNamespace(dato=date(2026, 5, 1), loknr=7, turid=1, adf="", lat=55.0, lon=12.0, antal=1, secret=False),
        SimpleNamespace(dato=date(2026, 5, 1), loknr=9, turid=2, adf="", lat=57.5, lon=9.0, antal=1, secret=True),
    ]
    seen: dict = {}

    class R:
        def __init__(self, data):
            self.data = data

        def all(self):
            return self.data

        def scalars(self):  # artens numre (SELECT artnr FROM species WHERE list_artnr = …)
            return [100]

    async def fake_execute(session, sql, params=None, **kw):
        seen.update(kw)
        return R(rows)

    class Session:
        async def execute(self, *_a, **_k):
            return R([(7, "Offentlig"), (9, "Hemmelig")])

    async def species(_s, _a):
        return 100, "SU"

    async def version(_):
        return 1

    async def cached(_key, _ttl, produce):
        return await produce()

    monkeypatch.setattr(dataset, "execute", fake_execute)
    monkeypatch.setattr(finds, "_species", species)
    monkeypatch.setattr(finds, "version", version)
    monkeypatch.setattr(finds, "cached", cached)
    data = await finds.species_finds(Session(), 100)
    assert seen.get("secret_all") is True
    assert seen.get("artnrs") == [100]  # DOF.tool-rækkerne grovfiltreres på arten (ellers læses alle observationer)
    assert data["total"] == 2
    secret = [f for f in data["finds"] if f["loknr"] is None]
    assert len(secret) == 1 and secret[0]["lat"] is None and secret[0]["lok"] is None
    assert "2026-05-01|9" not in data["members"]
    assert "2026-05-01|7" in data["members"]


# ---------------------------------------------------------------- R-02: SSRF-værnet i artikelimporten


@pytest.mark.parametrize("url", ["http://127.0.0.1/x.jpg", "http://10.0.1.8:8100/", "http://[::1]/", "file:///etc/passwd",
                                 "http://169.254.169.254/latest/meta-data/"])
async def test_egress_afviser_interne_adresser(url):
    with pytest.raises(ValueError):
        await egress.check_request(httpx.Request("GET", url))


async def test_artikelimport_bruger_egress_hook():
    """Klienten i artikelimporten skal have SSRF-hooket (også omdirigeringer fra netfugl.dk kontrolleres)."""
    import inspect

    from portal.services import article_import

    src = inspect.getsource(article_import)
    assert "event_hooks={\"request\": [egress.check_request]}" in src


async def test_artikelimport_omdirigering_til_internt_stoppes(monkeypatch):
    """En omdirigering fra en offentlig adresse til en intern stoppes af hooket, før forbindelsen åbnes."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "netfugl.dk":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:6379/"})
        return httpx.Response(200, content=b"intern")

    def fake_public(url: str) -> None:  # netfugl.dk regnes for offentlig uden DNS i testen
        if httpx.URL(url).host != "netfugl.dk":
            raise ValueError("ikke offentlig")

    monkeypatch.setattr(egress, "public_host", fake_public)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                 event_hooks={"request": [egress.check_request]}) as client:
        with pytest.raises(ValueError):
            await client.get("https://netfugl.dk/billede.jpg")
    assert calls == ["https://netfugl.dk/billede.jpg"]


# ---------------------------------------------------------------- R-05: testbrugeres adgangskoder


def test_testbruger_adgangskode_skal_vaere_lang():
    from pydantic import ValidationError

    from portal.api.admin import brugere

    with pytest.raises(ValidationError):
        brugere.TestUserIn(navn="Test", password="kort123")
    assert brugere.TestUserIn(navn="Test", password="x" * brugere.TEST_PW_MIN).password
