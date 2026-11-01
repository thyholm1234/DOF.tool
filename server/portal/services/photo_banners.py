"""Bannerbilleder til fotogalleriet (docs/netfugl-afloesning-spec.md §10): et beskåret 3:1-udsnit af et foto (1800×600 WebP) med tekst og datointerval."""

from __future__ import annotations

import asyncio
import io
import random
from datetime import date

from PIL import Image, ImageOps
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import ContentMedia, GalBanner
from portal.services import content_media, storage
from portal.timeutil import dk_today

SIZE = (1800, 600)
RATIO_TOLERANCE = 0.06


class BannerError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _render(data: bytes, crop: dict) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        x0, y0, x1, y1 = crop["x"] * w, crop["y"] * h, (crop["x"] + crop["w"]) * w, (crop["y"] + crop["h"]) * h
        box = tuple(int(round(v)) for v in (x0, y0, min(x1, w), min(y1, h)))
        out = im.crop(box).resize(SIZE, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "WEBP", quality=82, method=4)
        return buf.getvalue()


def check_crop(crop: dict, w: int, h: int) -> dict:
    try:
        c = {k: float(crop[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as exc:
        raise BannerError(400, "Beskæringen mangler x, y, w og h") from exc
    if not (0 <= c["x"] <= 1 and 0 <= c["y"] <= 1 and 0 < c["w"] <= 1 and 0 < c["h"] <= 1 and c["x"] + c["w"] <= 1.001 and c["y"] + c["h"] <= 1.001):
        raise BannerError(400, "Beskæringen ligger uden for billedet")
    ratio = (c["w"] * w) / (c["h"] * h)
    if abs(ratio - 3) / 3 > RATIO_TOLERANCE:
        raise BannerError(400, "Beskæringen skal have forholdet 3:1")
    return c


async def create(session: AsyncSession, user_id: int, photo_id: int, crop: dict, tekst: str | None, fra: date | None, til: date | None) -> GalBanner:
    row = (await session.execute(text("""
        SELECT p.id, p.status, m.storage_key, p.width, p.height, ph.navn FROM gal_photos p JOIN content_media m ON m.id = p.media_id
        LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id WHERE p.id = :i"""), {"i": photo_id})).mappings().first()
    if row is None:
        raise BannerError(404, "Fotoet findes ikke")
    if row["status"] != "publiceret":
        raise BannerError(400, "Kun publicerede fotos kan bruges som banner")
    if fra and til and til < fra:
        raise BannerError(400, "Slutdatoen ligger før startdatoen")
    obj = await storage.get(row["storage_key"], None, None, storage.ART_PREFIX)
    if obj is None:
        raise BannerError(404, "Billedfilen findes ikke")
    with Image.open(io.BytesIO(obj.data)) as im:
        w, h = ImageOps.exif_transpose(im).size
    c = check_crop(crop, w, h)
    data = await asyncio.to_thread(_render, obj.data, c)
    sort = (await session.execute(text("SELECT coalesce(max(sort), 0) + 1 FROM gal_banners"))).scalar()
    b = GalBanner(photo_id=photo_id, crop=c, tekst=(tekst or "").strip()[:200] or None, fra=fra, til=til, sort=sort, active=True)
    session.add(b)
    await session.flush()
    m = await content_media.store_image(session, data, filename=f"banner-{b.id}.webp", owner_kind="banner", owner_id=b.id, user_id=user_id, credit=(row["navn"] or "")[:160])
    b.media_id = m.id
    await session.commit()
    await bump("photos")
    return b


async def listing(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT b.id, b.photo_id, b.tekst, b.fra, b.til, b.sort, b.active, b.crop, bm.storage_key AS banner_key, ph.navn AS fotograf, p.status
        FROM gal_banners b LEFT JOIN content_media bm ON bm.id = b.media_id LEFT JOIN gal_photos p ON p.id = b.photo_id LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
        ORDER BY b.sort, b.id"""))).mappings().all()
    today = dk_today()
    return [{"id": r["id"], "photo_id": r["photo_id"], "tekst": r["tekst"], "fra": r["fra"].isoformat() if r["fra"] else None, "til": r["til"].isoformat() if r["til"] else None,
             "sort": r["sort"], "active": r["active"], "url": "/media/art/" + r["banner_key"] if r["banner_key"] else None, "fotograf": r["fotograf"], "foto_status": r["status"],
             "vises_nu": bool(r["active"] and r["status"] == "publiceret" and (not r["fra"] or r["fra"] <= today) and (not r["til"] or r["til"] >= today))} for r in rows]


async def update(session: AsyncSession, bid: int, data: dict) -> None:
    b = await session.get(GalBanner, bid)
    if b is None:
        raise BannerError(404, "Banneret findes ikke")
    if "tekst" in data:
        b.tekst = (data["tekst"] or "").strip()[:200] or None
    for k in ("fra", "til"):
        if k in data:
            setattr(b, k, data[k])
    if b.fra and b.til and b.til < b.fra:
        raise BannerError(400, "Slutdatoen ligger før startdatoen")
    if data.get("sort") is not None:
        b.sort = int(data["sort"])
    if data.get("active") is not None:
        b.active = bool(data["active"])
    await session.commit()


async def delete(session: AsyncSession, bid: int) -> None:
    b = await session.get(GalBanner, bid)
    if b is None:
        return
    media = await session.get(ContentMedia, b.media_id) if b.media_id else None
    await session.delete(b)
    await session.flush()
    if media is not None:
        await content_media.delete_media(session, media)
    await session.commit()


async def current(session: AsyncSession) -> dict | None:
    """Ét banner pr. visning, valgt blandt de aktive i datointervallet (roterer pr. sidevisning)."""
    rows = [b for b in await listing(session) if b["vises_nu"] and b["url"]]
    if not rows:
        return None
    b = random.choice(rows)
    return {"id": b["id"], "url": b["url"], "tekst": b["tekst"], "photo_id": b["photo_id"], "fotograf": b["fotograf"]}
