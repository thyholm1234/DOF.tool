"""Profilbilleder: beskæring, to størrelser, metadata væk, afvisning af ikke-billeder (services/avatar.py)."""

import io

import pytest
from PIL import Image

from portal.services import avatar


def _img(size=(1200, 800), fmt="JPEG", mode="RGB", color=(200, 30, 30), **kw) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **kw)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_two_square_webp_sizes():
    sha, big, small = avatar.process(_img())
    for blob, side in ((big, avatar.LARGE), (small, avatar.SMALL)):
        im = _open(blob)
        assert im.format == "WEBP" and im.size == (side, side)
    assert len(sha) == 64 and len(small) < len(big)


def test_same_picture_same_sha():
    assert avatar.process(_img())[0] == avatar.process(_img())[0]


def test_exif_is_stripped_and_orientation_baked_in():
    im = Image.new("RGB", (400, 200), (255, 255, 255))
    im.paste((0, 0, 255), (0, 0, 200, 200))  # venstre halvdel blå
    exif = Image.Exif()
    exif[0x0112] = 6  # 90° med uret ved visning
    exif[0x8825] = {1: "N"}
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    _, big, _ = avatar.process(buf.getvalue())
    out = _open(big)
    assert not out.getexif() and "exif" not in out.info
    # efter 90° med uret ligger den blå halvdel øverst
    assert out.convert("RGB").getpixel((256, 20))[2] > 200 and out.convert("RGB").getpixel((256, 20))[0] < 80


def test_transparency_goes_on_white():
    _, big, _ = avatar.process(_img((300, 300), "PNG", "RGBA", (0, 0, 0, 0)))
    assert _open(big).convert("RGB").getpixel((5, 5)) == (255, 255, 255)


def test_rejects_non_images_and_tiny_and_huge():
    with pytest.raises(avatar.AvatarError):
        avatar.process(b"%PDF-1.4 not an image")
    with pytest.raises(avatar.AvatarError):
        avatar.process(b"\xff\xd8\xff broken jpeg")
    with pytest.raises(avatar.AvatarError):
        avatar.process(_img((20, 20)))
    with pytest.raises(avatar.AvatarError):
        avatar.process(b"\xff\xd8\xff" + b"0" * (avatar.MAX_BYTES + 1))


def test_urls():
    sha = "ab" + "0" * 62
    u = avatar.urls(sha)
    assert u == {"l": f"/media/art/avatar/ab/{sha}-l.webp", "s": f"/media/art/avatar/ab/{sha}-s.webp"}
    assert avatar.urls(None) is None and avatar.urls("../../etc/passwd") is None
