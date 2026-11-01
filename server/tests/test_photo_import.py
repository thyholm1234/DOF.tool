"""Fotoimporten (docs/netfugl-afloesning-spec.md §4–§5): rene dele uden database, og import + fotografmatch mod en rigtig Postgres.

DB-delen springes over, medmindre ARTICLES_TEST_DSN er sat (se tests/test_articles_db.py for engangscontainerne)."""

import io
import json
import os
import zipfile

import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from portal.services import photo_codes, photo_import, photo_photographers

DSN = os.environ.get("ARTICLES_TEST_DSN")


def jpeg(color=(200, 30, 30), size=(900, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def meta(pid, **kw) -> dict:
    m = {"schema_version": 1, "id": pid, "url": f"https://netfugl.dk/fotos/{pid}", "hentet": "2026-11-02T10:11:12Z", "fil": "foto.jpg", "fil_sha256": "x",
         "bredde": 900, "hoejde": 600, "titel": f"Foto {pid}", "beskrivelse_html": None, "dato": "2026-10-27", "uploadet": "2026-10-27",
         "publiceret": "2026-10-28", "land": {"navn": "Danmark", "netfugl_id": 1}, "lokalitet": "Tisvildekysten", "koordinat": None,
         "fotograf": {"navn": "Leif Bolding", "netfugl_bruger_id": 3221},
         "fugle": [{"art": {"dansk": "Dværgterne", "latin": "Sternula albifrons", "engelsk": "Little Tern", "netfugl_id": 100}, "adfaerd": "Siddende",
                    "koen": "Han", "alder": "1. kalenderår", "dragt": None, "sjaeldenhed": "Almindelig"}],
         "emneord": [], "kamera": {"model": "NIKON", "linse": None, "blaende": None, "braendvidde": None, "lukkertid": None, "iso": None, "flash": None},
         "rettighed": {"id": 2, "tekst": "Personlig aftale med fotografen er påkrævet for brug af foto"}, "likes": 4, "visninger": 352, "gruppe": [], "kommentarer": []}
    m.update(kw)
    return m


def sample() -> dict[int, tuple[dict, bytes]]:
    two = [meta(2)["fugle"][0], {"art": {"dansk": "Ravn", "latin": "Corvus corax", "engelsk": "Raven", "netfugl_id": 200}, "adfaerd": "Svævende på vinden",
                                 "koen": None, "alder": None, "dragt": "Pragtdragt", "sjaeldenhed": "Almindelig"}]
    com = [{"id": 991, "forfatter": "Navn", "forfatter_id": 123, "dato": "2026-10-28T09:12", "tekst_html": "<p>Flot!</p><script>x()</script>",
            "svar": [{"id": 992, "forfatter": "Anden", "forfatter_id": 456, "dato": "2026-10-28T10:00", "tekst_html": "<p>Enig</p>", "svar": []}]}]
    return {1: (meta(1, gruppe=[2]), jpeg()), 2: (meta(2, fugle=two, titel=None, koordinat={"lat": 56.05, "lon": 12.0}, gruppe=[1],
                                                     emneord=["Tvivl om art"]), jpeg((10, 120, 10))),
            3: (meta(3, kommentarer=com, fotograf={"navn": "Anna Andersen", "netfugl_bruger_id": 77}, beskrivelse_html="<p>Fin <b>dag</b></p><img src=x onerror=y>"),
                jpeg((10, 10, 120)))}


def write_dir(root, photos) -> None:
    for pid, (m, img) in photos.items():
        d = root / "netfugl-fotos" / str(pid)
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps(m))
        (d / "foto.jpg").write_bytes(img)


def write_zip(path, photos, extra: dict[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("netfugl-fotos/manifest.json", "{}")
        for pid, (m, img) in photos.items():
            z.writestr(f"netfugl-fotos/{pid}/meta.json", json.dumps(m))
            z.writestr(f"netfugl-fotos/{pid}/foto.jpg", img)
        for n, d in (extra or {}).items():
            z.writestr(n, d)


# ---------------------------------------------------------------- uden database

def test_navnenormalisering():
    n = photo_photographers.norm
    assert n("Knud-ERIK Andersen") == n("Knud-Erik  Andersen") == "knud erik andersen"
    assert n("Leif Bolding (fsh)") == "leif bolding"
    assert n("J. Hansen") == n("J Hansen")


def test_zip_sikkerhed(tmp_path):
    write_zip(tmp_path / "a.zip", sample(), extra={"../../ond/1/meta.json": b"{}", "/abs/5/meta.json": b"{}", "netfugl-fotos/9/foto.exe": b"MZ",
                                                  "netfugl-fotos/8/../../7/meta.json": b"{}"})
    src = photo_import.find_source("a.zip", tmp_path)
    assert src.ids() == [1, 2, 3]
    assert len(src.rejected) >= 3 and not any(i in src.ids() for i in (5, 7, 8, 9))


def test_mappe_uden_for_importmappen_afvises(tmp_path):
    (tmp_path / "ind").mkdir()
    with pytest.raises(photo_import.ImportAbort):
        photo_import.find_source("../", tmp_path / "ind")


def test_kilde_vaelges_efter_indhold(tmp_path):
    write_dir(tmp_path / "m", sample())
    assert photo_import.find_source("m", tmp_path).ids() == [1, 2, 3]
    assert [f["sti"] for f in photo_import.list_folders(tmp_path)] == ["m"]
    (tmp_path / "tom").mkdir()
    with pytest.raises(photo_import.ImportAbort):
        photo_import.find_source("tom", tmp_path)


def test_kodeslug():
    assert photo_codes.slug("Sommerdragt/yngledragt") == "sommerdragt_yngl"
    assert photo_codes.slug("Svævende på vinden") == "svaevende_paa_vi"


# ---------------------------------------------------------------- med database

@pytest.fixture
async def db(tmp_path, monkeypatch):
    import redis.asyncio as aioredis

    from portal import cache
    from portal.config import get_settings
    from portal.db import Base
    from portal.dbgroups import USER_TABLES
    from portal.dbops import ddl_groups
    from portal.models import EXTRA_DDL, Observer, Species, User

    engine = create_async_engine(DSN)
    tables = [t for t in Base.metadata.sorted_tables if t.name in USER_TABLES or t.name in ("species", "locations", "observers", "observations", "code_texts")]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(lambda c: Base.metadata.drop_all(c, tables=tables))
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        for ddl in EXTRA_DDL:
            if "gal_photos" in ddl and "users" in ddl_groups(ddl):
                await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(photo_import, "SessionLocal", factory)
    from portal.services import activity

    monkeypatch.setattr(activity, "SessionLocal", factory)
    s = get_settings()
    monkeypatch.setattr(s, "media_dir", str(tmp_path / "media"))
    monkeypatch.setattr(s, "photo_import_dir", str(tmp_path / "imports"))
    monkeypatch.setattr(s, "s3_endpoint", "")
    kv = aioredis.from_url(os.environ.get("ARTICLES_TEST_REDIS", "redis://127.0.0.1:56379/0"), decode_responses=True)
    await kv.flushdb()
    monkeypatch.setattr(cache, "_client", kv)
    async with factory() as session:
        session.add(User(id=1, obserkode="LB", navn="Leif Bolding", role="user"))
        session.add(User(id=2, obserkode="A1", navn="Anna Andersen", role="user"))
        session.add(User(id=3, obserkode="A2", navn="Anna  andersen", role="user"))
        session.add(User(id=4, obserkode="AD", navn="Admin Adminsen", role="admin"))
        session.add(User(id=5, obserkode="SU", navn="Super Bruger", role="superadmin"))
        for nr, navn, latin in [(1, "Dværgterne", "Sternula albifrons"), (2, "Ravn", "Corvus corax")]:
            session.add(Species(artnr=nr, navn=navn, latin=latin, sortering=nr, art_type="art"))
        await session.execute(text("INSERT INTO locations (loknr, navn, lat, lon) VALUES (100, 'Tisvildekysten', 56.05, 12.01), (101, 'Tisvildekysten', 57.5, 10.0)"))
        await session.execute(text("INSERT INTO code_texts (kind, code, text) VALUES ('adf', 'SI', 'Siddende'), ('dragt', 'PR', 'Pragtdragt')"))
        await session.execute(text("INSERT INTO observations (obsid, dato, artnr, antal, koen, adfkode, alderkode, dragtkode, loknr, obserkode, hemmelig, kategori, afdelinger) VALUES "
                                   "(5001, '2026-10-27', 1, 2, 'M', 'SI', 'k1', 'PR', 100, 'LB', 0, 'alm', '{}'), (5002, '2026-10-27', 1, 1, 'F', NULL, NULL, NULL, 100, 'A1', 0, 'alm', '{}'), (5003, '2026-10-29', 1, 1, NULL, NULL, NULL, NULL, 100, 'LB', 0, 'alm', '{}')"))
        await session.execute(text("INSERT INTO observers (obserkode, fornavn, efternavn) VALUES ('KE1', 'Knud-Erik', 'Hansen'), ('XX', 'Per', 'Pedersen')"))
        await session.commit()
    yield factory
    await kv.aclose()
    await engine.dispose()


async def run(factory, rel, **opts):
    from portal.models import GalImport

    async with factory() as s:
        imp = GalImport(kilde="mappe", sti=rel, filename=rel, status="klar", options=opts)
        s.add(imp)
        await s.commit()
        iid = imp.id
    await photo_import.run_import(iid)
    async with factory() as s:
        return await s.get(GalImport, iid)


@pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
async def test_import_fra_mappe_og_idempotens(db, tmp_path):
    from portal.models import GalBird, GalComment, GalGroup, GalPhoto, GalPhotographer

    write_dir(tmp_path / "imports" / "a", sample())
    imp = await run(db, "a")
    assert imp.status == "faerdig", imp.log
    assert imp.stats["oprettet"] == 3 and imp.stats["fejl"] == 0 and imp.stats["fotografer"] == 2
    async with db() as s:
        photos = {p.netfugl_id: p for p in (await s.execute(select(GalPhoto))).scalars()}
        assert set(photos) == {1, 2, 3} and all(p.status == "publiceret" and p.media_id for p in photos.values())
        p1, p2, p3 = photos[1], photos[2], photos[3]
        assert p1.loknr is None and p2.loknr == 100  # uden koordinat er to lokaliteter med samme navn tvetydige; koordinaten vælger den nære
        assert p1.kamera == {"model": "NIKON"}
        assert p2.tags == ["tvivl_art"] and p2.lat == 56.05
        assert p3.beskrivelse_md == "Fin dag" and "onerror" not in p3.beskrivelse_html and "<img" not in p3.beskrivelse_html
        birds = (await s.execute(select(GalBird).where(GalBird.photo_id == p2.id).order_by(GalBird.pos))).scalars().all()
        assert [(b.pos, b.artnr, b.dragt) for b in birds] == [(0, 1, None), (1, 2, "pragt")]
        assert birds[1].adfaerd == "svaevende_paa_vi" and birds[0].adfaerd == "siddende" and birds[0].alder == "k1"
        assert (await s.execute(text("SELECT text, sort FROM gal_codes WHERE code = 'svaevende_paa_vi'"))).one() == ("Svævende på vinden", 999)
        com = (await s.execute(select(GalComment).where(GalComment.photo_id == p3.id).order_by(GalComment.id))).scalars().all()
        assert len(com) == 2 and com[1].parent_id == com[0].id and "script" not in com[0].body_html and p3.comments == 2
        g = (await s.execute(select(GalGroup))).scalars().all()
        assert len(g) == 1 and p1.group_id == p2.group_id == g[0].id and g[0].cover_photo_id == p1.id
        ph = {p.netfugl_bruger_id: p for p in (await s.execute(select(GalPhotographer))).scalars()}
        assert ph[3221].user_id == 1 and ph[3221].match == "auto" and p1.user_id == 1
        assert ph[77].user_id is None and ph[77].match == "tvetydig"  # to brugere hedder Anna Andersen
    again = await run(db, "a")
    assert again.stats["uaendret"] == 3 and again.stats["oprettet"] == 0


@pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
async def test_import_fra_zip_dry_run_limit_og_lokalt_rettet(db, tmp_path):
    from portal.models import GalPhoto

    (tmp_path / "imports").mkdir()
    write_zip(tmp_path / "imports" / "Fotos-001.zip", sample())
    dry = await run(db, "Fotos-001.zip", dry_run=True)
    assert dry.status == "faerdig" and dry.stats["oprettet"] == 3
    async with db() as s:
        assert (await s.execute(select(GalPhoto))).first() is None
    lim = await run(db, "Fotos-001.zip", limit=1)
    assert lim.stats["oprettet"] == 1
    async with db() as s:
        p = (await s.execute(select(GalPhoto))).scalar_one()
        p.locally_edited, p.titel = True, "Rettet her"
        await s.commit()
    forced = await run(db, "Fotos-001.zip", force=True)
    assert forced.stats["sprunget_lokalt"] == 1 and forced.stats["oprettet"] == 2
    over = await run(db, "Fotos-001.zip", force=True, overwrite_local=True)
    assert over.stats["sprunget_lokalt"] == 0 and over.stats["opdateret"] == 3


@pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
async def test_ugyldigt_foto_stopper_ikke_resten(db, tmp_path):
    photos = sample()
    photos[2] = ({**photos[2][0], "schema_version": 9}, photos[2][1])
    photos[3] = (photos[3][0], b"ikke et billede")
    write_dir(tmp_path / "imports" / "b", photos)
    imp = await run(db, "b")
    assert imp.status == "faerdig" and imp.stats["oprettet"] == 1 and imp.stats["fejl"] == 2
    assert {e["netfugl_id"] for e in imp.log if e["niveau"] == "fejl"} == {2, 3}


@pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")
async def test_fotografmatch(db):
    from portal.models import GalPhotographer

    async with db() as s:
        a, b, c, d, e = [await photo_photographers.find_or_create(s, n, netfugl_bruger_id=i) for i, n in
                         enumerate(["Leif Bolding", "knud-ERIK hansen", "Anna Andersen", "Ukendt Person", "Per Pedersen (fsh)"], 1)]
        m = await photo_photographers.find_or_create(s, "Manuel Mand", netfugl_bruger_id=99)
        m.match = "manuel"
        await s.commit()
        out = await photo_photographers.match_all(s)
        await s.commit()
        assert out == {"auto": 3, "tvetydig": 1, "ingen": 1}
        rows = {p.navn: p for p in (await s.execute(select(GalPhotographer))).scalars()}
        assert (rows["Leif Bolding"].user_id, rows["Leif Bolding"].match) == (1, "auto")
        assert (rows["knud-ERIK hansen"].obserkode, rows["knud-ERIK hansen"].user_id) == ("KE1", None)
        assert rows["Anna Andersen"].match == "tvetydig" and rows["Ukendt Person"].match == "ingen"
        assert rows["Per Pedersen (fsh)"].obserkode == "XX"
        assert rows["Manuel Mand"].match == "manuel" and rows["Manuel Mand"].user_id is None
        # en bruger med obserkoden logger senere ind → fotografen kobles
        from portal.models import User

        s.add(User(id=10, obserkode="KE1", navn="Helt anden", role="user"))
        await s.commit()
        user = await s.get(User, 10)
        await photo_photographers.claim_by_obserkode(s, user)
        await s.commit()
        assert (await s.get(GalPhotographer, b.id)).user_id == 10
