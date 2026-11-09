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
