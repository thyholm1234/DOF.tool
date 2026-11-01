"""Fotos: upload, ret, slet, kvote, privilegier, lås, gruppering og kobling til egen observation (fase 3). Kræver ARTICLES_TEST_DSN."""

import io
import os
from datetime import datetime

import pytest
from PIL import Image
from sqlalchemy import select, text

from portal.services import photo_edit, photo_obs, photo_privileges
from portal.timeutil import TZ
from test_photo_import import db, run, sample, write_dir  # noqa: F401

DSN = os.environ.get("ARTICLES_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ARTICLES_TEST_DSN er ikke sat")


def exif_jpeg() -> bytes:
    """JPEG med kamera-EXIF og GPS-position (som en telefon ville gemme den)."""
    im = Image.new("RGB", (800, 500), (30, 90, 160))
    ex = Image.Exif()
    ex[0x0110] = "Canon EOS R5"
    sub = ex.get_ifd(0x8769)
    sub[0xA434], sub[0x829D], sub[0x920A], sub[0x829A], sub[0x8827] = "RF100-500mm", 7.1, 500.0, 1 / 2000, 800
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (56.0, 3.0, 0.0), "E", (10.0, 30.0, 0.0)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex)
    return buf.getvalue()


def fields(**kw) -> dict:
    d = {"dato": "2026-10-27", "land_id": 1, "lokalitet": "Tisvildekysten", "loknr": 100, "lat": None, "lon": None, "titel": "Min fugl", "beskrivelse": "Lidt **tekst**",
         "fugle": [{"artnr": 1, "adfaerd": "siddende", "koen": "han", "alder": "k1", "dragt": None}, {"artnr": 2}], "tags": ["personer"], "kamera": None, "rettighed": 3, "obsid": None}
    d.update(kw)
    return d


@pytest.fixture
async def s(db):  # noqa: F811
    from portal.models import GalCountry

    async with db() as session:
        session.add(GalCountry(id=1, navn="Danmark", netfugl_id=1))
        await session.commit()
        yield session


async def user(s, uid):
    from portal.models import User

    return await s.get(User, uid)


async def test_upload_to_fugle_exif_og_gps(s):
    a = await user(s, 1)
    out = await photo_edit.create(s, a, False, exif_jpeg(), "ib.jpg", fields())
    assert out["status"] == "afventer"  # standard: godkendes af en moderator
    from portal.models import ContentMedia, GalBird, GalPhoto
    from portal.services import storage

    p = await s.get(GalPhoto, out["id"])
    assert p.source == "upload" and p.user_id == 1 and p.photographer_id and p.rettighed == 3 and p.tags == ["personer"] and p.published_at is None
    assert p.kamera["model"] == "Canon EOS R5" and p.kamera["blaende"] == "7.1" and p.kamera["braendvidde"] == "500" and p.kamera["lukkertid"] == "1/2000" and p.kamera["iso"] == "800"
    birds = (await s.execute(select(GalBird).where(GalBird.photo_id == p.id).order_by(GalBird.pos))).scalars().all()
    assert [(b.pos, b.artnr, b.navn, b.koen, b.alder) for b in birds] == [(0, 1, "Dværgterne", "han", "k1"), (1, 2, "Ravn", None, None)]
    assert "<strong>tekst</strong>" in p.beskrivelse_html
    m = await s.get(ContentMedia, p.media_id)
    stored = (await storage.get(m.storage_key, None, None, storage.ART_PREFIX)).data
    exif = Image.open(io.BytesIO(stored)).getexif()
    assert 0x8825 not in exif and not exif.get_ifd(0x8825)  # GPS er fjernet fra den gemte fil
    assert b"Canon" not in stored


async def test_brugerens_kamerafelter_vinder_og_ryd(s):
    a = await user(s, 1)
    out = await photo_edit.create(s, a, False, exif_jpeg(), "a.jpg", fields(kamera={"model": "Egen model", "iso": ""}))
    from portal.models import GalPhoto

    assert (await s.get(GalPhoto, out["id"])).kamera["model"] == "Egen model" and (await s.get(GalPhoto, out["id"])).kamera["iso"] == "800"
    out = await photo_edit.create(s, a, False, exif_jpeg(), "a.jpg", fields(ryd_kamera=True))
    assert (await s.get(GalPhoto, out["id"])).kamera == {}


async def test_validering(s):
    a = await user(s, 1)
    jpg = exif_jpeg()
    for bad, msg in [({"fugle": []}, "fugle"), ({"fugle": [{"artnr": 999}]}, "ukendt art"), ({"fugle": [{"artnr": 1, "koen": "xx"}]}, "ukendt koen"), ({"land_id": 99}, "land"),
                     ({"dato": "2999-01-01"}, "fremtid"), ({"tags": ["nej"]}, "emneord"), ({"rettighed": 7}, "rettighed"), ({"lat": 56.0}, "koordinat"), ({"lokalitet": " "}, "lokalitet"),
                     ({"fugle": [{"artnr": 1}] * 11}, "10 fugle")]:
        with pytest.raises(photo_edit.PhotoError) as e:
            await photo_edit.create(s, a, False, jpg, "a.jpg", fields(**bad))
        assert e.value.status == 400, msg
    with pytest.raises(photo_edit.PhotoError):
        await photo_edit.create(s, a, False, b"ikke et billede", "a.jpg", fields())
    assert (await s.execute(text("SELECT count(*) FROM gal_photos"))).scalar() == 0


async def test_privilegier_og_maanedlig_kvote(s):
    a, admin = await user(s, 1), await user(s, 4)
    jpg = exif_jpeg()
    await photo_privileges.set_level(s, admin, 1, "udelukket", "spam")
    with pytest.raises(photo_edit.PhotoError) as e:
        await photo_edit.check_upload_allowed(s, a, False)
    assert e.value.status == 403
    await photo_edit.check_upload_allowed(s, a, True)  # moderatorer er altid ubegrænsede
    await photo_privileges.set_level(s, admin, 1, "ubegraenset", None)
    assert (await photo_edit.create(s, a, False, jpg, "a.jpg", fields()))["status"] == "publiceret"
    await photo_privileges.set_level(s, admin, 1, "maanedlig", None)
    from portal.services import photo_settings

    await photo_settings.save(s, {"maanedlig_kvote": 2})
    oct_ = datetime(2026, 10, 15, 12, tzinfo=TZ)
    statuses = [(await photo_edit.create(s, a, False, jpg, "a.jpg", fields(), now=oct_))["status"] for _ in range(3)]
    assert statuses == ["publiceret", "publiceret", "afventer"]  # kvote 2 i oktober; det ubegrænsede foto fra før er publiceret i en anden måned
    # månedsskifte: kvoten nulstilles
    nov = datetime(2026, 11, 1, 0, 5, tzinfo=TZ)
    await s.execute(text("UPDATE gal_photos SET published_at = :t WHERE status = 'publiceret'"), {"t": oct_})
    await s.commit()
    assert await photo_edit.month_used(s, 1, nov) == 0
    assert (await photo_edit.create(s, a, False, jpg, "a.jpg", fields(), now=nov))["status"] == "publiceret"
    assert await photo_edit.month_used(s, 1, nov) == 1
    # godkendte fotos (behandlet af en moderator) tæller ikke med
    await s.execute(text("UPDATE gal_photos SET behandlet_af = 4"))
    await s.commit()
    assert await photo_edit.month_used(s, 1, nov) == 0


async def test_ret_slet_og_laas(s):
    a, b, admin = await user(s, 1), await user(s, 2), await user(s, 4)
    out = await photo_edit.create(s, a, False, exif_jpeg(), "a.jpg", fields())
    pid = out["id"]
    with pytest.raises(photo_edit.PhotoError) as e:
        await photo_edit.update(s, b, False, pid, fields())  # andres foto
    assert e.value.status == 404
    await photo_edit.update(s, a, False, pid, fields(titel="Ny titel", fugle=[{"artnr": 2}]))
    from portal.models import GalBird, GalPhoto

    p = await s.get(GalPhoto, pid)
    assert p.titel == "Ny titel" and p.status == "afventer"
    assert [b.artnr for b in (await s.execute(select(GalBird).where(GalBird.photo_id == pid))).scalars()] == [2]
    # afvist foto, der rettes af ejeren, kommer tilbage i køen
    p.status, p.afvist_grund = "afvist", "Dårlig"
    await s.commit()
    assert (await photo_edit.update(s, a, False, pid, fields()))["status"] == "afventer"
    # låst: ejeren kan hverken rette eller slette, moderatoren kan
    p.locked_by = 4
    await s.commit()
    for call in (photo_edit.update(s, a, False, pid, fields()), photo_edit.delete(s, a, False, pid)):
        with pytest.raises(photo_edit.PhotoError) as e:
            await call
        assert e.value.status == 403
    assert (await photo_edit.update(s, admin, True, pid, fields(titel="Af mod")))["id"] == pid
    assert await photo_edit.delete(s, admin, True, pid) is True  # slettet af en anden → skal auditeres
    assert await s.get(GalPhoto, pid) is None
    assert (await s.execute(text("SELECT count(*) FROM content_media WHERE owner_kind = 'foto'"))).scalar() == 0  # filen er også væk


async def test_knyt_observation(s):
    leif = await user(s, 1)
    assert leif.obserkode == "LB"
    jpg = exif_jpeg()
    rows = await photo_obs.mine(s, "LB", 1, datetime(2026, 10, 28).date())
    assert [r["obsid"] for r in rows] == [5001, 5003]  # ±1 dag, kun egne
    assert rows[0]["koen"] == "han" and rows[0]["adfaerd"] == "Siddende" and rows[0]["lokalitet"] == "Tisvildekysten"
    out = await photo_edit.create(s, leif, False, jpg, "a.jpg", fields(obsid=5001, loknr=None, fugle=[{"artnr": 1}], lokalitet="x"))
    from portal.models import GalBird, GalPhoto

    p = await s.get(GalPhoto, out["id"])
    bird = (await s.execute(select(GalBird).where(GalBird.photo_id == p.id))).scalar_one()
    assert p.obsid == 5001 and p.loknr == 100 and p.lat == 56.05 and bird.koen == "han" and bird.adfaerd == "siddende" and bird.dragt == "pragt" and bird.alder == "k1"
    with pytest.raises(photo_edit.PhotoError):  # en andens observation
        await photo_edit.create(s, leif, False, jpg, "a.jpg", fields(obsid=5002))
    # publiceret foto vises på observationen
    p.status = "publiceret"
    await s.commit()
    assert [x["id"] for x in (await photo_obs.for_observations(s, [5001, 5002]))[5001]] == [p.id] and 5002 not in await photo_obs.for_observations(s, [5001, 5002])


async def test_grupper(s):
    a, b, admin = await user(s, 1), await user(s, 2), await user(s, 4)
    jpg = exif_jpeg()
    one = await photo_edit.create(s, a, False, jpg, "a.jpg", fields())
    assert one["gruppe_forslag"] == []
    two = await photo_edit.create(s, a, False, jpg, "b.jpg", fields())
    assert two["gruppe_forslag"] == [one["id"]]  # samme fotograf, art, dato og lokalitet
    other = await photo_edit.create(s, b, False, jpg, "c.jpg", fields())
    assert other["gruppe_forslag"] == []
    await photo_edit.set_group(s, a, False, two["id"], [one["id"]])
    from portal.models import GalGroup, GalPhoto

    ps = [await s.get(GalPhoto, i) for i in (one["id"], two["id"])]
    assert ps[0].group_id == ps[1].group_id and (await s.get(GalGroup, ps[0].group_id)).cover_photo_id == one["id"]
    with pytest.raises(photo_edit.PhotoError) as e:
        await photo_edit.set_group(s, a, False, two["id"], [other["id"]])  # andres foto
    assert e.value.status == 403
    await photo_edit.set_group(s, admin, True, two["id"], [other["id"]])  # moderatorer må alt
    await photo_edit.ungroup(s, a, False, one["id"])
    await photo_edit.delete(s, admin, True, other["id"])
    await s.refresh(ps[1])
    assert ps[1].group_id is None and (await s.execute(select(GalGroup))).first() is None  # gruppen med ét medlem opløses


async def test_mine_fotos_og_indstillinger(s):
    a = await user(s, 1)
    await photo_edit.create(s, a, False, exif_jpeg(), "a.jpg", fields())
    m = await photo_edit.mine(s, a)
    assert len(m["fotos"]) == 1 and m["fotos"][0]["status"] == "afventer" and m["indstillinger"] == {"standard_rettighed": 2, "dof_samtykke": False}
    assert m["privilegie"] == "standard" and m["kvote"] is None
    await photo_edit.save_settings(s, a, 1, True)
    assert (await photo_edit.mine(s, a))["indstillinger"] == {"standard_rettighed": 1, "dof_samtykke": True}
    from portal.services import photos

    # fotos uden egen rettighed følger fotografens standard
    await photo_edit.create(s, a, False, exif_jpeg(), "a.jpg", fields(rettighed=None))
    pid = (await s.execute(text("SELECT max(id) FROM gal_photos"))).scalar()
    await s.execute(text("UPDATE gal_photos SET status = 'publiceret' WHERE id = :i"), {"i": pid})
    await s.commit()
    assert (await photos.get(s, pid, user_id=1, moderator=False))["rettighed"] == {"id": 1, "tekst": (await photos.get(s, pid, user_id=1, moderator=False))["rettighed"]["tekst"], "egen": False}
    with pytest.raises(photo_edit.PhotoError):
        await photo_edit.save_settings(s, a, 9, False)
