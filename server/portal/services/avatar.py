"""Profilbilleder: ét upload giver to kvadratiske WebP-varianter uden EXIF/GPS.

* ``l`` (512 px): profilen og observatørsiden.
* ``s`` (96 px): feedet i Snak og kommentarerne (op til 48 px bredt i 2x).

Filerne er content-addressed (``avatar/<sha[:2]>/<sha>-l.webp``, sha = hash af den store variant) i objektlageret under ``art/``, så
adressen aldrig skifter indhold og kan cachees. ``users.avatar`` holder kun sha'en. Billedet er kun tilgængeligt for indloggede brugere
(/media/ er privat). Billedet beskæres i midten til et kvadrat; alfa lægges på hvid, så små WebP'er ikke får sorte hjørner.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re

from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import content_media, storage

LARGE, SMALL = 512, 96
MAX_BYTES = content_media.MAX_IMAGE
SHA = re.compile(r"^[0-9a-f]{64}$")


class AvatarError(ValueError):
    pass


def _key(sha: str, tag: str) -> str:
    return f"avatar/{sha[:2]}/{sha}-{tag}.webp"


def urls(sha: str | None) -> dict | None:
    """{"l": adresse, "s": adresse} til frontenden – eller None uden billede."""
    if not sha or not SHA.match(sha):
        return None
    return {"l": f"/media/art/{_key(sha, 'l')}", "s": f"/media/art/{_key(sha, 's')}"}


def _square(im: Image.Image, size: int) -> bytes:
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    c = im.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    c.save(buf, "WEBP", quality=82 if size >= LARGE else 80, method=4)
    return buf.getvalue()


def process(data: bytes) -> tuple[str, bytes, bytes]:
    """(sha, stor, lille). Kaster AvatarError for filer, der ikke er billeder, eller som er for store."""
    if len(data) > MAX_BYTES:
        raise AvatarError(f"Billedet er for stort (højst {MAX_BYTES // 1024 // 1024} MB)")
    if content_media.sniff_convertible(data) is None:
        raise AvatarError("Vælg et billede (JPEG, PNG, WebP, GIF eller AVIF)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)  # animeret GIF/WebP: første billede
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
            if min(im.size) < 32:
                raise AvatarError("Billedet er for lille (mindst 32 × 32 px)")
            big = _square(im, LARGE)
            small = _square(im, SMALL)
    except AvatarError:
        raise
    except Exception as exc:  # noqa: BLE001 – Pillow kaster mange slags fejl for ødelagte filer
        raise AvatarError(f"Billedet kan ikke læses ({exc.__class__.__name__})") from exc
    return hashlib.sha256(big).hexdigest(), big, small


async def _unused(session: AsyncSession, sha: str) -> bool:
    return not (await session.execute(select(func.count()).select_from(User).where(User.avatar == sha))).scalar()


async def forget(session: AsyncSession, sha: str | None) -> None:
    """Slet filerne, hvis ingen anden bruger har det samme billede (samme fil = samme sha)."""
    if sha and SHA.match(sha) and await _unused(session, sha):
        for tag in ("l", "s"):
            await storage.delete(_key(sha, tag), prefix=storage.ART_PREFIX)


async def set_avatar(session: AsyncSession, user: User, data: bytes) -> dict:
    sha, big, small = await asyncio.to_thread(process, data)
    await storage.put(_key(sha, "l"), big, "image/webp", prefix=storage.ART_PREFIX)
    await storage.put(_key(sha, "s"), small, "image/webp", prefix=storage.ART_PREFIX)
    old, user.avatar = user.avatar, sha
    await session.flush()
    if old != sha:
        await forget(session, old)
    return urls(sha)


async def clear_avatar(session: AsyncSession, user: User) -> None:
    old, user.avatar = user.avatar, None
    await session.flush()
    await forget(session, old)
