"""Læring: Phylloscopus-kurset som startindhold og automatiske medie-referencer (kræver ARTICLES_TEST_DSN)."""

import pytest
from sqlalchemy import text

from test_learn_play import DSN, db, flags, web  # noqa: F401 – fixtures

pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


async def test_phylloscopus_importeres_som_kladde_en_gang(web, db):  # noqa: F811
    from portal.services import learning

    async with db() as s:
        assert await learning.seed_packs(s) == 16  # Se på fugle, Phylloscopus-, måge-, rovfugle-, havfugle-, vadefugle-, 100-arter- og trækkurset, hver med en øvelse
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
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'rovfugle'"))).scalar() == 19
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'havfugle'"))).scalar() == 16
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'vadefugle'"))).scalar() == 25
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'fugle-100'"))).scalar() == 31
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'fugletraek'"))).scalar() == 22
        # trækkurset beder om nattrækkald – typen overlever importens rensning (både i blokke og i aktiviteternes lyd_ref)
        tb = (await s.execute(text("SELECT blocks FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'fugletraek' ORDER BY position"))).scalars().all()
        assert any(b.get("type") == "lyd" and b["ref"].get("type") == "nattraek" for bl in tb for b in bl)
        assert (await s.execute(text("""SELECT count(*) FROM questions q JOIN courses c ON c.id = q.course_id
                                        WHERE c.slug = 'fugletraek' AND q.config->'lyd_ref'->>'type' = 'nattraek'"""))).scalar() >= 16
        # 100-arter-kurset beder om fotos af et bestemt køn (han/hun) – filteret overlever importens rensning
        fb = (await s.execute(text("SELECT blocks FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'fugle-100' ORDER BY position"))).scalars().all()
        assert any(r.get("koen") == "Hun" for bl in fb for b in bl for r in [b.get("ref") or {}] + (b.get("refs") or []))
        assert (await s.execute(text("SELECT count(*) FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'se-paa-fugle'"))).scalar() == 21
        st = {r[0] for r in (await s.execute(text("SELECT DISTINCT q.type FROM questions q JOIN courses c ON c.id = q.course_id WHERE c.slug = 'se-paa-fugle'"))).all()}
        assert {"valg", "silhuet", "raekkefoelge", "tekst", "refleksion", "foto_art", "foto_sammenlign", "lyd_art", "obs_odds", "obs_habitat"} <= st
        # «Se på fugle» afløser Trækrutes fem metodekurser: de importeres ikke, og forløbet peger på det nye kursus
        assert await learning.seed_traekrute(s) == 11
        assert not (await s.execute(text("SELECT 1 FROM courses WHERE slug = ANY(:s)"), {"s": list(learning.SKIP_COURSES)})).first()
        pc = (await s.execute(text("""SELECT c.slug FROM lr_path_items i JOIN lr_paths p ON p.id = i.path_id JOIN courses c ON c.id = i.course_id
                                       WHERE p.slug = 'fra-nysgerrig-til-feltbisse' AND i.kind = 'kursus' ORDER BY i.position"""))).scalars().all()
        assert pc == ["se-paa-fugle", "selvstaendigt-feltarbejde"]
        # vadefuglekurset beder om bestemte dragter (sommer/vinter) – filteret overlever importens rensning
        vb = (await s.execute(text("SELECT blocks FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'vadefugle' ORDER BY position"))).scalars().all()
        assert any(r.get("dragt") == "Vinterdragt" for bl in vb for b in bl for r in [b.get("ref") or {}] + (b.get("refs") or []))
        # rovfuglekurset beder om flugtfotos – filteret overlever importens rensning
        rb = (await s.execute(text("SELECT blocks FROM lr_units u JOIN courses c ON c.id = u.course_id WHERE c.slug = 'rovfugle' ORDER BY position"))).scalars().all()
        assert any(r.get("adfaerd") == "Flyvende" for bl in rb for b in bl for r in [b.get("ref") or {}] + (b.get("refs") or []))
    # kladden har ingen blokerende fejl (der er ingen fotos i testbasen at tjekke rettigheder på)
    await flags(db, laering_portal="on", laering_byg="on")
    ov = {c["slug"]: c for c in (await web["ad"].get("/api/learn/byg/oversigt")).json()}
    assert ov["phylloscopus"]["fejl"] == 0 and ov["maager"]["fejl"] == 0 and ov["rovfugle"]["fejl"] == 0 and ov["havfugle"]["fejl"] == 0 and ov["vadefugle"]["fejl"] == 0
    assert ov["se-paa-fugle"]["fejl"] == 0 and ov["se-paa-fugle-traening"]["fejl"] == 0
    assert ov["fugle-100"]["fejl"] == 0 and ov["fugle-100-traening"]["fejl"] == 0
    assert ov["fugletraek"]["fejl"] == 0 and ov["fugletraek-traening"]["fejl"] == 0
    # lektionerne kan vises (redaktørens forhåndsvisning): blokke med referencer løses – uden fotos/lyd i basen bliver de blot tomme
    r = await web["ad"].get("/api/learn/courses/phylloscopus/units?preview=true")
    assert r.status_code == 200, r.text
    units = r.json()["units"]
    assert len(units) == 13 and "blocks" not in units[0] and units[0]["n_blocks"] > 0  # kun lektionslisten – indholdet hentes pr. lektion
    html = ""
    for u in units:
        ind = await web["ad"].get(f"/api/learn/units/{u['id']}/indhold?preview=true")
        assert ind.status_code == 200, ind.text
        html += "".join(b.get("html", "") for b in ind.json()["blocks"])
    assert "<table>" in html
    uid = units[3]["id"]
    s = await web["ad"].get(f"/api/learn/units/{uid}/session?preview=true")
    assert s.status_code == 200, s.text
    r = await web["ad"].get("/api/learn/courses/se-paa-fugle/units?preview=true")
    assert r.status_code == 200, r.text
    assert len(r.json()["units"]) == 21
    for u in r.json()["units"]:  # alle lektioner kan afspilles (silhuetter, generatorer uden data springes over)
        s = await web["ad"].get(f"/api/learn/units/{u['id']}/session?preview=true")
        assert s.status_code == 200, s.text
    r = await web["ad"].get("/api/learn/courses/fugle-100/units?preview=true")
    assert r.status_code == 200, r.text
    assert len(r.json()["units"]) == 31
    for u in r.json()["units"]:  # alle lektioner i 100-arter-kurset kan afspilles (fotoreferencer uden fotos i basen bliver blot tomme)
        s = await web["ad"].get(f"/api/learn/units/{u['id']}/session?preview=true")
        assert s.status_code == 200, s.text


async def _photo(s, pid, nf, artnr, latin, likes, ret, samtykke=False, flere=False):
    await s.execute(text("INSERT INTO gal_photographers (id, navn, navn_norm, standard_rettighed, dof_samtykke) VALUES (:i, :n, :n, :r, :s) ON CONFLICT (id) DO NOTHING"),
                    {"i": pid, "n": f"Fotograf {pid}", "r": ret, "s": samtykke})
    await s.execute(text("INSERT INTO gal_photos (id, netfugl_id, status, source, views, likes, photographer_id) VALUES (:i, :nf, 'publiceret', 'netfugl', 0, :l, :i)"),
                    {"i": pid, "nf": nf, "l": likes})
    await s.execute(text("INSERT INTO gal_birds (photo_id, pos, artnr, navn, latin) VALUES (:i, 0, :a, 'x', :lt)"), {"i": pid, "a": artnr, "lt": latin})
    if flere:
        await s.execute(text("INSERT INTO gal_birds (photo_id, pos, artnr, navn, latin) VALUES (:i, 1, 13110, 'x', 'Phylloscopus collybita')"), {"i": pid})


async def test_foto_reference_bruger_alle_publicerede_fotos(web, db):  # noqa: F811
    from portal.services import learn_build, learn_play

    async with db() as s:
        await _photo(s, 901, 5001, 13120, "Phylloscopus trochilus", 50, 2)            # mest likede (rettighed 2 må også bruges)
        await _photo(s, 902, 5002, 13120, "Phylloscopus trochilus", 10, 1)
        await _photo(s, 904, 5004, 13120, "Phylloscopus trochilus", 99, 1, flere=True)   # to fugle – aldrig valgt
        await _photo(s, 905, 5005, None, "Phylloscopus canariensis", 1, 2)            # art uden artsnummer
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 13120}) == 901
        assert await learn_play.photo_by_ref(s, {"artnr": 13120, "n": 1}) == 902
        assert await learn_play.photo_by_ref(s, {"artnr": 13120, "n": 2}) is None
        assert await learn_play.photo_by_ref(s, {"nf": 5002, "artnr": 13120}) == 902     # bestemt foto
        assert await learn_play.photo_by_ref(s, {"id": 902}) == 902
        assert await learn_play.photo_by_ref(s, {"latin": "Phylloscopus canariensis"}) == 905
        assert await learn_play.photo_by_ref(s, {"nf": 9999}) is None
        assert await learn_play.photo_by_ref(s, None) is None
        # byggeren giver ingen rettighedsfejl – kun en advarsel, når fotobiblioteket mangler et foto af arten
        st = {"course": {"kind": "kursus", "learn": [{"title": "x", "text": "y"}], "title": "T"},
              "units": [{"id": 1, "title": "L", "lesson_md": "x", "acts": [], "blocks": [{"type": "foto", "photo_id": 901}, {"type": "foto", "ref": {"artnr": 13110}}]}]}
        w = await learn_build.warnings(s, st)
        assert not [x for x in w if x["level"] == "fejl"]
        assert any("intet foto i fotobiblioteket" in x["msg"] for x in w)


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
        # nattrækkald: en «nocturnal flight call» foretrækkes; findes ingen, bruges artens almindelige kald
        await s.execute(text("""INSERT INTO snd_recordings (source, ext_id, artnr, latin, rtype, page_url, file_url) VALUES
            ('xc', '2', 12010, 'Turdus iliacus', 'call', 'u', 'f'), ('xc', '3', 12010, 'Turdus iliacus', 'nocturnal flight call', 'u', 'f')"""))
        await s.commit()
        vin_nat = (await s.execute(text("SELECT id FROM snd_recordings WHERE ext_id = '3'"))).scalar()
        assert await learn_play.rec_by_ref(s, {"artnr": 12010, "type": "nattraek"}) == vin_nat
        assert await learn_play.rec_by_ref(s, {"artnr": 12950, "type": "nattraek"}) == nord  # intet trækkald – det almindelige kald
    assert learn_build.clean_blocks([{"type": "lyd", "ref": {"artnr": 12010, "type": "nattraek"}}])[0]["ref"] == {"artnr": 12010, "type": "nattraek"}
    clean = learn_build.clean_blocks([{"type": "foto", "ref": {"nf": "12", "artnr": 13120, "latin": "x" * 200, "evil": "<script>", "type": "andet"}},
                                      {"type": "fotoraekke", "refs": [{"nf": 1}, "nej", {"n": "abc"}], "labels": ["A", 3]}])
    assert clean[0]["ref"] == {"nf": 12, "artnr": 13120, "latin": "x" * 80}
    assert clean[1]["refs"] == [{"nf": 1}] and clean[1]["labels"] == ["A", "3"]


def test_xc_soegning_med_underart():
    from portal.services import sound_quiz

    s = {"min_quality": "A", "area": "alle", "extra": ""}
    assert "ssp:tristis" in sound_quiz.xc_query("Phylloscopus collybita tristis", s, "call")
    assert 'type:"nocturnal flight call"' in sound_quiz.xc_query("Turdus iliacus", s, sound_quiz.NIGHT_TYPE)  # flerordstype i anførselstegn
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
        # adfærd: et flugtfoto vælges frem for det mest likede siddende foto
        await s.execute(text("INSERT INTO gal_codes (kind, code, text, sort) VALUES ('adfaerd', 'fl', 'Flyvende', 1) ON CONFLICT DO NOTHING"))
        await s.execute(text("UPDATE gal_birds SET adfaerd = 'fl' WHERE photo_id = 912"))
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "adfaerd": "Flyvende"}) == 912
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "adfaerd": "Flyvende", "alder": "Adult (udfarvet)"}) is None
        # dragt: vadefugle skifter dragt, så et erstatningsfoto skal vise den dragt, teksten beskriver
        await s.execute(text("INSERT INTO gal_codes (kind, code, text, sort) VALUES ('dragt', 'v', 'Vinterdragt', 2) ON CONFLICT DO NOTHING"))
        await s.execute(text("UPDATE gal_birds SET dragt = 'v' WHERE photo_id = 912"))
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "dragt": "Vinterdragt"}) == 912
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "dragt": "Sommerdragt/yngledragt"}) is None
        # køn: et erstatningsfoto til «han»/«hun» skal vise samme køn
        await s.execute(text("INSERT INTO gal_codes (kind, code, text, sort) VALUES ('koen', 'hun', 'Hun', 2) ON CONFLICT DO NOTHING"))
        await s.execute(text("UPDATE gal_birds SET koen = 'hun' WHERE photo_id = 912"))
        await s.commit()
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "koen": "Hun"}) == 912
        assert await learn_play.photo_by_ref(s, {"artnr": 5930, "koen": "Han"}) is None


async def test_forsidebillede_og_banner(web, db):  # noqa: F811
    from portal.cache import kv
    from portal.services import learn_play, learning

    async with db() as s:
        await learning.seed_packs(s)
        await s.execute(text("UPDATE courses SET cover_ref = NULL WHERE slug = 'maager'"))
        await s.commit()
        await learning.seed_packs(s)  # en redaktør har fjernet billedet: en ny opstart lægger det ikke tilbage
        assert (await s.execute(text("SELECT cover_ref FROM courses WHERE slug = 'maager'"))).scalar() is None
        await s.execute(text("DELETE FROM settings WHERE key = 'laering_seed_maager_v1_cover'"))
        await s.commit()
        s.expunge_all()
        await learning.seed_packs(s)  # efterfyldning (én gang): kurser importeret før forsidebilleder får pakkens billede
        assert (await s.execute(text("SELECT cover_ref FROM courses WHERE slug = 'maager'"))).scalar() == {"artnr": 5930, "nf": 120310}
        await s.execute(text("INSERT INTO content_media (id, owner_kind, kind, storage_key, filename, mime, size, alt, caption, credit, sha256, sort) VALUES (9901, 'galleri', 'billede', 'x/y.jpg', 'y.jpg', 'image/jpeg', 1, '', '', 'Fotograf', 'abc', 0) ON CONFLICT DO NOTHING"))
        await _photo(s, 931, 120310, 5930, "Larus cachinnans", 99, 2)
        await s.execute(text("UPDATE gal_photos SET media_id = 9901 WHERE id = 931"))
        await s.commit()
        c = (await s.execute(select_course("maager"))).scalar_one()
        cover = await learn_play.course_cover(s, c)
        assert cover and cover["url"].endswith("x/y.jpg")
        for k in [k async for k in kv().scan_iter("lr:banner:*")]:
            await kv().delete(k)
        b = await learn_play.banner(s)
        assert b and b["url"].endswith("x/y.jpg")


def select_course(slug):
    from sqlalchemy import select

    from portal.models import Course
    return select(Course).where(Course.slug == slug)
