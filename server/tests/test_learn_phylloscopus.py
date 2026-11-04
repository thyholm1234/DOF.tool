"""Læring: Phylloscopus-kurset som startindhold og automatiske medie-referencer (kræver ARTICLES_TEST_DSN)."""

import pytest
from sqlalchemy import text

from test_learn_play import DSN, db, flags, web  # noqa: F401 – fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


async def test_phylloscopus_importeres_som_kladde_en_gang(web, db):  # noqa: F811
    from portal.services import learning

    async with db() as s:
        assert await learning.seed_packs(s) == 4  # Phylloscopus- og mågekurset, hver med en øvelse
        assert await learning.seed_packs(s) == 0  # kun én gang
        c = (await s.execute(text("SELECT id, published, kind FROM courses WHERE slug = 'phylloscopus'"))).mappings().one()
        assert not c["published"] and c["kind"] == "kursus"
        units = (await s.execute(text("SELECT title, blocks, est_minutes FROM lr_units WHERE course_id = :c ORDER BY position"), {"c": c["id"]})).mappings().all()
        assert len(units) == 13 and all(u["est_minutes"] for u in units)
        # referencerne overlever importens rensning
        refs = [b for u in units for b in u["blocks"] if b.get("ref") or b.get("refs")]
        assert any(b["type"] == "lyd" and b["ref"].get("xc") == "420696" for b in refs)
        assert any(b["type"] == "fotoraekke" and b["refs"][0].get("nf") and b.get("labels") for b in refs)
        types = {r[0] for r in (await s.execute(text("SELECT DISTINCT type FROM questions WHERE course_id = :c"), {"c": c["id"]})).all()}
        assert {"valg", "raekkefoelge", "tekst", "refleksion", "foto_art", "lyd_art", "foto_sammenlign"} <= types
        assert (await s.execute(text("SELECT kind FROM courses WHERE slug = 'phylloscopus-traening'"))).scalar() == "ovelse"
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'maager'"))).scalar() == 14
    # kladden har ingen blokerende fejl (der er ingen fotos i testbasen at tjekke rettigheder på)
    await flags(db, laering_portal="on", laering_byg="on")
    ov = {c["slug"]: c for c in (await web["ad"].get("/api/learn/byg/oversigt")).json()}
    assert ov["phylloscopus"]["fejl"] == 0 and ov["maager"]["fejl"] == 0
    # lektionerne kan vises (redaktørens forhåndsvisning): blokke med referencer løses – uden fotos/lyd i basen bliver de blot tomme
    r = await web["ad"].get("/api/learn/courses/phylloscopus/units?preview=true")
    assert r.status_code == 200, r.text
    units = r.json()["units"]
    assert len(units) == 13 and "<table>" in "".join(b.get("html", "") for u in units for b in u["blocks"])
    uid = units[3]["id"]
    s = await web["ad"].get(f"/api/learn/units/{uid}/session?preview=true")
    assert s.status_code == 200, s.text


async def _photo(s, pid, nf, artnr, latin, likes, ret, samtykke=False, flere=False):
    await s.execute(text("INSERT INTO gal_photographers (id, navn, navn_norm, standard_rettighed, dof_samtykke) VALUES (:i, :n, :n, :r, :s) ON CONFLICT (id) DO NOTHING"),
                    {"i": pid, "n": f"Fotograf {pid}", "r": ret, "s": samtykke})
    await s.execute(text("INSERT INTO gal_photos (id, netfugl_id, status, source, views, likes, photographer_id) VALUES (:i, :nf, 'publiceret', 'netfugl', 0, :l, :i)"),
                    {"i": pid, "nf": nf, "l": likes})
    await s.execute(text("INSERT INTO gal_birds (photo_id, pos, artnr, navn, latin) VALUES (:i, 0, :a, 'x', :lt)"), {"i": pid, "a": artnr, "lt": latin})
    if flere:
        await s.execute(text("INSERT INTO gal_birds (photo_id, pos, artnr, navn, latin) VALUES (:i, 1, 13110, 'x', 'Phylloscopus collybita')"), {"i": pid})


async def test_foto_reference_bruger_kun_fotos_dof_maa_bruge(web, db):  # noqa: F811
    from portal.services import learn_build, learn_play

    async with db() as s:
        await _photo(s, 901, 5001, 13120, "Phylloscopus trochilus", 50, 2)            # mest likede, men rettighed 2
        await _photo(s, 902, 5002, 13120, "Phylloscopus trochilus", 10, 1)            # må bruges
        await _photo(s, 903, 5003, 13120, "Phylloscopus trochilus", 5, 2, samtykke=True)  # samtykke
        await _photo(s, 904, 5004, 13120, "Phylloscopus trochilus", 99, 1, flere=True)   # to fugle – aldrig valgt
        await _photo(s, 905, 5005, None, "Phylloscopus canariensis", 1, 1)            # art uden artsnummer
        await _photo(s, 906, 5006, 13110, "Phylloscopus collybita", 9, 2)             # gransanger: kun et foto uden rettighed
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 13120}) == 902
        assert await learn_play.photo_by_ref(s, {"artnr": 13120, "n": 1}) == 903
        assert await learn_play.photo_by_ref(s, {"artnr": 13120, "n": 2}) is None       # ikke flere, der må bruges
        assert await learn_play.photo_by_ref(s, {"nf": 5003, "artnr": 13120}) == 903     # bestemt foto, der må bruges
        assert await learn_play.photo_by_ref(s, {"nf": 5001, "artnr": 13120}) == 902     # bestemt foto uden rettighed → et der må
        assert await learn_play.photo_by_ref(s, {"nf": 5001}) is None                     # aldrig et foto uden rettighed
        assert await learn_play.photo_by_ref(s, {"artnr": 13110}) is None
        assert await learn_play.photo_by_ref(s, {"latin": "Phylloscopus canariensis"}) == 905
        assert await learn_play.photo_by_ref(s, None) is None
        # byggeren: ingen røde rettighedsfejl for automatiske fotos – kun en advarsel, når der intet tilladt foto er
        st = {"course": {"kind": "kursus", "learn": [{"title": "x", "text": "y"}], "title": "T"},
              "units": [{"id": 1, "title": "L", "lesson_md": "x", "acts": [], "blocks": [{"type": "foto", "ref": {"nf": 5001, "artnr": 13120}},
                                                                                   {"type": "foto", "ref": {"artnr": 13110}}]}]}
        w = await learn_build.warnings(s, st)
        assert not [x for x in w if x["level"] == "fejl"]
        assert any("Gransanger" in x["msg"] or "13110" in x["msg"] for x in w if x["level"] == "advarsel")


async def test_lyd_reference_og_rensning(db):  # noqa: F811
    from portal.services import learn_build, learn_play

    async with db() as s:
        await s.execute(text("""INSERT INTO snd_recordings (source, ext_id, artnr, latin, rtype, page_url, file_url) VALUES
            ('xc', '420696', 12950, 'Phylloscopus borealis', 'call', 'u', 'f'), ('xc', '1', 0, 'Phylloscopus canariensis', 'song', 'u', 'f')"""))
        await s.commit()
        nord = (await s.execute(text("SELECT id FROM snd_recordings WHERE ext_id = '420696'"))).scalar()
        kan = (await s.execute(text("SELECT id FROM snd_recordings WHERE ext_id = '1'"))).scalar()
        assert await learn_play.rec_by_ref(s, {"xc": "420696"}) == nord
        assert await learn_play.rec_by_ref(s, {"artnr": 12950, "type": "kald"}) == nord
        assert await learn_play.rec_by_ref(s, {"latin": "Phylloscopus canariensis", "type": "sang"}) == kan
    clean = learn_build.clean_blocks([{"type": "foto", "ref": {"nf": "12", "artnr": 13120, "latin": "x" * 200, "evil": "<script>", "type": "andet"}},
                                      {"type": "fotoraekke", "refs": [{"nf": 1}, "nej", {"n": "abc"}], "labels": ["A", 3]}])
    assert clean[0]["ref"] == {"nf": 12, "artnr": 13120, "latin": "x" * 80}
    assert clean[1]["refs"] == [{"nf": 1}] and clean[1]["labels"] == ["A", "3"]


def test_xc_soegning_med_underart():
    from portal.services import sound_quiz

    s = {"min_quality": "A", "area": "alle", "extra": ""}
    assert "ssp:tristis" in sound_quiz.xc_query("Phylloscopus collybita tristis", s, "call")
    data = {"recordings": [{"id": 1, "gen": "Phylloscopus", "sp": "collybita", "ssp": "tristis", "file": "//x/1.mp3", "length": "0:10"},
                           {"id": 2, "gen": "Phylloscopus", "sp": "collybita", "ssp": "", "file": "//x/2.mp3", "length": "0:10"}]}
    assert [r["ext_id"] for r in sound_quiz._parse(data, "Phylloscopus collybita tristis", 60)] == ["1"]
    assert [r["ext_id"] for r in sound_quiz._parse(data, "Phylloscopus collybita", 60)] == ["1", "2"]


async def test_foto_reference_med_alder(db):  # noqa: F811
    from portal.services import learn_play

    async with db() as s:
        await s.execute(text("INSERT INTO gal_codes (kind, code, text, sort) VALUES ('alder', 'k1', '1. kalenderår', 1), ('alder', 'ad', 'Adult (udfarvet)', 9) ON CONFLICT DO NOTHING"))
        await _photo(s, 911, 6001, 5930, "Larus cachinnans", 50, 1)
        await _photo(s, 912, 6002, 5930, "Larus cachinnans", 10, 1)
        await s.execute(text("UPDATE gal_birds SET alder = 'ad' WHERE photo_id = 911"))
        await s.execute(text("UPDATE gal_birds SET alder = 'k1' WHERE photo_id = 912"))
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 5930}) == 911
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "alder": "1. kalenderår"}) == 912
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "alder": "2. kalenderår"}) is None  # aldrig et foto af forkert alder
