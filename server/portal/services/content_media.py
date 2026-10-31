"""Medier til artikler og nyheder: billeder (med varianter), PDF'er (med miniature af side 1), lydfiler og andre filer.

Filerne er content-addressed: nøglen er ``<sha256[:2]>/<sha256>.<ext>`` i objektlageret under præfikset ``art/``, så den samme fil kun
ligger én gang. Objektlageret er RustFS (S3) når S3_ENDPOINT er sat, ellers MEDIA_DIR/art på disken (services/storage.py). Billeder får en ``-t`` (400 px) og en ``-m`` (1200 px) variant som WebP; originalen gemmes uden EXIF/GPS.
Signaturen tjekkes – filendelsen og brugerens Content-Type stoles ikke på.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import ContentMedia
from portal.services import imagemeta, storage

log = logging.getLogger("portal.media")
Image.MAX_IMAGE_PIXELS = 120_000_000

MAX_IMAGE = 25 * 1024 * 1024
MAX_PDF = 60 * 1024 * 1024
MAX_FILE = 60 * 1024 * 1024
THUMB, MEDIUM = 400, 1200
FILE_TYPES = {".pdf": "application/pdf", ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv", ".rtf": "application/rtf"}
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
               ".wav": "audio/wav", ".flac": "audio/flac"}
# Aldrig inline i browseren (kunne køre script): leveres altid som download
DOWNLOAD_ONLY = {".doc", ".docx", ".xls", ".xlsx", ".rtf", ".csv", ".txt", ".md"}


class MediaError(ValueError):
    pass


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """(mime, endelse) efter filens første bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def sniff_audio(data: bytes, ext: str) -> bool:
    """Passer filens første bytes til lydformatet i endelsen?"""
    if ext == ".mp3":
        return data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    if ext == ".m4a":
        return data[4:8] == b"ftyp"
    if ext in (".ogg", ".oga", ".opus"):
        return data[:4] == b"OggS"
    if ext == ".wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if ext == ".flac":
        return data[:4] == b"fLaC"
    return False


def clean_filename(name: str) -> str:
    base = Path((name or "fil").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f]", "", base).strip()
    return (base or "fil")[:200]


def _variants(data: bytes) -> tuple[int, int, dict[str, bytes]]:
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        out: dict[str, bytes] = {}
        if getattr(im, "is_animated", False):
            return width, height, out  # animeret GIF/WebP: kun originalen
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        for tag, size in (("t", THUMB), ("m", MEDIUM)):
            if max(width, height) <= size and tag == "m":
                continue  # originalen er ikke større end mellemvarianten
            c = im.copy()
            c.thumbnail((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            c.save(buf, "WEBP", quality=80, method=4)
            out[tag] = buf.getvalue()
        return width, height, out


def _reencode(data: bytes, mime: str) -> bytes:
    """Gem et billede, metadatafjerneren ikke kan læse (fx skrald mellem JPEG-segmenter), igen med Pillow – uden EXIF/GPS."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, fmt, **({"quality": 92} if fmt != "PNG" else {}))
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 – Pillow kaster mange slags fejl for ødelagte filer
        raise MediaError(f"Billedet er ødelagt og kan ikke læses ({exc.__class__.__name__})") from exc


async def _existing(session: AsyncSession, owner_kind: str, owner_id: int | None, sha: str) -> ContentMedia | None:
    q = select(ContentMedia).where(ContentMedia.sha256 == sha, ContentMedia.owner_kind == owner_kind)
    q = q.where(ContentMedia.owner_id == owner_id) if owner_id is not None else q.where(ContentMedia.owner_id.is_(None))
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def _put(key: str, data: bytes, mime: str) -> None:
    await storage.put(key, data, mime, prefix=storage.ART_PREFIX)


async def store_image(session: AsyncSession, data: bytes, *, filename: str, owner_kind: str, owner_id: int | None, user_id: int | None = None,
                      alt: str = "", caption: str = "", credit: str = "", original_url: str | None = None, sort: int = 0) -> ContentMedia:
    if len(data) > MAX_IMAGE:
        raise MediaError(f"Billedet er for stort (højst {MAX_IMAGE // 1024 // 1024} MB)")
    kind = sniff_image(data)
    if kind is None:
        raise MediaError("Filen er ikke et billede (JPEG, PNG, GIF eller WebP)")
    mime, ext = kind
    if mime in ("image/jpeg", "image/png", "image/webp"):
        try:
            data = await asyncio.to_thread(imagemeta.strip, data)
        except (ValueError, IndexError):  # struct.error er en ValueError; afkortede segmenter giver IndexError
            data = await asyncio.to_thread(_reencode, data, mime)
    sha = hashlib.sha256(data).hexdigest()
    if (found := await _existing(session, owner_kind, owner_id, sha)) is not None:
        return found
    try:
        width, height, variants = await asyncio.to_thread(_variants, data)
    except Exception as exc:  # noqa: BLE001 – Pillow kaster mange slags fejl for ødelagte filer
        raise MediaError(f"Billedet kan ikke læses ({exc.__class__.__name__})") from exc
    key = f"{sha[:2]}/{sha}.{ext}"
    await _put(key, data, mime)
    keys: dict[str, str | None] = {"t": None, "m": None}
    for tag, blob in variants.items():
        keys[tag] = f"{sha[:2]}/{sha}-{tag}.webp"
        await _put(keys[tag], blob, "image/webp")
    row = ContentMedia(owner_kind=owner_kind, owner_id=owner_id, kind="billede", storage_key=key, thumb_key=keys["t"], medium_key=keys["m"],
                       filename=clean_filename(filename), mime=mime, size=len(data), width=width, height=height, alt=alt[:300],
                       caption=caption[:300], credit=credit[:160], original_url=original_url, sha256=sha, sort=sort, created_by=user_id)
    session.add(row)
    await session.flush()
    return row


async def _run(*cmd: str, timeout: float = 60) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out


async def pdf_info(path: Path) -> int | None:
    """Antal sider (pdfinfo fra poppler-utils), eller None."""
    try:
        code, out = await _run("pdfinfo", str(path))
    except (FileNotFoundError, asyncio.TimeoutError):
        return None
    m = re.search(rb"^Pages:\s+(\d+)", out, re.M)
    return int(m[1]) if code == 0 and m else None


async def pdf_thumb(path: Path) -> bytes | None:
    """Side 1 som WebP (600 px bred) til forsidekort, eller None hvis poppler mangler eller PDF'en er ødelagt."""
    out = path.parent / "thumb"
    try:
        code, _ = await _run("pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to-x", "600", "-scale-to-y", "-1", "-singlefile", str(path), str(out), timeout=90)
    except (FileNotFoundError, asyncio.TimeoutError):
        return None
    jpg = out.with_suffix(".jpg")
    if code != 0 or not jpg.is_file():
        return None

    def to_webp() -> bytes:
        with Image.open(jpg) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "WEBP", quality=78, method=4)
            return buf.getvalue()

    try:
        return await asyncio.to_thread(to_webp)
    except Exception:  # noqa: BLE001
        return None


async def store_file(session: AsyncSession, data: bytes, *, filename: str, owner_kind: str, owner_id: int | None, user_id: int | None = None,
                     title: str = "", text_md: str | None = None, text_source: str | None = None, original_url: str | None = None,
                     sort: int = 0) -> ContentMedia:
    """PDF'er, lydfiler og andre vedhæftninger. En PDF får sideantal og en miniature af side 1."""
    name = clean_filename(filename)
    ext = Path(name).suffix.lower()
    mime = FILE_TYPES.get(ext) or AUDIO_TYPES.get(ext)
    if mime is None:
        raise MediaError(f"Filtypen {ext or '(ingen)'} er ikke tilladt")
    is_pdf, is_audio = ext == ".pdf", ext in AUDIO_TYPES
    if is_pdf and not data.startswith(b"%PDF"):
        raise MediaError("Filen ligner ikke en PDF")
    if is_audio and not sniff_audio(data, ext):
        raise MediaError(f"Filen ligner ikke en {ext[1:].upper()}-lydfil")
    if len(data) > (MAX_PDF if is_pdf else MAX_FILE):
        raise MediaError("Filen er for stor")
    sha = hashlib.sha256(data).hexdigest()
    if (found := await _existing(session, owner_kind, owner_id, sha)) is not None:
        if text_md and not found.text_md:
            found.text_md, found.text_source = text_md, text_source
        return found
    key = f"{sha[:2]}/{sha}{ext}"
    await _put(key, data, mime)
    pages = thumb_key = None
    if is_pdf:
        with tempfile.TemporaryDirectory(prefix="art-pdf-") as tmp:
            path = Path(tmp) / "in.pdf"
            path.write_bytes(data)
            pages = await pdf_info(path)
            blob = await pdf_thumb(path)
        if blob:
            thumb_key = f"{sha[:2]}/{sha}-t.webp"
            await _put(thumb_key, blob, "image/webp")
    row = ContentMedia(owner_kind=owner_kind, owner_id=owner_id, kind="pdf" if is_pdf else "lyd" if is_audio else "fil", storage_key=key,
                       thumb_key=thumb_key, filename=name, mime=mime, size=len(data), caption=title[:300], original_url=original_url, pages=pages,
                       text_md=text_md, text_source=text_source, sha256=sha, sort=sort, created_by=user_id)
    session.add(row)
    await session.flush()
    return row


def _url(key: str | None) -> str | None:
    return "/media/art/" + key if key else None


def public(m: ContentMedia) -> dict:
    """Det frontenden skal bruge til at indsætte et medie i editoren."""
    image = m.kind == "billede"
    return {"id": m.id, "kind": m.kind, "url": _url(m.storage_key), "thumb": _url(m.thumb_key or (m.storage_key if image else None)),
            "medium": _url(m.medium_key or m.storage_key) if image else None, "width": m.width, "height": m.height, "alt": m.alt,
            "caption": m.caption, "credit": m.credit, "filename": m.filename, "size": m.size, "pages": m.pages, "mime": m.mime}


async def delete_media(session: AsyncSession, m: ContentMedia) -> None:
    """Slet rækken og filerne – men kun hvis ingen anden række bruger de samme filer (content-addressed)."""
    keys = {m.storage_key, m.thumb_key, m.medium_key} - {None}
    await session.delete(m)
    await session.flush()
    for key in keys:
        shared = (await session.execute(select(ContentMedia.id).where(
            (ContentMedia.storage_key == key) | (ContentMedia.thumb_key == key) | (ContentMedia.medium_key == key)).limit(1))).first()
        if not shared:
            try:
                await storage.delete(key, prefix=storage.ART_PREFIX)
            except Exception as exc:  # noqa: BLE001 – en manglende fil må ikke stoppe sletningen
                log.warning("Kunne ikke slette %s: %s", key, exc)


async def owned(session: AsyncSession, owner_kind: str, owner_id: int | None, user_id: int) -> list[ContentMedia]:
    q = select(ContentMedia).where(ContentMedia.owner_kind == owner_kind)
    q = q.where(ContentMedia.owner_id == owner_id) if owner_id is not None else q.where(ContentMedia.owner_id.is_(None), ContentMedia.created_by == user_id)
    return list((await session.execute(q.order_by(ContentMedia.sort, ContentMedia.id))).scalars())


async def get(session: AsyncSession, mid: int) -> ContentMedia | None:
    return await session.get(ContentMedia, mid)
