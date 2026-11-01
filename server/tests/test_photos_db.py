"""Fotomodulets læsesider mod en rigtig Postgres: søgning og filtre, fotosiden, likes, visninger, kommentarer og medieadgang.
Springes over uden ARTICLES_TEST_DSN (se test_photo_import.py)."""

import os

import pytest
from sqlalchemy import select, text

from portal.services import photo_comments, photos
from test_photo_import import db, run, sample, write_dir  # noqa: F401 – fixture + hjælpere

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


@pytest.fixture
async def gallery(db, tmp_path):
    write_dir(tmp_path / "imports" / "a", sample())
    imp = await run(db, "a")
    assert imp.status == "faerdig", imp.log
    return db


async def ids(db, **q) -> list[int]:
    async with db() as s:
        out = await photos.search(s, q, cache=False)
        return [r["id"] for r in out["rows"]]


async def test_galleri_grupper_og_status(gallery):
    async with gallery() as s:
        out = await photos.search(s, {}, cache=False)
        assert out["total"] == 2  # foto 1 og 2 er grupperet: ét kort med «+1»
        by_title = {r["titel"]: r for r in out["rows"]}
        assert by_title["Foto 1"]["gruppe"] == 1 and by_title["Foto 3"]["gruppe"] == 0
        assert by_title["Foto 1"]["thumb"].startswith("/media/art/") and by_title["Foto 1"]["likes"] == 4 and by_title["Foto 1"]["views"] == 352
        await s.execute(text("UPDATE gal_photos SET status = 'afventer' WHERE netfugl_id = 3"))
        await s.commit()
    assert len(await ids(gallery)) == 1


async def test_filtre(gallery):
    async def titles(**q):
        async with gallery() as s:
            return sorted(r["titel"] for r in (await photos.search(s, q, cache=False))["rows"] if r["titel"])
    assert await titles(q="Foto 3") == ["Foto 3"]
    assert await titles(q="Tisvilde") == ["Foto 1", "Foto 3"]
    assert await titles(q="Anna") == ["Foto 3"]  # fotografens navn
    assert await titles(q="ravn") == []  # foto 2 (to fugle) har ingen titel; kortet vises ikke i titellisten
    async with gallery() as s:
        assert (await photos.search(s, {"q": "ravn"}, cache=False))["total"] == 1
        assert (await photos.search(s, {"art": "2"}, cache=False))["total"] == 1  # Ravn er hjælpefugl nr. 2 på foto 2
        assert (await photos.search(s, {"art": "1,2"}, cache=False))["total"] == 2
        assert (await photos.search(s, {"fra": "2026-10-28"}, cache=False))["total"] == 0
        assert (await photos.search(s, {"fra": "2026-10-27", "til": "2026-10-27", "md": "10"}, cache=False))["total"] == 2
        assert (await photos.search(s, {"md": "5"}, cache=False))["total"] == 0
        assert (await photos.search(s, {"koen": "han"}, cache=False))["total"] == 2
        assert (await photos.search(s, {"koen": "hun"}, cache=False))["total"] == 0
        assert (await photos.search(s, {"alder": "k1", "adf": "siddende"}, cache=False))["total"] == 2
        assert (await photos.search(s, {"dragt": "pragt"}, cache=False))["total"] == 1
        assert (await photos.search(s, {"tag": "tvivl_art"}, cache=False))["total"] == 1
        assert (await photos.search(s, {"land": "999"}, cache=False))["total"] == 0
        assert (await photos.search(s, {"lok": "tisvilde"}, cache=False))["total"] == 2
        assert (await photos.search(s, {"loknr": "100"}, cache=False))["total"] == 1
        pid = (await s.execute(text("SELECT photographer_id FROM gal_photos WHERE netfugl_id = 3"))).scalar()
        assert (await photos.search(s, {"fotograf": str(pid)}, cache=False))["total"] == 1
        for sort in photos.SORTS:
            assert (await photos.search(s, {"sort": sort}, cache=False))["total"] == 2
        assert len((await photos.search(s, {"per": "48", "side": "2"}, cache=False))["rows"]) == 0


async def test_fotoside_synlighed_og_rettighed(gallery):
    async with gallery() as s:
        pid = (await s.execute(text("SELECT id FROM gal_photos WHERE netfugl_id = 2"))).scalar()
        p = await photos.get(s, pid, user_id=1, moderator=False)
        assert [f["navn"] for f in p["fugle"]] == ["Dværgterne", "Ravn"] and p["fugle"][1]["dragt"] == "Pragtdragt"
        assert [g["id"] for g in p["gruppe"]] == sorted(g["id"] for g in p["gruppe"]) and len(p["gruppe"]) == 2
        assert p["rettighed"]["id"] == 2 and p["rettighed"]["egen"] is True  # arkivets rettighed er fotoets egen and "Personlig aftale" in p["rettighed"]["tekst"]
        assert p["url"].startswith("/media/art/") and p["kamera"]["model"] == "NIKON"
        await s.execute(text("UPDATE gal_photos SET status = 'afventer', user_id = 2 WHERE id = :i"), {"i": pid})
        await s.commit()
        assert await photos.get(s, pid, user_id=1, moderator=False) is None  # andre kan ikke se det
        assert (await photos.get(s, pid, user_id=2, moderator=False))["egen"] is True  # ejeren kan
        assert (await photos.get(s, pid, user_id=1, moderator=True)) is not None  # moderator kan
        assert await photos.by_netfugl(s, 2) == pid and await photos.by_netfugl(s, 77777) is None


async def test_like_og_visning(gallery):
    async with gallery() as s:
        pid = (await s.execute(text("SELECT id FROM gal_photos WHERE netfugl_id = 1"))).scalar()
        assert await photos.toggle_like(s, pid, 1) == {"liked": True, "likes": 5}
        assert await photos.toggle_like(s, pid, 2) == {"liked": True, "likes": 6}
        assert await photos.toggle_like(s, pid, 1) == {"liked": False, "likes": 5}
        await photos.register_view(s, pid, 1)
        await photos.register_view(s, pid, 1)  # samme bruger samme døgn tæller ikke igen
        await photos.register_view(s, pid, 2)
        assert (await s.execute(text("SELECT views FROM gal_photos WHERE id = :i"), {"i": pid})).scalar() == 2
        await s.execute(text("UPDATE gal_photos SET status = 'afvist' WHERE id = :i"), {"i": pid})
        await s.commit()
        with pytest.raises(LookupError):
            await photos.toggle_like(s, pid, 1)


async def test_kommentarer_og_interne_noter(gallery):
    from portal.models import User

    async with gallery() as s:
        pid = (await s.execute(text("SELECT id FROM gal_photos WHERE netfugl_id = 1"))).scalar()
        a, b = await s.get(User, 1), await s.get(User, 2)
        c1 = await photo_comments.add(s, a, pid, "Flot **fugl**", None, moderator=False)
        r1 = await photo_comments.add(s, b, pid, "Enig", c1.id, moderator=False)
        note = await photo_comments.add(s, b, pid, "Skal vi afvise?", None, moderator=True, intern=True)
        with pytest.raises(photo_comments.CommentError):
            await photo_comments.add(s, a, pid, "snyd", None, moderator=False, intern=True)
        assert r1.parent_id == c1.id and "<strong>fugl</strong>" in c1.body_html
        assert (await s.execute(text("SELECT comments FROM gal_photos WHERE id = :i"), {"i": pid})).scalar() == 2  # noten tæller ikke
        pub = await photo_comments.list_comments(s, pid, a.id, moderator=False)
        assert pub["interne"] == [] and len(pub["comments"]) == 1 and len(pub["comments"][0]["svar"]) == 1
        mod = await photo_comments.list_comments(s, pid, b.id, moderator=True)
        assert [n["id"] for n in mod["interne"]] == [note.id]
        # anmeldelse skjuler automatisk ved tærsklen, tælleren falder
        for uid in (2, 3):
            u = await s.get(User, uid)
            await photo_comments.report(s, u, c1.id, "spam", "")
        assert (await s.get(type(c1), c1.id)).status in ("synlig", "skjult")
        await photo_comments.remove_own(s, a, c1.id)
        assert (await s.execute(text("SELECT comments FROM gal_photos WHERE id = :i"), {"i": pid})).scalar() == 1


async def test_medieadgang(gallery):
    async with gallery() as s:
        pid, key = (await s.execute(text("SELECT p.id, m.storage_key FROM gal_photos p JOIN content_media m ON m.id = p.media_id WHERE p.netfugl_id = 1"))).one()
        info = await photos.media_info(s, key)
        assert info["photo"] and info["id"] == pid and info["status"] == "publiceret"
        other = await photos.media_info(s, "ff/" + "0" * 64 + ".jpg")
        assert other["photo"] is False


async def test_sjaeldenhed_og_taxon_via_krydslistemodulet(gallery):
    """Filteret sjældenhed følger DK-listen i krydslistemodulet; taxon-filteret bruger World Bird List-koblingen (photos_link_taxa)."""
    from portal.services import kl_admin, krydslister

    async with gallery() as s:
        assert (await photos.search(s, {"sj": "su"}, cache=False))["total"] == 0  # tabellerne findes, men er tomme
        await krydslister.seed(s)
        await s.execute(text("UPDATE kl_species_list_entries SET sjaeldenhed = 'su' WHERE list_id = (SELECT id FROM kl_species_lists WHERE key = 'dk') "
                             "AND taxon_id = (SELECT id FROM kl_taxa WHERE latin = 'Corvus corax')"))
        await s.commit()
        n = await kl_admin.link_netfugl_ids(s)
        assert n >= 1  # netfugl_id på kl_taxa fra fotoarkivets artsnumre (Ravn: 200 findes ikke i testdata, Dværgterne: 100)
        tid = (await s.execute(text("SELECT taxon_id FROM gal_birds WHERE latin = 'Corvus corax'"))).scalar()
        assert tid is not None
        assert (await photos.search(s, {"sj": "su"}, cache=False))["total"] == 0  # filteret ser kun hovedarten (pos 0): Ravn er hjælpefugl
        await s.execute(text("UPDATE kl_species_list_entries SET sjaeldenhed = 'sjaelden' WHERE taxon_id = (SELECT id FROM kl_taxa WHERE latin = 'Sternula albifrons')"))
        await s.commit()
        assert (await photos.search(s, {"sj": "sjaelden"}, cache=False))["total"] == 2  # to kort: fotos 1+2 (gruppe) og 3
        assert (await photos.search(s, {"sj": "su,almindelig"}, cache=False))["total"] == 0
        assert (await photos.search(s, {"taxon": str(tid)}, cache=False))["total"] == 1
