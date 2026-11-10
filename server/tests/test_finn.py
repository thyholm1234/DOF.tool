"""FiNN-områder og -punkter (services/finn.py): WKT → ringe, punkt i område, testområder udelades, og punkter uden
område knyttes til det område, de ligger i. Spontan- og FiNN-tællingernes metoder findes i opslagslisten."""

from __future__ import annotations

SITE = {"id": 7, "lokId": None, "locationName": None, "isTestSite": False, "title": "Mosen",
        "border": "POLYGON ((10.0 56.0, 10.1 56.0, 10.1 56.1, 10.0 56.1, 10.0 56.0))"}


def test_parse_ring_lat_lon():
    from portal.services import finn

    ring = finn.parse_ring(SITE["border"])
    assert ring[0] == [56.0, 10.0] and len(ring) == 5
    assert finn.parse_ring("MULTIPOLYGON (((10 56, 11 56, 11 57, 10 56)), ((1 1, 2 2, 3 3, 1 1)))")[1] == [56.0, 11.0]
    assert finn.parse_ring(None) == []


def test_inside_og_site_at():
    from portal.services import finn

    d = finn.build([SITE, {**SITE, "id": 8, "isTestSite": True, "title": "Test"}], [])
    assert [s["id"] for s in d["sites"]] == [7]  # testområdet er udeladt
    assert finn.site_at(d, 56.05, 10.05)["title"] == "Mosen"
    assert finn.site_at(d, 56.2, 10.05) is None


def test_punkter_knyttes_til_omraadet():
    from portal.services import finn

    d = finn.build([SITE], [{"id": 1, "finnSiteId": None, "lat": 56.05, "lon": 10.05, "source": "researcher", "type": "management"},
                            {"id": 2, "finnSiteId": 99, "lat": 55.0, "lon": 9.0, "source": "volunteer", "type": "reference"},
                            {"id": 3, "finnSiteId": None, "lat": None, "lon": 9.0}])
    assert [(p["id"], p["site"]) for p in d["points"]] == [(1, 7), (2, 99)]


def test_taellingernes_metoder_kendes():
    from portal.api import trips

    assert {98, 99} <= trips.METODE_IDS
    body = trips.TripIn(dato="2026-11-01", loknr=1, metode_id=99, finn_site_id=7, komplet=True)
    assert body.finn_site_id == 7


async def test_short_user_kan_bruges_efter_sessionen(monkeypatch):
    """SSE-strømmen får brugeren fra en kortlivet session; felterne skal kunne læses, når den er lukket."""
    from types import SimpleNamespace

    from portal.api import deps
    from portal.models import User

    u = User(id=5, obserkode="ABC", role="user", blocked=False)

    class FakeSession:
        def __init__(self):
            self.objs, self.closed = {id(u)}, False

        def __contains__(self, o):
            return id(o) in self.objs

        def expunge(self, o):
            self.objs.discard(id(o))

        async def rollback(self):
            for o in list(self.objs):  # som SQLAlchemy: alt, der stadig er i sessionen, udløbes
                raise AssertionError("brugeren blev ikke løsrevet før rollback")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.closed = True

    async def real_user(request, db):
        return u

    monkeypatch.setattr(deps, "real_user", real_user)
    import portal.db
    monkeypatch.setattr(portal.db, "SessionLocal", FakeSession)
    got = await deps.short_user(SimpleNamespace(cookies={}, state=SimpleNamespace()))
    assert got is u and got.id == 5


def _row(model, **kw):
    base = {"origin": "dofbasen", "aktiv": True, "edited": False, "removed_at": None}
    return model(**{**base, **kw})


def test_synk_diff_grupperer_nye_aendrede_konflikter_og_fjernede():
    from datetime import datetime, timezone

    from portal.models import FinnSite
    from portal.services import finn, finn_admin

    ring = finn.parse_ring(SITE["border"])
    ring2 = [[56.0, 10.0], [56.0, 10.2], [56.2, 10.2], [56.0, 10.0]]
    dof = lambda title, r=ring: {"title": title, "loknr": None, "ring": r}  # noqa: E731
    fetched = finn.build([SITE, {**SITE, "id": 8, "title": "Engen"}, {**SITE, "id": 9, "title": "Skoven ny"},
                          {**SITE, "id": 10, "title": "Kæret ny"}], [])
    sites = [
        _row(FinnSite, id=7, title="Mosen", loknr=None, ring=ring, dof=dof("Mosen")),                       # uændret
        _row(FinnSite, id=9, title="Skoven", loknr=None, ring=ring, dof=dof("Skoven")),                     # ændret hos DOFbasen
        _row(FinnSite, id=10, title="Kæret (rettet)", loknr=None, ring=ring2, edited=True, dof=dof("Kæret")),  # konflikt
        _row(FinnSite, id=11, title="Væk", loknr=None, ring=ring, dof=dof("Væk")),                          # fjernet
        _row(FinnSite, id=12, title="Allerede væk", loknr=None, ring=ring, dof=dof("x"), removed_at=datetime.now(timezone.utc)),
        _row(FinnSite, id=1_000_000, origin="portal", title="Egen", loknr=None, ring=ring),                 # røres aldrig
    ]
    rep = finn_admin.diff(fetched, sites, [])
    ids = {g: [r["id"] for r in rows] for g, rows in rep["sites"].items()}
    assert ids == {"nye": [8], "aendrede": [9], "konflikter": [10], "fjernede": [11]}
    assert rep["sites"]["aendrede"][0]["felter"] == ["title"]
    assert rep["antal"]["sites"]["nye"] == 1 and rep["hentet"]["sites"] == 4


def test_synk_diff_punkter_og_rettet_tilbage_er_ikke_konflikt():
    from portal.models import FinnPoint
    from portal.services import finn_admin

    p = {"id": 1, "site": 7, "lat": 56.05, "lon": 10.05, "source": "volunteer", "type": "reference"}
    fetched = {"sites": [], "points": [{**p, "lat": 56.06}]}
    dof = {k: p[k] for k in finn_admin.POINT_FIELDS}
    # Portalen har «rettet» punktet til præcis det, DOFbasen nu har: ingen konflikt, bare ændret
    same = _row(FinnPoint, id=1, site_id=7, lat=56.06, lon=10.05, source="volunteer", type="reference", edited=True, dof=dof)
    assert [r["id"] for r in finn_admin.diff(fetched, [], [same])["points"]["aendrede"]] == [1]
    moved = _row(FinnPoint, id=1, site_id=7, lat=56.07, lon=10.05, source="volunteer", type="reference", edited=True, dof=dof)
    assert [r["id"] for r in finn_admin.diff(fetched, [], [moved])["points"]["konflikter"]] == [1]


def test_check_ring_lukker_og_afviser():
    import pytest

    from portal.services.finn_admin import FinnError, _check_ring

    assert _check_ring([[56, 10], [56, 10.1], [56.1, 10.1]])[-1] == [56.0, 10.0]
    with pytest.raises(FinnError):
        _check_ring([[56, 10], [56, 10.1]])
    with pytest.raises(FinnError):
        _check_ring([[50, 10], [56, 10.1], [56.1, 10.1]])


def test_utm32_og_gridceller():
    """Mod PostGIS' ST_Transform(…, 25832): under en millimeter, også på Bornholm uden for zonen."""
    import pytest

    from portal.domain.geo import grid_cell, grid_ring, utm32, utm32_inv

    for (lat, lon), (e, n) in {(55.6761, 12.5683): (724351.928637642, 6175804.02212861),
                               (55.1, 15.1): (888982.3411151072, 6122928.563989488),
                               (57.7, 10.0): (559601.4498316299, 6395750.679647572)}.items():
        assert utm32(lat, lon) == pytest.approx((e, n), abs=1e-3)
        assert utm32_inv(e, n) == pytest.approx((lat, lon), abs=1e-9)
    assert grid_cell(55.6761, 12.5683, 10) == "10km_617_72"  # DKN: 10km_<N/10 km>_<E/10 km>
    assert grid_cell(55.6761, 12.5683, 1) == "1km_6175_724"
    assert grid_cell(55.6761, 12.5683, 5) == "5km_1235_144"
    ring = grid_ring("10km_617_72")
    assert len(ring) == 4 and utm32(*ring[0]) == pytest.approx((720000, 6170000), abs=0.1)  # sydvestligt hjørne
    center = [sum(p[i] for p in ring) / 4 for i in (0, 1)]
    assert grid_cell(*center, 10) == "10km_617_72"


def test_taellinger_filter_sql():
    from datetime import date

    from portal.services.taellinger import Filter

    where, p = Filter(kind="finn", fra=date(2026, 1, 1), site=7, obserkode="abc", artnr=1).sql()
    assert p["metoder"] == [99] and p["obs"] == "ABC" and p["site"] == 7
    assert "t.dato >= :fra" in where and "fr.artnr = :artnr" in where
    assert Filter().sql()[1]["metoder"] == [98, 99]
