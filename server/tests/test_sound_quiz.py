"""Lydquizzen: indstillinger (nøglen skjules), automatisk opslag hos xeno-canto (mocket), Macaulay-optagelser, quiz og facit først ved svar."""

import pytest
from sqlalchemy import text

from test_photo_import import DSN, db  # noqa: F401 – db er en fixture
from test_photos_api import flags, web  # noqa: F401 – web er en fixture

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


def fake_xc(monkeypatch, tmp_path):
    from portal.services import sound_quiz

    calls = []

    async def xc_search(latin, s, rtype=None):
        calls.append((latin, rtype))
        return [{"ext_id": f"{abs(hash(latin)) % 10**6}{rtype}{i}", "recordist": "Rita Optager", "license": "CC BY-NC-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                 "rtype": "call" if rtype == "call" else "song", "quality": "A", "length_s": 20, "country": "Denmark", "page_url": f"https://xeno-canto.org/9{i}", "file_url": "https://example.invalid/x.mp3"} for i in range(3)]

    async def audio_file(source, ext_id, url):
        p = tmp_path / f"{source}-{ext_id}.mp3"
        p.write_bytes(b"ID3fakeaudio")
        return p

    monkeypatch.setattr(sound_quiz, "xc_search", xc_search)
    monkeypatch.setattr(sound_quiz, "audio_file", audio_file)
    return calls


async def test_lydquiz(web, db, monkeypatch, tmp_path):  # noqa: F811
    calls = fake_xc(monkeypatch, tmp_path)
    await flags(db, lydquiz="on")
    async with db() as s:
        for i, latin in enumerate(["Phylloscopus collybita", "Phylloscopus trochilus", "Phylloscopus sibilatrix"]):
            await s.execute(text("INSERT INTO species (artnr, navn, latin, klass, art_type, list_artnr, excluded) VALUES (:a, :n, :l, 'Alm', 'art', :a, false)"),
                            {"a": 900 + i, "n": f"Løvsanger {i}", "l": latin})
        await s.commit()
        arter = list((await s.execute(text("SELECT artnr FROM species WHERE latin ~ '^[A-Z][a-z]+ [a-z]+$' AND art_type = 'art' AND NOT excluded ORDER BY artnr LIMIT 5"))).scalars())
    assert len(arter) == 5
    # indstillinger: kun superadmin, nøglen sendes aldrig tilbage
    assert (await web["a1"].get("/api/admin/lydquiz")).status_code == 403
    r = await web["su"].put("/api/admin/lydquiz", json={"xc_key": "hemmeligNoegle123", "min_quality": "A", "area": "denmark", "per_species": 5})
    assert r.status_code == 200 and r.json()["has_key"] and r.json()["key_hint"] == "e123" and "xc_key" not in r.json() and r.json()["min_quality"] == "A"
    assert "hemmelig" not in (await web["su"].get("/api/admin/lydquiz")).text
    assert (await web["su"].put("/api/admin/lydquiz", json={"xc_key": "a b"})).status_code == 400
    t = (await web["su"].post("/api/admin/lydquiz/test?artnr=900")).json()
    assert t["ok"] and 'q:A' in t["soegning"] and "cnt:denmark" in t["soegning"]
    # brugerdelen
    assert (await web["a1"].get("/api/me")).json()["user"]["lydquiz"] is True
    g = (await web["a1"].get("/api/learn/lyd/grupper")).json()
    assert g["ukendt"] and g["grupper"]
    assert (await web["a1"].post("/api/learn/lyd/quiz", json={"arter": arter[:1], "metode": "valg"})).status_code == 422
    r = await web["a1"].post("/api/learn/lyd/quiz", json={"arter": arter, "metode": "valg"})
    assert r.status_code == 200
    qs = r.json()["questions"]
    assert len(calls) == 1 + 10 and len(qs) == 20  # afprøvningen + sang og kald pr. art (standard: begge); 30 optagelser, 20 spørgsmål
    q = qs[0]
    assert set(q) <= {"id", "media_url", "rtype", "metode", "choices"} and q["media_url"] == f"/api/learn/lyd/audio/{q['id']}" and len(q["choices"]) == 4
    a = await web["a1"].get(q["media_url"])
    assert a.status_code == 200 and a.content.startswith(b"ID3")
    wrong = (await web["a1"].post("/api/learn/lyd/svar", json={"token": q["id"], "artnr": 0})).json()
    assert wrong["correct"] is False and wrong["artnr"] in arter and wrong["recordist"] == "Rita Optager" and wrong["url"].startswith("https://xeno-canto.org/")
    assert (await web["a1"].post("/api/learn/lyd/svar", json={"token": q["id"], "artnr": wrong["artnr"]})).json()["correct"] is True
    # anden runde: arterne er gemt, så xeno-canto spørges ikke igen
    before = len(calls)
    assert (await web["a1"].post("/api/learn/lyd/quiz", json={"arter": arter, "metode": "tekst"})).status_code == 200
    assert len(calls) == before
    # kun sang / kun kald bruger de gemte optagelser (og slår ikke op igen)
    for kind, want in (("sang", "song"), ("kald", "call")):
        r = await web["a1"].post("/api/learn/lyd/quiz", json={"arter": arter, "metode": "tekst", "type": kind})
        assert r.status_code == 200 and len(r.json()["questions"]) == 15 and {q["rtype"] for q in r.json()["questions"]} == {want}
    assert len(calls) == before
    assert (await web["a1"].post("/api/learn/lyd/quiz", json={"arter": arter, "metode": "tekst", "type": "bla"})).status_code == 422
    # Macaulay: kun redaktører/admin
    assert (await web["a1"].post("/api/learn/lyd/ml", json={"ref": "123456789", "artnr": arter[0]})).status_code == 403
    r = await web["ad"].post("/api/learn/lyd/ml", json={"ref": "https://macaulaylibrary.org/asset/123456789", "artnr": arter[0], "rtype": "sang"})
    assert r.status_code == 200 and r.json()["ext_id"] == "123456789"
    assert (await web["ad"].post("/api/learn/lyd/ml", json={"ref": "ML123456789", "artnr": arter[0]})).status_code == 400  # findes allerede
    assert (await web["ad"].post("/api/learn/lyd/ml", json={"ref": "ikke et link", "artnr": arter[0]})).status_code == 400
    ml = (await web["ad"].get("/api/learn/lyd/ml")).json()
    assert len(ml) == 1 and (await web["ad"].delete(f"/api/learn/lyd/ml/{ml[0]['id']}")).status_code == 200
    # ryd hentede optagelser
    assert (await web["su"].delete("/api/admin/lydquiz/cache")).json()["slettet"] >= 5
    await flags(db, lydquiz="off")
    assert (await web["a1"].get("/api/learn/lyd/grupper")).status_code == 404
