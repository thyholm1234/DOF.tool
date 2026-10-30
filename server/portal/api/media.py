"""Levering af filer fra observationer (/media/obs/<navn>) fra objektlageret.

Login kræves af middlewaren i main.py. Range-forespørgsler understøttes, så lyd kan spoles (Safari kræver det)."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from portal.services import content_media, storage

router = APIRouter(include_in_schema=False)
RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


@router.api_route("/media/obs/{name}", methods=["GET", "HEAD"])
async def obs_media(name: str, request: Request) -> Response:
    if not storage.name_of(f"/media/obs/{name}"):
        raise HTTPException(404)
    return await _serve(request, name, storage.PREFIX)


ART_NAME = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}(-[tm])?\.[a-z0-9]{2,5}$")


@router.api_route("/media/art/{name:path}", methods=["GET", "HEAD"])
async def art_media(name: str, request: Request) -> Response:
    """Artikelmodulets medier. Navnene er indholdets SHA-256 og kan ikke gættes. Cache-Control sættes af vagten i main.py (private, 24 timer)."""
    if not ART_NAME.match(name):
        raise HTTPException(404)
    ext = "." + name.rsplit(".", 1)[1]
    extra = {"X-Content-Type-Options": "nosniff"}
    if ext in content_media.DOWNLOAD_ONLY:  # kunne ellers vises som side i browseren
        extra["Content-Disposition"] = "attachment"
    elif ext == ".pdf":
        # PDF'er vises i en iframe på artikelsiden: browserens PDF-visning virker hverken med X-Frame-Options: DENY eller CSP sandbox
        extra["X-Frame-Options"] = "SAMEORIGIN"
        extra["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'"
    return await _serve(request, name, storage.ART_PREFIX, extra)


async def _serve(request: Request, name: str, prefix: str, extra: dict | None = None) -> Response:
    start = end = None
    m = RANGE.match(request.headers.get("range", "").strip())
    if m and (m[1] or m[2]):
        size = await storage.stat(name, prefix)
        if size is None:
            raise HTTPException(404)
        if m[1]:
            start, end = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
        else:  # de sidste N bytes
            start, end = max(0, size - int(m[2])), size - 1
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    obj = await storage.get(name, start, end, prefix)
    if obj is None:
        raise HTTPException(404)
    etag = f'"{obj.etag}"'
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "private, max-age=86400", **(extra or {})}
    if start is None and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    status = 200
    if start is not None:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{start + len(obj.data) - 1}/{obj.size}"
    body = b"" if request.method == "HEAD" else obj.data
    headers["Content-Length"] = str(len(obj.data))
    return Response(body, status_code=status, media_type=obj.content_type, headers=headers)
