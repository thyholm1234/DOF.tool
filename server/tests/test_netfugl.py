import json

import pytest

from portal.config import SEED_DIR
from portal.services import features
from portal.services import netfugl as svc


def data(rows, people=None):
    people = people or {r[0]: r[0].title() for r in rows}
    return {"people": people, "name": "Test", "rows": [
        {"kode": k, "key": key, "navn": key.upper(), "latin": key, "dato": d, "lok": "Lok", "land": "Danmark", "kilde": "dofbasen"}
        for k, key, d in rows]}


def test_parse_list():
    assert svc.parse_list("dk") == ("dk", None)
    assert svc.parse_list("aar:2026") == ("aar", "2026")
    assert svc.parse_list("md:12") == ("md", "12")
    assert svc.parse_list("kom:0101") == ("kom", "0101")
    for bad in ("xx", "aar", "aar:abc", "md:13", "afd:x", "kom:abcd"):
        with pytest.raises(ValueError):
            svc.parse_list(bad)


def test_ranking_ties_and_latest():
    d = data([("A", "a1", "2026-01-01"), ("A", "a2", "2026-03-01"), ("B", "a1", "2026-02-01"), ("B", "a3", "2026-04-01"), ("C", "a1", "2025-01-01")])
    r = svc.ranking(d)
    assert [x["kode"] for x in r] == ["A", "B", "C"]
    assert [x["placering"] for x in r] == [1, 1, 3]
    assert r[1]["seneste"] == "A3" and r[1]["opdateret"] == "2026-04-01"
    lat = svc.latest(d, 3)
    assert [(x["kode"], x["key"], x["x"]) for x in lat] == [("B", "a3", 2), ("A", "a2", 2), ("B", "a1", 1)]
    assert svc.latest(d, 10, min_count=2) == [x for x in svc.latest(d, 10) if x["x"] >= 2]


def test_blockers_and_tophits():
    rows = [(f"P{i}", "common", "2026-05-01") for i in range(20)] + [("P1", "rare", "2026-05-02"), ("P2", "rare", "2025-05-02"), ("P1", "mega", "2024-01-01")]
    d = data(rows)
    b = svc.blockers(d)
    assert b["total"] == 20 and b["limit"] == 2
    assert [r["key"] for r in b["rows"]] == ["mega", "rare"]
    assert b["stat"][0]["kode"] == "P1" and b["stat"][0]["blockers"] == 2
    top = svc.tophits(d, 2026)
    assert top[0]["key"] == "common" and top[0]["aar"] == 20
    assert next(t for t in top if t["key"] == "rare") == {"key": "rare", "navn": "RARE", "latin": "rare", "aar": 1, "total": 2}


def test_who_has_what_includes_unseen_reference_species():
    d = data([("A", "a1", "2026-01-01"), ("B", "a1", "2026-01-02"), ("A", "x9", "2026-01-03")])
    ref = [{"key": "a1", "navn": "Art 1", "latin": "l1", "su": False}, {"key": "a2", "navn": "Art 2", "latin": "l2", "su": True}]
    w = svc.who_has_what(d, ref)
    assert w["total"] == 2
    assert [(r["key"], r["antal"]) for r in w["rows"]] == [("a1", 2), ("a2", 0), ("x9", 1)]


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Nyt &amp; spændende</title><link>https://x.dk/1</link><guid>g1</guid><pubDate>Thu, 22 Oct 2026 10:00:00 +0200</pubDate>
<description>&lt;p&gt;Hej &lt;b&gt;verden&lt;/b&gt;&lt;/p&gt;</description></item></channel></rss>""".encode()
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>urn:1</id><title>Atom-indslag</title>
<link rel="alternate" href="https://y.dk/a"/><updated>2026-10-21T08:00:00Z</updated><summary>Kort</summary></entry></feed>"""


def test_parse_feed_rss_and_atom():
    r = svc.parse_feed(RSS)[0]
    assert r["title"] == "Nyt & spændende" and r["url"] == "https://x.dk/1" and r["guid"] == "g1"
    assert r["summary"] == "Hej verden" and r["published_at"].isoformat() == "2026-10-22T10:00:00+02:00"
    a = svc.parse_feed(ATOM)[0]
    assert a["guid"] == "urn:1" and a["url"] == "https://y.dk/a" and a["published_at"].year == 2026


def test_parse_feed_rejects_entities():
    with pytest.raises(ValueError):
        svc.parse_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss/>')


def test_discover_feed_url():
    page = '<html><head><link rel="alternate" type="application/rss+xml" title="Feed" href="/feed/"></head></html>'
    assert svc.discover(page, "https://www.dof.dk/") == "https://www.dof.dk/feed/"
    assert svc.discover("<html></html>", "https://x.dk") is None


def test_netfugl_features_have_parent():
    for k, (_, _, parent, default) in features.FEATURES.items():
        if k.startswith("netfugl_"):
            assert parent == "netfugl" and default in features.STATES
    flags = {k: "on" for k in features.FEATURES}
    flags["netfugl"] = "preview"
    assert not features.visible(flags, "netfugl_kryds", superadmin=False, insider=False)
    assert features.visible(flags, "netfugl_kryds", superadmin=False, insider=True)


def test_ctx_dofbasen_integration_requires_both_modules():
    flags = {k: "on" for k in features.FEATURES}
    ctx = svc.Ctx(user=None, flags=flags)
    assert ctx.out()["features"]["netfugl_dofbasen"] is True
    flags["dofbasen"] = "off"
    assert ctx.out()["features"]["netfugl_dofbasen"] is False


def test_seed_content():
    d = json.loads((SEED_DIR / "netfugl" / "content.json").read_text(encoding="utf-8"))
    assert len(d["dk_list"]) > 500 and len(d["vp_list"]) > 1000 and len(d["families"]) > 200
    assert all(len(r) == 4 for r in d["dk_list"]) and all(len(r) == 5 for r in d["vp_list"])
    assert {"om", "faq", "regler", "stoet", "krydslister"} <= set(d["pages"])
    assert d["ordforklaringer"] and d["links"] and d["feeds"]
    # Artikler kun som uddrag med link til originalen
    assert all(a["source_url"].startswith("https://www.netfugl.dk/artikler/") and len(a["body"]) < 1500 for a in d["articles"])


def test_rss_refuses_internal_hosts():
    for url in ("http://localhost/feed", "http://127.0.0.1/", "http://10.0.0.5/rss", "file:///etc/passwd", "http://[::1]/"):
        with pytest.raises(ValueError):
            svc._public_host(url)
