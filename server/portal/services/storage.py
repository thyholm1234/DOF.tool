"""Objektlager til filer uploadet til observationer (fotos og lyd i feltmeldinger og ture).

Med S3_ENDPOINT gemmes filerne i en S3-bucket (RustFS i compose, men alt S3-kompatibelt virker); uden ligger de på
disken under MEDIA_DIR som før. Adresserne er de samme i begge tilfælde (/media/obs/<navn>), og filerne leveres af
api/media.py, så de aldrig er offentlige i bucket'en. S3-klienten (minio-py) er synkron og køres i en tråd.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from portal.config import get_settings

log = logging.getLogger("portal.storage")
PREFIX = "obs/"  # nøgle i bucket'en = PREFIX + filnavn; adressen = /media/obs/<filnavn>


@dataclass
class Obj:
    data: bytes
    size: int  # hele objektets størrelse (også ved delvis læsning)
    content_type: str
    etag: str


def enabled() -> bool:
    return bool(get_settings().s3_endpoint)


@lru_cache
def _client():
    from minio import Minio

    s = get_settings()
    return Minio(s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key, secure=s.s3_secure,
                 region=s.s3_region or None)


def _local(key: str) -> Path:
    root = Path(get_settings().media_dir).resolve()
    p = (root / key).resolve()
    if root not in p.parents:
        raise ValueError("Ugyldig nøgle")
    return p


def _ensure_bucket() -> None:
    c, bucket = _client(), get_settings().s3_bucket
    if not c.bucket_exists(bucket):
        c.make_bucket(bucket)


async def init() -> None:
    """Opret bucket'en ved opstart. Er lageret nede, startes appen alligevel – uploads fejler så, til det er oppe."""
    if enabled():
        try:
            await asyncio.to_thread(_ensure_bucket)
        except Exception as exc:  # noqa: BLE001
            log.warning("Objektlageret svarer ikke (%s) – uploads virker ikke, før det er oppe", exc)


def _put(key: str, data: bytes, content_type: str) -> None:
    if enabled():
        import io

        _client().put_object(get_settings().s3_bucket, key, io.BytesIO(data), len(data), content_type=content_type)
    else:
        p = _local(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _get(key: str, start: int | None, end: int | None) -> Obj | None:
    if enabled():
        from minio.error import S3Error

        c, bucket = _client(), get_settings().s3_bucket
        try:
            st = c.stat_object(bucket, key)
            length = None if start is None else (min(end, st.size - 1) if end is not None else st.size - 1) - start + 1
            r = c.get_object(bucket, key, offset=start or 0, length=length or 0)
            try:
                data = r.read()
            finally:
                r.close()
                r.release_conn()
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket"):
                return None
            raise
        return Obj(data, st.size, st.content_type or "application/octet-stream", st.etag)
    p = _local(key)
    if not p.is_file():
        return None
    size = p.stat().st_size
    with p.open("rb") as fh:
        if start is not None:
            fh.seek(start)
            data = fh.read((min(end, size - 1) if end is not None else size - 1) - start + 1)
        else:
            data = fh.read()
    st = p.stat()
    return Obj(data, size, mimetypes.guess_type(p.name)[0] or "application/octet-stream", f"{st.st_mtime_ns:x}-{size:x}")


def _stat(key: str) -> int | None:
    if enabled():
        from minio.error import S3Error

        try:
            return _client().stat_object(get_settings().s3_bucket, key).size
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchBucket"):
                return None
            raise
    p = _local(key)
    return p.stat().st_size if p.is_file() else None


def _delete(key: str) -> None:
    if enabled():
        _client().remove_object(get_settings().s3_bucket, key)
    else:
        _local(key).unlink(missing_ok=True)


def _usage() -> tuple[int, int]:
    if enabled():
        n = size = 0
        for o in _client().list_objects(get_settings().s3_bucket, prefix=PREFIX, recursive=True):
            n += 1
            size += o.size or 0
        return n, size
    root = _local(PREFIX)
    files = [p.stat().st_size for p in root.rglob("*") if p.is_file()] if root.is_dir() else []
    return len(files), sum(files)


async def put(name: str, data: bytes, content_type: str) -> str:
    """Gem en fil og returnér dens adresse."""
    await asyncio.to_thread(_put, PREFIX + name, data, content_type)
    return f"/media/{PREFIX}{name}"


async def get(name: str, start: int | None = None, end: int | None = None) -> Obj | None:
    return await asyncio.to_thread(_get, PREFIX + name, start, end)


async def stat(name: str) -> int | None:
    """Filens størrelse i bytes, eller None hvis den ikke findes."""
    return await asyncio.to_thread(_stat, PREFIX + name)


def name_of(url: str | None) -> str | None:
    """Filnavnet i en adresse som /media/obs/<navn> (None for andre adresser)."""
    prefix = f"/media/{PREFIX}"
    if not url or not url.startswith(prefix):
        return None
    name = url[len(prefix):]
    return name if name and "/" not in name and name not in (".", "..") else None


async def delete_urls(urls) -> None:
    for name in filter(None, (name_of(u) for u in urls)):
        try:
            await asyncio.to_thread(_delete, PREFIX + name)
        except Exception as exc:  # noqa: BLE001 – en manglende fil må ikke stoppe sletningen af resten
            log.warning("Kunne ikke slette %s: %s", name, exc)


async def usage() -> tuple[int, int]:
    """(antal filer, bytes) i lageret."""
    return await asyncio.to_thread(_usage)


def _migrate_local() -> int:
    """Flyt filer fra disken (MEDIA_DIR/obs) til bucket'en – engangskørsel, når S3 slås til."""
    root = Path(get_settings().media_dir) / PREFIX
    if not enabled() or not root.is_dir():
        return 0
    _ensure_bucket()
    n = 0
    for p in sorted(root.iterdir()):
        if p.is_file() and not p.name.endswith(".tmp"):
            _put(PREFIX + p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            p.unlink()
            n += 1
    return n


async def migrate_local() -> int:
    return await asyncio.to_thread(_migrate_local)
