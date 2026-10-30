"""Chunked upload af store zip-filer (artikelarkivet er flere GB; ét POST rammer proxy- og timeoutgrænser).

Delene skrives til deres plads i én fil (``<id>.part``); en tom markørfil ``<id>.part.<n>`` fortæller, at del n er modtaget. Sendes en del
igen, overskrives den (idempotent). Ved ``finish`` kontrolleres, at alle dele er der, at størrelsen passer, og at filen er en zip."""

from __future__ import annotations

import asyncio
import hashlib
import os
import zipfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import worker_command
from portal.config import get_settings
from portal.models import ArtImport

CHUNK = 32 * 1024 * 1024
MAX_SIZE = 20 * 1024 * 1024 * 1024


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def root() -> Path:
    p = Path(get_settings().article_import_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def parts_for(size: int) -> int:
    return max(1, -(-size // CHUNK))


async def create(session: AsyncSession, user_id: int, filename: str, size: int, sha256: str | None) -> ArtImport:
    if size <= 0 or size > MAX_SIZE:
        raise UploadError(400, "Filen har en ugyldig størrelse (højst 20 GB)")
    if not filename.lower().endswith(".zip"):
        raise UploadError(400, "Vælg en .zip-fil")
    imp = ArtImport(filename=filename[:300], size=size, sha256=(sha256 or "").lower() or None, uploaded_by=user_id, status="uploader")
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


async def write_part(imp: ArtImport, n: int, stream) -> int:
    """Skriv del n fra en asynkron bytestrøm. Returnerer antal bytes."""
    if imp.status != "uploader":
        raise UploadError(409, "Importen er ikke længere i upload-fasen")
    total = parts_for(imp.size)
    if not 0 <= n < total:
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


def received(imp: ArtImport) -> list[int]:
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


async def finish(session: AsyncSession, imp: ArtImport, options: dict) -> ArtImport:
    if imp.status != "uploader":
        raise UploadError(409, "Importen er allerede startet")
    missing = sorted(set(range(parts_for(imp.size))) - set(received(imp)))
    if missing:
        raise UploadError(400, f"Der mangler {len(missing)} dele (første: {missing[0]})")
    src = root() / f"{imp.id}.part"
    if src.stat().st_size != imp.size:
        raise UploadError(400, "Filens størrelse passer ikke")
    if imp.sha256 and await asyncio.to_thread(_sha, src) != imp.sha256:
        raise UploadError(400, "Kontrolsummen (SHA-256) passer ikke – send filen igen")
    if not await asyncio.to_thread(zipfile.is_zipfile, src):
        raise UploadError(400, "Filen er ikke en gyldig zip")
    await asyncio.to_thread(os.replace, src, root() / f"{imp.id}.zip")
    await asyncio.to_thread(_cleanup, imp.id)
    keep = {"overwrite_local", "import_comments", "publish", "dry_run", "fetch_images", "keep_zip", "force"}
    imp.options = {k: bool(v) for k, v in options.items() if k in keep}
    imp.status = "klar"
    await session.commit()
    await worker_command("articles_import", import_id=imp.id)
    return imp


async def discard(session: AsyncSession, imp: ArtImport) -> None:
    if imp.status == "koerer":
        raise UploadError(409, "Importen kører – vent til den er færdig")
    await asyncio.to_thread(_cleanup, imp.id)
    (root() / f"{imp.id}.zip").unlink(missing_ok=True)
    await session.delete(imp)
    await session.commit()
