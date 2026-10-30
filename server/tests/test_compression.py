"""SelectiveGZip: app-filer komprimeres; Range-forespørgsler og medier gør ikke."""

from starlette.testclient import TestClient


def _client():
    from portal.main import app  # conftest.py peger medie- og modelmapperne på en midlertidig mappe

    return TestClient(app)


def test_index_and_app_js_are_gzipped():
    c = _client()
    r = c.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    r = c.get("/v/test/js/core.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"


def test_range_and_without_accept_encoding_are_not_gzipped():
    c = _client()
    r = c.get("/v/test/js/core.js", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert "content-encoding" not in r.headers
    r = c.get("/v/test/js/core.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
