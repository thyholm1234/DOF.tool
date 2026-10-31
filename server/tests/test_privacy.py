"""GDPR og sikkerhed: metadata fjernes fra billeder, udtrækket dækker brugerens tabeller, klientens IP kan ikke forfalskes."""

import struct
from types import SimpleNamespace

from portal.api.deps import client_ip
from portal.services import account, imagemeta


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _exif(orientation: int) -> bytes:
    # IFD0 med orientering og en GPS-peger; GPS-IFD'en med breddegrad
    entries = [(0x0112, 3, 1, struct.pack("<HH", orientation, 0)), (0x8825, 4, 1, struct.pack("<I", 38))]
    ifd0 = struct.pack("<H", len(entries)) + b"".join(struct.pack("<HHI", t, ty, n) + v for t, ty, n, v in entries) + struct.pack("<I", 0)
    gps = struct.pack("<H", 1) + struct.pack("<HHI", 0x0002, 5, 3) + struct.pack("<I", 0) + struct.pack("<I", 0)
    return b"Exif\x00\x00" + b"II*\x00" + struct.pack("<I", 8) + ifd0 + gps + b"GPS-55.6761N"


def test_jpeg_keeps_orientation_and_drops_gps_xmp_comments():
    scan = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\xff\x00\x56" + b"\xff\xd9"
    jpeg = (b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + _seg(0xE1, _exif(6))
            + _seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>GPS</x:xmpmeta>") + _seg(0xFE, b"Kamera SN 1234")
            + _seg(0xE2, b"ICC_PROFILE\x00\x01\x01profil") + _seg(0xDB, b"\x00" + bytes(64)) + scan)
    out = imagemeta.strip(jpeg)
    assert b"GPS" not in out and b"xmpmeta" not in out and b"Kamera" not in out
    assert b"ICC_PROFILE" in out and out.endswith(scan)
    assert out[2:4] == b"\xff\xe0"  # JFIF-hovedet først, så den minimale EXIF-blok
    exif_at = out.index(b"Exif\x00\x00")
    assert imagemeta._orientation(out[exif_at:]) == 6


def test_jpeg_without_rotation_gets_no_exif():
    jpeg = b"\xff\xd8" + _seg(0xE1, _exif(1)) + b"\xff\xda\x00\x02" + b"\x00\xff\xd9"
    assert b"Exif" not in imagemeta.strip(jpeg)


def test_png_and_webp_metadata_removed():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + b"\x00\x00\x00\x00"
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", bytes(13)) + chunk(b"eXIf", b"GPS") + chunk(b"tEXt", b"Author\x00Mig") + chunk(b"IDAT", b"x") + chunk(b"IEND", b"")
    out = imagemeta.strip(png)
    assert b"eXIf" not in out and b"Author" not in out and b"IDAT" in out and out.endswith(chunk(b"IEND", b""))

    def wchunk(kind, data):
        return kind + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")
    body = b"WEBP" + wchunk(b"VP8X", bytes([0x0C]) + bytes(9)) + wchunk(b"VP8 ", b"billede") + wchunk(b"EXIF", b"GPS") + wchunk(b"XMP ", b"<x/>")
    webp = b"RIFF" + struct.pack("<I", len(body)) + body
    out = imagemeta.strip(webp)
    assert b"EXIF" not in out and b"XMP " not in out and b"billede" in out
    assert out[20] & 0x0C == 0 and struct.unpack("<I", out[4:8])[0] == len(out) - 8


def test_unknown_format_is_rejected():
    try:
        imagemeta.strip(b"<html>")
    except ValueError:
        return
    raise AssertionError("ukendt format blev accepteret")


def test_export_covers_user_tables():
    refs = dict(account._user_refs())
    for table in ("areas", "alert_rules", "field_reports", "thread_comments", "nf_photos", "push_subscriptions", "user_locations"):
        assert table in refs
    assert "users" not in refs and "observations" not in refs


def test_client_ip_ignores_spoofed_header():
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4"}, client=SimpleNamespace(host="10.0.1.7"))
    assert client_ip(req) == "10.0.1.7"


def test_storage_names_reject_traversal():
    from portal.services import storage
    assert storage.name_of("/media/obs/abc.jpg") == "abc.jpg"
    for bad in ("/media/obs/../x", "/media/obs/a/b.jpg", "/media/netfugl/x.jpg", "/media/obs/", None):
        assert storage.name_of(bad) is None


def test_local_storage_roundtrip(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from portal.services import storage
    monkeypatch.setattr(storage, "get_settings", lambda: SimpleNamespace(s3_endpoint="", media_dir=str(tmp_path)))

    async def run():
        url = await storage.put("a.mp3", b"0123456789", "audio/mpeg")
        assert url == "/media/obs/a.mp3" and (tmp_path / "obs" / "a.mp3").read_bytes() == b"0123456789"
        assert (await storage.get("a.mp3", 2, 4)).data == b"234" and await storage.stat("a.mp3") == 10
        assert await storage.usage() == (1, 10)
        assert await storage.put("ab/" + "a" * 64 + ".pdf", b"%PDF", "application/pdf", prefix=storage.ART_PREFIX) == "/media/art/ab/" + "a" * 64 + ".pdf"
        assert await storage.usage() == (2, 14)  # artikelmedierne tæller med
        await storage.delete_urls([url, "/media/netfugl/x.jpg"])
        assert await storage.get("a.mp3") is None
    asyncio.run(run())


def test_migrate_local_moves_observation_and_article_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from portal.services import storage
    (tmp_path / "obs").mkdir()
    (tmp_path / "obs" / "a.jpg").write_bytes(b"jpg")
    (tmp_path / "obs" / "b.jpg.tmp").write_bytes(b"halv")
    (tmp_path / "art" / "ab").mkdir(parents=True)
    (tmp_path / "art" / "ab" / "x.mp3").write_bytes(b"ID3")
    put = {}
    monkeypatch.setattr(storage, "get_settings", lambda: SimpleNamespace(s3_endpoint="rustfs:9000", media_dir=str(tmp_path)))
    monkeypatch.setattr(storage, "_ensure_bucket", lambda: None)
    monkeypatch.setattr(storage, "_put", lambda key, data, ct: put.__setitem__(key, (data, ct)))
    assert storage._migrate_local() == 2
    assert put == {"obs/a.jpg": (b"jpg", "image/jpeg"), "art/ab/x.mp3": (b"ID3", "audio/mpeg")}
    assert not (tmp_path / "art" / "ab" / "x.mp3").exists() and (tmp_path / "obs" / "b.jpg.tmp").exists()
