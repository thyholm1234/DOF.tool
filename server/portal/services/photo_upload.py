"""Chunked upload af fotoarkivets zip-filer (som article_upload). Delene lægges i ``photo_import_dir``; ved ``finish`` bliver filen til en
``<id>.zip``, som importen finder via ``gal_imports.sti``. Zip-filen kan være op til 20 GB – større arkiver deles med ``tools/netfugl_fotos zip``."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import worker_command
from portal.config import get_settings
from portal.models import GalImport
from portal.services.article_upload import CHUNK, MAX_SIZE, UploadError, parts_for

__all__ = ["CHUNK", "UploadError", "parts_for", "create", "write_part", "received", "finish", "discard", "purge", "root"]
UPLOADS = "uploads"


def root() -> Path:
    p = Path(get_settings().photo_import_dir) / UPLOADS
    p.mkdir(parents=True, exist_ok=True)
    return p


async def create(session: AsyncSession, user_id: int, filename: str, size: int, sha256: str | None) -> GalImport:
    if size <= 0 or size > MAX_SIZE:
        raise UploadError(400, "Filen har en ugyldig størrelse (højst 20 GB)")
    if not filename.lower().endswith(".zip"):
        raise UploadError(400, "Vælg en .zip-fil")
    imp = GalImport(kilde="zip", filename=filename[:300], size=size, uploaded_by=user_id, status="uploader", options={"sha256": (sha256 or "").lower() or None})
    session.add(imp)
    await session.commit()
    with (root() / f"{imp.id}.part").open("wb") as fh:
        fh.truncate(size)
    return imp


def _write(path: Path, n: int, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        os.pwrite(fd, data, n * CHUNK)
    finally:
        os.close(fd)
    (path.parent / f"{path.name}.{n}").touch()


async def write_part(imp: GalImport, n: int, stream) -> int:
    if imp.status != "uploader":
        raise UploadError(409, "Importen er ikke længere i upload-fasen")
    if not 0 <= n < parts_for(imp.size):
        raise UploadError(400, "Ugyldigt delnummer")
    expected = min(CHUNK, imp.size - n * CHUNK)
    buf = bytearray()
    async for chunk in stream:
        buf += chunk
        if len(buf) > expected:
            raise UploadError(413, "Delen er større end forventet")
    if len(buf) != expected:
        raise UploadError(400, f"Delen skulle være {expected} bytes, men var {len(buf)}")
    path = root() / f"{imp.id}.part"
    if not path.is_file():
        raise UploadError(404, "Uploaden findes ikke længere")
    await asyncio.to_thread(_write, path, n, bytes(buf))
    return len(buf)


def received(imp: GalImport) -> list[int]:
    return [n for n in range(parts_for(imp.size)) if (root() / f"{imp.id}.part.{n}").exists()]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 22):
            h.update(block)
    return h.hexdigest()


def _cleanup(import_id: int) -> None:
    for p in root().glob(f"{import_id}.part*"):
        p.unlink(missing_ok=True)


KEEP = {"import_comments", "overwrite_local", "dry_run", "force", "keep_files"}


async def finish(session: AsyncSession, imp: GalImport, options: dict) -> GalImport:
    if imp.status != "uploader":
        raise UploadError(409, "Importen er allerede startet")
    missing = sorted(set(range(parts_for(imp.size))) - set(received(imp)))
    if missing:
        raise UploadError(400, f"Der mangler {len(missing)} dele (første: {missing[0]})")
    src = root() / f"{imp.id}.part"
    if src.stat().st_size != imp.size:
        raise UploadError(400, "Filens størrelse passer ikke")
    want = (imp.options or {}).get("sha256")
    if want and await asyncio.to_thread(_sha, src) != want:
        raise UploadError(400, "Kontrolsummen (SHA-256) passer ikke – send filen igen")
    if not await asyncio.to_thread(zipfile.is_zipfile, src):
        raise UploadError(400, "Filen er ikke en gyldig zip")
    dst = root() / f"{imp.id}.zip"
    await asyncio.to_thread(os.replace, src, dst)
    await asyncio.to_thread(_cleanup, imp.id)
    imp.sti = f"{UPLOADS}/{imp.id}.zip"
    await start(session, imp, options)
    return imp


async def start(session: AsyncSession, imp: GalImport, options: dict) -> None:
    imp.options = {k: bool(v) for k, v in options.items() if k in KEEP}
    imp.status = "klar"
    await session.commit()
    await worker_command("photos_import", import_id=imp.id)


async def discard(session: AsyncSession, imp: GalImport) -> None:
    if imp.status == "koerer":
        raise UploadError(409, "Importen kører – vent til den er færdig")
    await asyncio.to_thread(_cleanup, imp.id)
    if imp.kilde == "zip":
        (root() / f"{imp.id}.zip").unlink(missing_ok=True)
    await session.delete(imp)
    await session.commit()


def _purge(busy: set[int], resumable: set[int]) -> dict:
    """Slet zip-uploads, der ikke bruges af en igangværende import, og delvise uploads, der ikke kan genoptages."""
    files = bytes_ = 0
    for p in root().iterdir():
        m = re.match(r"(\d+)\.(zip|part.*)$", p.name)
        if not m or not p.is_file():
            continue
        iid, kind = int(m.group(1)), m.group(2)
        if iid in busy or (kind != "zip" and iid in resumable):
            continue
        bytes_ += p.stat().st_size
        p.unlink(missing_ok=True)
        files += 1
    return {"filer": files, "bytes": bytes_}


async def purge(session: AsyncSession) -> dict:
    """Ryd ``uploads/``: behold zip-filer til importer, der er klar/kører, og delene af uploads, der stadig kan genoptages."""
    rows = (await session.execute(select(GalImport.id, GalImport.status))).all()
    busy = {i for i, st in rows if st in ("klar", "koerer")}
    resumable = {i for i, st in rows if st == "uploader"}
    return await asyncio.to_thread(_purge, busy, resumable)
