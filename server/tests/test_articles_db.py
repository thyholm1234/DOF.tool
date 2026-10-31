"""Artikelmodulet mod en rigtig Postgres og Valkey (SQL, dansk fuldtekst, trigram, import, kommentarer, notifikationer, nyheder).

Springes over, medmindre ARTICLES_TEST_DSN er sat. Start fx to engangs-containere og kør testen:

    docker run -d --rm --name art-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=arttest -p 127.0.0.1:55432:5432 postgis/postgis:17-3.5
    docker run -d --rm --name art-kv -p 127.0.0.1:56379:6379 valkey/valkey:8.1
    ARTICLES_TEST_DSN=postgresql+asyncpg://postgres:test@127.0.0.1:55432/arttest ARTICLES_TEST_REDIS=redis://127.0.0.1:56379/0 \\
        python -m pytest tests/test_articles_db.py

Testen sletter og genopretter artikeltabellerne i den database, den peger på – brug aldrig en rigtig database."""

import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
import redis.asyncio as aioredis
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")

TABLES = ["roles", "users", "species", "news", "notifications", "settings", "comment_bans", "push_subscriptions", "art_imports", "content_media",
          "art_articles", "art_tags", "art_article_tags", "art_species", "art_comments", "art_comment_reports", "art_subscriptions", "art_revisions"]


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from portal import cache
    from portal.config import get_settings
    from portal.db import Base
    from portal.models import EXTRA_DDL, Species, User
    from portal.dbops import ddl_groups
    from portal.services import article_import, article_notify

    engine = create_async_engine(DSN)
    from portal.dbgroups import USER_TABLES

    # Alle brugergruppens tabeller (/api/me slår også DOFbasen-, Netfugl- og DKU-rettigheder op) plus arter
    tables = [t for t in Base.metadata.sorted_tables if t.name in USER_TABLES or t.name == "species"]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        for ddl in EXTRA_DDL:
            if "art_articles" in ddl and "users" in ddl_groups(ddl):
                await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(article_import, "SessionLocal", factory)
    monkeypatch.setattr(article_notify, "SessionLocal", factory)
    s = get_settings()
    monkeypatch.setattr(s, "media_dir", str(tmp_path / "media"))
    monkeypatch.setattr(s, "article_import_dir", str(tmp_path / "imports"))
    monkeypatch.setattr(s, "s3_endpoint", "")
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    await kv.flushdb()
    monkeypatch.setattr(cache, "_client", kv)
    async with factory() as session:
        for i, (kode, role) in enumerate([("A1", "user"), ("A2", "user"), ("ED", "editor"), ("MO", "moderator"), ("SU", "superadmin")], 1):
            session.add(User(id=i, obserkode=kode, navn=f"Bruger {kode}", role=role))
        for nr, navn, latin, sort in [(1, "Dværgterne", "Sternula albifrons", 10), (2, "Sortgrå Ryle", "Calidris maritima", 20),
                                      (3, "Almindelig Ryle", "Calidris alpina", 30), (4, "Ravn", "Corvus corax", 40), (5, "Hjejle", "Pluvialis apricaria", 50)]:
            session.add(Species(artnr=nr, navn=navn, latin=latin, sortering=sort, art_type="art"))
        await session.commit()
    yield factory
    await kv.aclose()
    await engine.dispose()


def jpeg(color=(200, 30, 30), size=(900, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def odd_jpeg(color=(10, 120, 10)) -> bytes:
    """Gyldig JPEG med skrald efter første segment (APP0): metadatafjerneren giver op, Pillow kan læse den."""
    j = jpeg(color)
    end = 4 + int.from_bytes(j[4:6], "big")
    return j[:end] + b"\x00\x00" + j[end:]


def pdf() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), (240, 240, 240)).save(buf, "PDF")
    return buf.getvalue()


def make_zip(path, articles: dict[int, dict], extra: dict[str, bytes] | None = None, root="netfugl-artikler"):
    """articles: nr → {meta, md, comments, files: {relpath: bytes}}"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/MANIFEST.md", "# arkiv")
        z.writestr(f"{root}/_index.json", json.dumps([{"id": 1}]))  # bevidst ufuldstændig
        z.writestr(f"{root}/Turrapporter/0001/meta.json", "{}")
        for no, a in articles.items():
            d = f"{root}/{no:04d}"
            meta = {"schema_version": 1, "id": no, "titel": a["titel"], "dato": a.get("dato", "2001-05-09T20:27"), "forfattere": a.get("forfattere", [{"navn": "Pia Pil", "netfugl_forfatter_id": 7}]),
                    "emneord": a.get("emneord", []), "billeder": a.get("billeder", []), "vedhaeftede_filer": a.get("vedhaeftede_filer", []), "antal_laest_ved_hentning": 41,
                    "kilde_url": f"https://www.netfugl.dk/artikler/{no}/x", "fejl": []}
            z.writestr(f"{d}/meta.json", json.dumps(meta))
            z.writestr(f"{d}/artikel.md", a["md"])
            z.writestr(f"{d}/kommentarer.json", json.dumps(a.get("comments", [])))
            z.writestr(f"{d}/original.html", "<html>ikke importeret</html>")
            for rel, data in a.get("files", {}).items():
                z.writestr(f"{d}/{rel}", data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def sample(path):
    make_zip(path, {
        155: {"titel": "Aztekerterne på Langli", "md": "# Aztekerterne på Langli\n\n**Aztekerterne på Langli**\n\nVi så Dværgterner og en Sortgrå Ryle på Langli. Se [den anden](../0300/artikel.md).\n",
              "emneord": [{"navn": "Nyhed", "netfugl_tag_id": 1054}, {"navn": "Dværgterne", "netfugl_tag_id": 77}, {"navn": "2001", "netfugl_tag_id": 5}]},
        300: {"titel": "Hjejler på marken", "md": "# Hjejler på marken\n\n![](billeder/001-a.jpg)\n\nEn flok **Hjejle** ved Skagen. Ravn sås også. Vedhæftning: [PDF](filer/rapport.pdf)\n\n![ekstra](billeder/002-b.jpg)\n",
              "emneord": [{"navn": "Nyhed", "netfugl_tag_id": 1054}, {"navn": "Skagen", "netfugl_tag_id": 90}],
              "billeder": [{"fil": "billeder/001-a.jpg", "alt": "Hjejler", "forsidebillede": True}, {"fil": "billeder/002-b.jpg", "alt": "Ekstra", "forsidebillede": False}],
              "vedhaeftede_filer": [{"fil": "filer/rapport.pdf", "titel": "Rapport", "type": "pdf", "tekst_fil": "filer/rapport.ocr.md"}],
              "files": {"billeder/001-a.jpg": jpeg(), "billeder/002-b.jpg": jpeg((10, 120, 200)), "filer/rapport.pdf": pdf(),
                        "filer/rapport.ocr.md": b"# Rapport\n\n> Tekst udtrukket fra `rapport.pdf` (OCR)\n\n## Side 1\n\nKalanderlaerke og Hvidhals observeret i marts.\n"},
              "comments": [{"id": "2", "forfatter": "Mark D", "forfatter_id": 5, "dato": "2020-10-07T09:26", "tekst_md": "Fedt <b>artikel</b>!", "svar": [
                  {"id": None, "forfatter": "Pia", "forfatter_id": 6, "dato": "2020-10-08T10:00", "tekst_md": "Enig", "svar": []}]}]},
        12: {"titel": "Kommende nye arter", "md": "# Kommende nye arter\n\n", "emneord": [{"navn": "Artikel", "netfugl_tag_id": 1055}, {"navn": "Pandion", "netfugl_tag_id": 300}],
             "vedhaeftede_filer": [{"fil": "filer/pandion.pdf", "titel": "Artiklen som PDF", "type": "pdf", "tekst_fil": "filer/pandion.ocr.md"}],
             "files": {"filer/pandion.pdf": pdf(), "filer/pandion.ocr.md": b"# T\n\n> note\n\n## Side 1\n\nHvem er paa vej ind? Almindelig Ryle og Dvaergterne i Danmark.\n"}},
    })


async def do_import(factory, tmp_path, zip_path, **options):
    from portal.models import ArtImport
    from portal.services import article_import

    async with factory() as s:
        imp = ArtImport(filename="a.zip", size=zip_path.stat().st_size, status="klar", options={"fetch_images": False, **options}, uploaded_by=3)
        s.add(imp)
        await s.commit()
        iid = imp.id
    d = tmp_path / "imports"
    d.mkdir(exist_ok=True)
    (d / f"{iid}.zip").write_bytes(zip_path.read_bytes())
    await article_import.run_import(iid)
    async with factory() as s:
        return await s.get(ArtImport, iid)


async def test_import_is_complete_and_idempotent(db, tmp_path):
    zp = tmp_path / "a.zip"
    sample(zp)
    imp = await do_import(db, tmp_path, zp)
    assert imp.status == "faerdig", imp.log
    assert imp.stats["artikler_i_zip"] == 3 and imp.stats["oprettet"] == 3 and imp.stats["fejl"] == 0, imp.stats
    assert imp.stats["billeder"] == 2 and imp.stats["pdf"] == 2 and imp.stats["kommentarer"] == 2
    assert any("_index.json nævner 1" in x["besked"] for x in imp.log)  # indekset stoles ikke på
    async with db() as s:
        a = (await s.execute(text("SELECT * FROM art_articles WHERE netfugl_id = 300"))).mappings().one()
        assert a["title"] == "Hjejler på marken" and a["status"] == "publiceret" and a["source"] == "netfugl" and a["views_imported"] == 41
        assert "![](media:" not in a["body_md"].split("\n")[0]  # forsidebilledet er fjernet fra toppen af teksten
        assert "media:" in a["body_md"] and "billeder/" not in a["body_md"] and "filer/" not in a["body_md"]
        assert a["cover_media_id"] is not None and "Hjejle" in a["body_text"]
        assert "Kalanderlaerke" in a["ocr_text"] and "Side 1" not in a["ocr_text"] and "udtrukket" not in a["ocr_text"]
        assert a["dato"].astimezone(timezone(timedelta(hours=2))).hour == 20  # dansk lokaltid uden tidszone → Europe/Copenhagen
        old = (await s.execute(text("SELECT * FROM art_articles WHERE netfugl_id = 155"))).mappings().one()
        assert "netfugl:300" in old["body_md"] and old["body_md"].startswith("Vi så")
        pdfa = (await s.execute(text("SELECT kind, ocr_text FROM art_articles WHERE netfugl_id = 12"))).mappings().one()
        assert pdfa["kind"] == "pdf" and "Dvaergterne" in pdfa["ocr_text"]
        tags = {r["navn"]: (r["kind"], r["artnr"], r["hidden"]) for r in (await s.execute(text("SELECT * FROM art_tags"))).mappings()}
        assert tags["Nyhed"] == ("type", None, True) and tags["2001"][0] == "aar" and tags["Pandion"][0] == "publikation"
        assert tags["Dværgterne"] == ("art", 1, False) and tags["Skagen"][0] == "emne"
        sp = {(r["article_id"], r["artnr"]): r["source"] for r in (await s.execute(text("SELECT * FROM art_species"))).mappings()}
        by_no = {r["netfugl_id"]: r["id"] for r in (await s.execute(text("SELECT id, netfugl_id FROM art_articles"))).mappings()}
        assert sp[(by_no[155], 1)] == "emneord" and sp[(by_no[155], 2)] == "tekst"
        assert sp[(by_no[300], 5)] == "tekst"
        assert (by_no[300], 4) not in sp or sp[(by_no[300], 4)] != "tekst"  # «Ravn» er tvetydigt og står først i sætningen
        assert sp[(by_no[12], 3)] == "ocr" and (by_no[12], 1) not in sp
        cm = (await s.execute(text("SELECT * FROM art_comments ORDER BY id"))).mappings().all()
        assert len(cm) == 2 and cm[0]["user_id"] is None and cm[0]["archive_author"] == "Mark D" and cm[1]["parent_id"] == cm[0]["id"]
        assert "<b>" not in cm[0]["body_html"]
        n_media = (await s.execute(text("SELECT count(*) FROM content_media"))).scalar()
        assert n_media == 4
        thumbs = (await s.execute(text("SELECT thumb_key FROM content_media WHERE kind = 'pdf'"))).scalars().all()
        assert all(thumbs)  # PDF-miniature af side 1 (poppler)
    # igen: intet nyt, ingen dubletter
    imp2 = await do_import(db, tmp_path, zp)
    assert imp2.stats["uaendret"] == 3 and imp2.stats["oprettet"] == 0 and imp2.stats["kommentarer"] == 0, imp2.stats
    imp3 = await do_import(db, tmp_path, zp, force=True)
    assert imp3.stats["opdateret"] == 3 and imp3.stats["kommentarer"] == 0
    async with db() as s:
        for t, n in [("art_articles", 3), ("content_media", 4), ("art_comments", 2), ("art_tags", 6)]:
            assert (await s.execute(text(f"SELECT count(*) FROM {t}"))).scalar() == n, t


async def test_dry_run_and_bad_zip(db, tmp_path):
    zp = tmp_path / "a.zip"
    sample(zp)
    imp = await do_import(db, tmp_path, zp, dry_run=True)
    assert imp.status == "faerdig" and imp.stats["oprettet"] == 3
    async with db() as s:
        assert (await s.execute(text("SELECT count(*) FROM art_articles"))).scalar() == 0
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"ikke en zip")
    imp = await do_import(db, tmp_path, bad)
    assert imp.status == "fejl" and imp.log


async def test_zip_slip_and_unknown_files_are_ignored(db, tmp_path):
    zp = tmp_path / "a.zip"
    make_zip(zp, {5: {"titel": "T", "md": "# T\n\ntekst", "files": {"billeder/x.svg": b"<svg onload=alert(1)>", "filer/x.exe": b"MZ", "billeder/ok.jpg": jpeg()}}},
             extra={"../evil.txt": b"x", "netfugl-artikler/0005/../../escape.txt": b"x"})
    imp = await do_import(db, tmp_path, zp)
    assert imp.status == "faerdig" and imp.stats["afvist"] == 2 and imp.stats["billeder"] == 1, imp.stats
    assert not (tmp_path.parent / "evil.txt").exists() and not (tmp_path / "escape.txt").exists()


async def test_broken_image_is_skipped_and_article_imported(db, tmp_path):
    zp = tmp_path / "a.zip"
    make_zip(zp, {2916: {"titel": "T", "md": "# T\n\n![](billeder/a.jpg)\n\n![](billeder/b.jpg)\n\n![](billeder/c.jpg)\n", "files": {
        "billeder/a.jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 7 + b"skrald",  # hverken metadatafjerneren eller Pillow kan læse den
        "billeder/b.jpg": odd_jpeg(),  # skrald mellem segmenter: gemmes igen med Pillow
        "billeder/c.jpg": jpeg((1, 2, 3))}}})
    imp = await do_import(db, tmp_path, zp)
    assert imp.status == "faerdig" and imp.stats["oprettet"] == 1 and imp.stats["fejl"] == 0 and imp.stats["billeder"] == 2, imp.stats
    assert any("billeder/a.jpg" in x["besked"] for x in imp.log)


async def test_local_edit_is_protected_from_reimport(db, tmp_path):
    from portal.models import ArtArticle, User
    from portal.services import article_edit

    zp = tmp_path / "a.zip"
    sample(zp)
    await do_import(db, tmp_path, zp)
    async with db() as s:
        ed = await s.get(User, 3)
        art = (await s.execute(select(ArtArticle).where(ArtArticle.netfugl_id == 155))).scalar_one()
        await article_edit.save(s, ed, {"title": "Redigeret titel", "body_md": "Ny tekst om Hjejle", "status": "publiceret", "tags": ["Rettet"],
                                        "authors": [{"navn": "Ed"}], "version": art.version}, art)
    imp = await do_import(db, tmp_path, zp, force=True)
    assert imp.stats["sprunget_lokalt"] == 1
    async with db() as s:
        art = (await s.execute(select(ArtArticle).where(ArtArticle.netfugl_id == 155))).scalar_one()
        assert art.title == "Redigeret titel" and art.locally_edited and art.version == 2
    imp = await do_import(db, tmp_path, zp, force=True, overwrite_local=True)
    async with db() as s:
        art = (await s.execute(select(ArtArticle).where(ArtArticle.netfugl_id == 155))).scalar_one()
        assert art.title == "Aztekerterne på Langli"


async def test_search_front_and_lists(db, tmp_path):
    from portal.services import article_settings, articles

    zp = tmp_path / "a.zip"
    sample(zp)
    await do_import(db, tmp_path, zp)
    async with db() as s:
        r = await articles.search(s, q="hjejler")  # dansk stemming: hjejler ≈ hjejle
        assert [x["title"] for x in r["rows"]] == ["Hjejler på marken"]
        assert "<mark>" in r["rows"][0]["headline"]
        r = await articles.search(s, q="kalanderlaerke")  # kun i OCR-teksten
        assert r["total"] == 1 and r["rows"][0]["headline_fra"] == "pdf"
        r = await articles.search(s, q="ernes", mode="praecis")  # delord
        assert r["total"] == 0
        r = await articles.search(s, q="terner", mode="praecis")
        assert [x["title"] for x in r["rows"]] == ["Aztekerterne på Langli"]
        assert (await articles.search(s, q="100%", mode="praecis"))["total"] == 0  # % er ikke et jokertegn
        r = await articles.search(s, q='"sortgrå ryle" -hjejle')
        assert r["total"] == 1
        tags = {t["navn"]: t["id"] for t in await articles.tags_overview(s, False)}
        assert (await articles.search(s, tags=[tags["Nyhed"]]))["total"] == 2
        assert (await articles.search(s, tags=[tags["Nyhed"], tags["Skagen"]]))["total"] == 1  # AND
        assert (await articles.search(s, arter=[3]))["total"] == 1 and (await articles.search(s, arter=[1, 5]))["total"] == 2  # OR
        assert (await articles.search(s, har_billeder=True))["total"] == 1 and (await articles.search(s, har_pdf=True))["total"] == 2
        assert (await articles.search(s, fra=2001, til=2001))["total"] == 3 and (await articles.search(s, fra=2002))["total"] == 0
        assert (await articles.search(s, type_="Artikel"))["total"] == 1
        assert (await articles.search(s, forfatter="Pia Pil"))["total"] == 3
        res = await articles.search(s, sort="mest_laest", per=2)
        assert res["total"] == 3 and len(res["rows"]) == 2 and res["facets"]["aar"] == [{"aar": 2001, "n": 3}]
        arter = {a["navn"]: a["n"] for a in await articles.species_overview(s, False)}
        assert arter["Dværgterne"] == 1 and arter["Sortgrå Ryle"] == 1
        assert (await articles.authors_overview(s, False)) == [{"navn": "Pia Pil", "n": 3}]
        front = await articles.front(s, False, await article_settings.get(s))
        assert front["total"] == 3 and front["hero"]["cover"] and front["hero"]["cover"]["kind"] == "billede"
        assert any(c["cover"] and c["cover"]["kind"] == "pdf" for c in front["secondary"] + front["latest"])  # PDF-artiklen får side 1 som billede
        assert not {"most_read", "most_commented", "species_focus", "archive_pdfs"} & front.keys()
        pdf_id = (await articles.search(s, type_="Artikel"))["rows"][0]["id"]
        assert "tags_all" not in await articles.detail(s, pdf_id, can_edit=False, user_id=1)
        full = await articles.detail(s, pdf_id, can_edit=True, user_id=1)
        assert {"Artikel", "Pandion"} <= {t["navn"] for t in full["tags_all"]} and "Artikel" not in {t["navn"] for t in full["tags"]}
        d = await articles.detail(s, pdf_id, can_edit=False, user_id=1)
        assert d["kind"] == "pdf" and d["pdf"]["text_md"] and d["pdf"]["pages"] == 1
        assert await articles.by_netfugl(s, 300) is not None and await articles.by_netfugl(s, 9999) is None
        box = await articles.species_box(s, 1, False)
        assert box["total"] == 1


async def test_drafts_are_hidden_from_readers(db, tmp_path):
    from portal.models import User
    from portal.services import article_edit, articles

    async with db() as s:
        ed = await s.get(User, 3)
        art, first = await article_edit.save(s, ed, {"title": "Kladde om Hjejle", "body_md": "tekst", "status": "kladde", "tags": []})
        assert not first
        assert (await articles.search(s, q="kladde"))["total"] == 0 and (await articles.search(s, q="kladde", can_edit=True))["total"] == 1
        assert await articles.detail(s, art.id, can_edit=False, user_id=1) is None
        art, first = await article_edit.save(s, ed, {"title": "Kladde om Hjejle", "body_md": "tekst", "status": "publiceret", "tags": [], "version": art.version}, art)
        assert first and (await articles.search(s, q="kladde"))["total"] == 1
        with pytest.raises(article_edit.Conflict):
            await article_edit.save(s, ed, {"title": "x", "body_md": "y", "status": "publiceret", "version": 1}, art)
        assert [r["version"] for r in await article_edit.revisions(s, art.id)] == [1]
        with pytest.raises(article_edit.Invalid):
            await article_edit.save(s, ed, {"title": "p", "status": "planlagt", "publish_at": datetime.now(timezone.utc) - timedelta(hours=1)})


async def test_scheduled_publish_and_views(db):
    from portal.models import User
    from portal.services import article_edit, articles

    async with db() as s:
        ed = await s.get(User, 3)
        art, _ = await article_edit.save(s, ed, {"title": "Senere", "body_md": "x", "status": "planlagt", "publish_at": datetime.now(timezone.utc) + timedelta(hours=1)})
        assert (await articles.search(s, q="senere"))["total"] == 0
        await s.execute(text("UPDATE art_articles SET publish_at = now() - interval '1 minute' WHERE id = :i"), {"i": art.id})
        await s.commit()
        assert await article_edit.publish_due(s) == [art.id]
        assert (await articles.search(s, q="senere"))["total"] == 1
        await articles.count_view(art.id, 1)
        await articles.count_view(art.id, 1)  # samme bruger samme døgn tæller kun én gang
        await articles.count_view(art.id, 2)
        assert await articles.flush_views(s) == 1
        assert (await s.execute(text("SELECT views FROM art_articles WHERE id = :i"), {"i": art.id})).scalar() == 2


async def test_comments_reports_bans_and_notifications(db):
    from portal.models import CommentBan, User
    from portal.services import article_comments as ac
    from portal.services import article_edit, article_notify, article_settings

    async with db() as s:
        ed = await s.get(User, 3)
        art, _ = await article_edit.save(s, ed, {"title": "Debat", "body_md": "tekst", "status": "publiceret"})
        u1, u2, mod = await s.get(User, 1), await s.get(User, 2), await s.get(User, 4)
        c1 = await ac.add(s, u1, art.id, "Første **kommentar** <script>x</script>", None, can_edit=False)
        assert "<script>" not in c1.body_html and "<strong>" in c1.body_html
        # første kommentar tilmelder, og et svar går til forfatteren af kommentaren
        assert (await s.execute(text("SELECT count(*) FROM art_subscriptions WHERE user_id = 1 AND kind = 'artikel'"))).scalar() == 1
        c2 = await ac.add(s, u2, art.id, "Svar", c1.id, can_edit=False)
        c3 = await ac.add(s, u1, art.id, "Svar på svar", c2.id, can_edit=False)
        assert c2.parent_id == c1.id and c3.parent_id == c1.id  # kun ét niveau
        await article_notify.on_comment(c2.id)
        await article_notify.on_comment(c3.id)
        notes = (await s.execute(text("SELECT user_id, dedup_key FROM notifications ORDER BY id"))).all()
        assert (1, f"art:svar:{c2.id}") in notes
        # u2 abonnerer via sin kommentar → får besked om c3, men ikke om sin egen
        assert any(u == 2 for u, _ in notes) and len([n for n in notes if n[0] == 2]) == 1
        # tider: dedup pr. time pr. artikel → en tredje kommentar samme time giver ikke endnu en besked til samme bruger
        # udelukkelse
        s.add(CommentBan(user_id=2, reason="Chikane", created_by=4))
        await s.commit()
        with pytest.raises(ac.CommentError) as e:
            await ac.add(s, u2, art.id, "igen", None, can_edit=False)
        assert e.value.status == 403 and "Chikane" in e.value.message
        # u2 er udelukket med skjulte kommentarer: u1 ser ikke u2's svar, men u2 ser det selv
        seen_by_1 = await ac.list_comments(s, art.id, 1, False)
        assert [c["id"] for c in seen_by_1["comments"]] == [c1.id] and [x["id"] for x in seen_by_1["comments"][0]["svar"]] == [c3.id]
        assert {x["id"] for x in (await ac.list_comments(s, art.id, 2, False))["comments"][0]["svar"]} == {c2.id, c3.id}
        await s.execute(text("DELETE FROM comment_bans"))
        await s.commit()
        # anmeldelser: tærskel 3 skjuler automatisk
        cfg = await article_settings.get(s)
        assert cfg["report_threshold"] == 3
        c4 = await ac.add(s, mod, art.id, "Slem kommentar", None, can_edit=False)
        for uid in (1, 2):
            assert not await ac.report(s, await s.get(User, uid), c4.id, "chikane", "")
        with pytest.raises(ac.CommentError):
            await ac.report(s, mod, c4.id, "spam", "")  # egen kommentar
        await ac.report(s, await s.get(User, 1), c4.id, "spam", "")  # dobbelt anmeldelse tæller ikke
        assert (await s.get(type(c4), c4.id)).status == "synlig"
        assert await ac.report(s, ed, c4.id, "forkert", "") is True
        listed = await ac.list_comments(s, art.id, 1, False)
        assert c4.id not in [c["id"] for c in listed["comments"]]
        q = await ac.queue(s, "anmeldte")
        assert q[0]["id"] == c4.id and q[0]["report_count"] == 3 and len(q[0]["reports"]) == 3
        await ac.moderate(s, mod, c4.id, "afvis")
        assert (await s.get(type(c4), c4.id)).status == "synlig" and (await s.get(type(c4), c4.id)).report_count == 0
        await ac.moderate(s, mod, c4.id, "skjul", "regelbrud")
        assert (await ac.list_comments(s, art.id, 1, True))["comments"][-1]["hidden_reason"] == "regelbrud"
        # ret og slet egen
        edited = await ac.edit(s, u1, c1.id, "Rettet")
        assert edited.edited_at is not None
        with pytest.raises(ac.CommentError):
            await ac.edit(s, u2, c1.id, "andres")
        await ac.remove_own(s, u1, c1.id)
        tree = await ac.list_comments(s, art.id, 2, False)
        assert tree["comments"][0]["html"] == "" and tree["comments"][0]["svar"]  # slettet, men svarene bevares
        # rate limit
        for i in range(5):
            await ac.add(s, ed, art.id, f"hurtig {i}", None, can_edit=True)
        with pytest.raises(ac.CommentError) as e:
            await ac.add(s, ed, art.id, "for hurtig", None, can_edit=True)
        assert e.value.status == 429
        # kommentarer lukket
        await s.execute(text("UPDATE art_articles SET comments_open = false WHERE id = :i"), {"i": art.id})
        await s.commit()
        with pytest.raises(ac.CommentError):
            await ac.add(s, u1, art.id, "lukket", None, can_edit=False)


async def test_subscriptions_and_new_article_notifications(db):
    from portal.models import User
    from portal.services import article_edit, article_notify, article_subs

    async with db() as s:
        ed = await s.get(User, 3)
        await article_subs.set_prefs(s, 1, True, None)
        await article_subs.set_prefs(s, 2, True, None)
        await article_subs.set_prefs(s, 2, False, None)
        assert (await article_subs.get_prefs(s, 1))["alle"] and not (await article_subs.get_prefs(s, 2))["alle"]
        art, first = await article_edit.save(s, ed, {"title": "Ny artikel", "body_md": "brødtekst om fugle", "status": "publiceret"})
        assert first
        await article_notify.on_publish(art.id)
        await article_notify.on_publish(art.id)  # dedup
        rows = (await s.execute(text("SELECT user_id, title FROM notifications"))).all()
        assert [r[0] for r in rows] == [1] and "Ny artikel" in rows[0][1]
        # «følg ikke» forhindrer at en kommentar tilmelder igen
        await article_subs.follow(s, 2, art.id, False)
        from portal.services import article_comments as ac

        await ac.add(s, await s.get(User, 2), art.id, "hej", None, can_edit=False)
        assert (await s.execute(text("SELECT muted FROM art_subscriptions WHERE user_id = 2 AND kind = 'artikel'"))).scalar() is True
        await article_subs.set_prefs(s, 1, None, False)
        assert (await article_subs.get_prefs(s, 1))["kommentarer"] is False


async def test_tags_admin_merge_and_species_overrides(db, tmp_path):
    from portal.services import article_edit, articles

    zp = tmp_path / "a.zip"
    sample(zp)
    await do_import(db, tmp_path, zp)
    async with db() as s:
        tags = {t["navn"]: t["id"] for t in await article_edit.tags_admin(s)}
        with pytest.raises(article_edit.Invalid):
            await article_edit.tag_update(s, tags["Skagen"], navn="Nyhed", kind=None, artnr=None, hidden=None)
        assert await article_edit.tag_merge(s, tags["Skagen"], tags["Pandion"]) == 1
        assert "Skagen" not in {t["navn"] for t in await article_edit.tags_admin(s)}
        art155 = (await articles.search(s, q="Aztekerterne"))["rows"][0]["id"]
        await article_edit.set_species(s, art155, [], [2], [])  # fjern falsk positiv
        assert 2 not in {x["artnr"] for x in (await articles.detail(s, art155, can_edit=False, user_id=1))["species"]}
        from portal.services import content_rerender

        await content_rerender.reindex_species(s)  # genberegning må ikke bringe det tilbage
        assert 2 not in {x["artnr"] for x in (await articles.detail(s, art155, can_edit=False, user_id=1))["species"]}
        await article_edit.set_species(s, art155, [], [], [2])
        assert 2 in {x["artnr"] for x in (await articles.detail(s, art155, can_edit=False, user_id=1))["species"]}
        await article_edit.set_species(s, art155, [4], [], [])
        assert any(x["artnr"] == 4 and x["source"] == "redaktoer" for x in (await articles.detail(s, art155, can_edit=False, user_id=1))["species"])


async def test_tags_bulk_save_export_and_import(db, tmp_path):
    from portal.services import article_edit, article_tags

    zp = tmp_path / "a.zip"
    sample(zp)
    await do_import(db, tmp_path, zp)
    async with db() as s:
        ids = {t["navn"]: t["id"] for t in await article_edit.tags_admin(s)}
        rows = {r["navn"]: r for r in await article_tags.export_rows(s)}
        assert rows["Skagen"]["eksempler"] == "Hjejler på marken" and rows["Dværgterne"]["art"] == "Dværgterne"
        out = article_tags.to_csv(list(rows.values()))
        assert out.startswith("﻿id;navn;slags;artnr;art;skjult;artikler;eksempler\n") and "Hjejler på marken" in out

        csv_in = (f"id;navn;slags;artnr;art;skjult\n{ids['Skagen']};Skagen;Sted;;;nej\n{ids['Pandion']};Pandion;publikation;;;ja\n"
                  f"{ids['2001']};2001;aar;;;nej\n999999;X;emne;;;nej\n{ids['Nyhed']};Nyhed;Bogstavsuppe;;;nej\n").encode()
        r = await article_tags.parse_import(s, csv_in)
        assert sorted(r["changes"], key=lambda c: c["id"]) == sorted([{"id": ids["Skagen"], "kind": "sted"}, {"id": ids["Pandion"], "hidden": True}], key=lambda c: c["id"])
        assert len(r["warnings"]) == 2 and r["rows"] == 5
        # Svar kopieret fra en chat: kodeblok, komma og artsnavn i stedet for artnr
        r = await article_tags.parse_import(s, f"```csv\nid,slags,art\n{ids['Skagen']},art,sortgrå ryle\n```".encode())
        assert r["changes"] == [{"id": ids["Skagen"], "kind": "art", "artnr": 2}] and not r["warnings"]
        r = await article_tags.parse_import(s, json.dumps([{"id": ids["Pandion"], "skjult": "ja"}]).encode())
        assert r["changes"] == [{"id": ids["Pandion"], "hidden": True}]

        # Alt eller intet: to emneord, der omdøbes til samme navn, stopper hele omgangen
        with pytest.raises(article_edit.Invalid):
            await article_tags.save_many(s, [{"id": ids["Pandion"], "hidden": True}, {"id": ids["Skagen"], "navn": "Ny"}, {"id": ids["2001"], "navn": "ny"}])
        assert not {t["navn"]: t for t in await article_edit.tags_admin(s)}["Pandion"]["hidden"]
        # To emneord kan bytte navn, og et emneord koblet til en art giver artiklerne arten
        n = await article_tags.save_many(s, [{"id": ids["Skagen"], "navn": "Pandion", "kind": "art", "artnr": 2},
                                             {"id": ids["Pandion"], "navn": "Skagen", "hidden": True}])
        assert n == {"gemt": 2, "flettet": []}
        after = {t["id"]: t for t in await article_edit.tags_admin(s)}
        assert after[ids["Skagen"]]["navn"] == "Pandion" and after[ids["Skagen"]]["kind"] == "art" and after[ids["Pandion"]]["navn"] == "Skagen"
        art300 = (await s.execute(text("SELECT id FROM art_articles WHERE netfugl_id = 300"))).scalar()
        assert (await s.execute(text("SELECT source FROM art_species WHERE article_id = :a AND artnr = 2"), {"a": art300})).scalar() == "emneord"
        # Omdøbning til et eksisterende navn fletter: artiklerne flyttes, og rækkens slags og skjult overskriver det andet emneords
        r = await article_tags.parse_import(s, f"id;navn;slags;skjult\n{ids['2001']};skagen;sted;ja\n".encode())
        assert r["changes"] == [{"id": ids["2001"], "navn": "skagen", "kind": "sted", "hidden": True}] and "flettes" in r["warnings"][0]
        n2001 = after[ids["2001"]]["n"]
        r = await article_tags.save_many(s, r["changes"])
        assert r["flettet"] == [{"id": ids["2001"], "into": ids["Pandion"], "flyttet": n2001}]
        after = {t["id"]: t for t in await article_edit.tags_admin(s)}
        assert ids["2001"] not in after and after[ids["Pandion"]]["navn"] == "Skagen"
        assert after[ids["Pandion"]]["kind"] == "sted" and after[ids["Pandion"]]["hidden"] is True


async def test_news_markdown_migration_and_conflicts(db):
    from portal.models import User
    from portal.services import article_edit, news

    async with db() as s:
        await s.execute(text("INSERT INTO news (title, body, published, author_id) VALUES ('Gammel', 'Linje 1\nLinje 2\n\nNyt afsnit <script>x</script>', true, 3)"))
        await s.commit()
        pub = await news.public_list(s)
        assert len(pub) == 1 and "<br" in news.content_render.render(news._legacy_to_markdown("a\nb")).html
        item = await news.public_get(s, pub[0]["id"])
        assert "<script>" not in item["html"] and "Linje 2" in item["html"] and "<p>" in item["html"]
        ed = await s.get(User, 3)
        n = await news.save(s, ed, {"title": "Ny nyhed", "body": "## Overskrift\n\ntekst", "status": "kladde"})
        assert await news.public_get(s, n.id) is None and len(await news.admin_list(s)) == 2
        n = await news.save(s, ed, {"title": "Ny nyhed", "body": "tekst 2", "status": "publiceret", "version": n.version}, n.id)
        assert (await news.public_get(s, n.id))["title"] == "Ny nyhed" and n.version == 2
        with pytest.raises(article_edit.Conflict):
            await news.save(s, ed, {"title": "x", "body": "y", "status": "publiceret", "version": 1}, n.id)
        await news.save(s, ed, {"title": "Planlagt", "body": "y", "status": "planlagt", "publish_at": datetime.now(timezone.utc) + timedelta(days=1)})
        assert len(await news.public_list(s)) == 2
        await s.execute(text("UPDATE news SET publish_at = now() - interval '1 minute' WHERE status = 'planlagt'"))
        await s.commit()
        await article_edit.publish_due(s)
        assert len(await news.public_list(s)) == 3
        await news.delete(s, n.id)
        assert len(await news.public_list(s)) == 2


async def test_media_dedup_and_validation(db, tmp_path):
    from portal.services import content_media

    async with db() as s:
        m1 = await content_media.store_image(s, jpeg(), filename="a.jpg", owner_kind="artikel", owner_id=None, user_id=3)
        m2 = await content_media.store_image(s, jpeg(), filename="b.jpg", owner_kind="artikel", owner_id=None, user_id=3)
        assert m1.id == m2.id and m1.thumb_key and m1.medium_key is None and m1.width == 900  # små billeder får ingen mellemvariant
        with pytest.raises(content_media.MediaError):
            await content_media.store_image(s, b"<svg onload=alert(1)>", filename="x.svg", owner_kind="artikel", owner_id=None)
        # Skrald mellem JPEG-segmenter: metadatafjerneren giver op, men Pillow kan læse billedet, så det gemmes igen uden EXIF
        odd = await content_media.store_image(s, odd_jpeg(), filename="odd.jpg", owner_kind="artikel", owner_id=None, user_id=3)
        assert odd.kind == "billede" and odd.width == 900
        with pytest.raises(content_media.MediaError):
            await content_media.store_image(s, b"\xff\xd8\xff\x00ikke et billede", filename="d.jpg", owner_kind="artikel", owner_id=None)
        with pytest.raises(content_media.MediaError):
            await content_media.store_file(s, b"MZ", filename="x.exe", owner_kind="artikel", owner_id=None)
        with pytest.raises(content_media.MediaError):
            await content_media.store_file(s, b"ikke pdf", filename="x.pdf", owner_kind="artikel", owner_id=None)
        f = await content_media.store_file(s, pdf(), filename="a.pdf", owner_kind="artikel", owner_id=None, user_id=3)
        assert f.kind == "pdf" and f.pages == 1 and f.thumb_key
        with pytest.raises(content_media.MediaError):
            await content_media.store_file(s, b"<html>", filename="x.mp3", owner_kind="artikel", owner_id=None)
        a = await content_media.store_file(s, b"ID3\x04\x00" + b"\x00" * 64, filename="Sanglærke.mp3", owner_kind="artikel", owner_id=None, user_id=3)
        assert a.kind == "lyd" and a.mime == "audio/mpeg" and a.storage_key.endswith(".mp3") and a.thumb_key is None
        assert (tmp_path / "media" / "art" / a.storage_key).is_file()
        await s.commit()
        assert len(await content_media.owned(s, "artikel", None, 3)) == 4 and await content_media.owned(s, "artikel", None, 1) == []
        await content_media.delete_media(s, m1)
        await s.commit()
        assert len(list((tmp_path / "media" / "art").rglob("*"))) > 0
