"""Levering af filer fra observationer (/media/obs/<navn>) fra objektlageret.

Login kræves af middlewaren i main.py. Range-forespørgsler understøttes, så lyd kan spoles (Safari kræver det)."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from portal.services import storage

router = APIRouter(include_in_schema=False)
RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


@router.api_route("/media/obs/{name}", methods=["GET", "HEAD"])
async def obs_media(name: str, request: Request) -> Response:
    if not storage.name_of(f"/media/obs/{name}"):
        raise HTTPException(404)
    start = end = None
    m = RANGE.match(request.headers.get("range", "").strip())
    if m and (m[1] or m[2]):
        size = await storage.stat(name)
        if size is None:
            raise HTTPException(404)
        if m[1]:
            start, end = int(m[1]), min(int(m[2]), size - 1) if m[2] else size - 1
        else:  # de sidste N bytes
            start, end = max(0, size - int(m[2])), size - 1
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    obj = await storage.get(name, start, end)
    if obj is None:
        raise HTTPException(404)
    etag = f'"{obj.etag}"'
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "private, max-age=86400"}
    if start is None and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    status = 200
    if start is not None:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{start + len(obj.data) - 1}/{obj.size}"
    body = b"" if request.method == "HEAD" else obj.data
    headers["Content-Length"] = str(len(obj.data))
    return Response(body, status_code=status, media_type=obj.content_type, headers=headers)
