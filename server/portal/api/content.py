"""Fælles for artikler og nyheder: forhåndsvisning af markdown og upload af medier til editoren (docs/artikler-modul-spec.md §7.3)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import kv
from portal.db import get_session
from portal.models import User
from portal.services import article_access, article_species, content_media, content_render
from portal.services.auth import can

router = APIRouter(prefix="/api/content", tags=["content"])


async def editor(owner_kind: str, user: User, db: AsyncSession) -> None:
    """Må brugeren redigere indhold af denne slags? Artikler: modulet «artikler» (og funktionen slået til). Nyheder: modulet «indhold»."""
    if owner_kind == "nyhed":
        if not can(user, "indhold"):
            raise HTTPException(403, "Kræver adgang til Nyheder")
    elif owner_kind == "artikel":
        acc = await article_access.context(db, user)
        if not acc.can_edit:
            raise HTTPException(403, "Kræver adgang til Artikler")
    else:
        raise HTTPException(400, "Ukendt type")


class RenderIn(BaseModel):
    md: str = Field(max_length=200_000)
    profile: Literal["artikel", "nyhed"] = "artikel"


@router.post("/render")
async def render(body: RenderIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Forhåndsvisning: serveren renderer og renser, så visningen er identisk med resultatet."""
    await editor("nyhed" if body.profile == "nyhed" else "artikel", user, db)
    key = f"contentrender:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 60)
    if n > 120:
        raise HTTPException(429, "For mange forhåndsvisninger – vent lidt")
    r = await content_render.render_for(db, body.md, body.profile)
    species = await article_species.detect(db, r.text) if body.profile == "artikel" else []
    words = len(r.text.split())
    return {"html": r.html, "warnings": r.warnings, "species": species, "words": words, "chars": len(r.text), "minutes": max(1, round(words / 200))}


async def _media(db: AsyncSession, mid: int, user: User):
    m = await content_media.get(db, mid)
    if m is None:
        raise HTTPException(404, "Mediet findes ikke")
    await editor(m.owner_kind, user, db)
    return m


class MediaPatch(BaseModel):
    alt: str | None = Field(None, max_length=300)
    caption: str | None = Field(None, max_length=300)
    credit: str | None = Field(None, max_length=160)


@router.post("/media")
async def upload(file: UploadFile = File(...), owner_kind: Literal["artikel", "nyhed"] = Form(...), owner_id: int | None = Form(None),
                 alt: str = Form("", max_length=300), caption: str = Form("", max_length=300), credit: str = Form("", max_length=160),
                 user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await editor(owner_kind, user, db)
    data = await file.read(content_media.MAX_FILE + 1)
    try:
        if content_media.sniff_image(data[:16]):
            m = await content_media.store_image(db, data, filename=file.filename or "billede", owner_kind=owner_kind, owner_id=owner_id,
                                                user_id=user.id, alt=alt, caption=caption, credit=credit)
        else:
            m = await content_media.store_file(db, data, filename=file.filename or "fil", owner_kind=owner_kind, owner_id=owner_id,
                                               user_id=user.id, title=caption)
    except content_media.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    await db.commit()
    return content_media.public(m)


@router.get("/media")
async def list_media(owner_kind: Literal["artikel", "nyhed"], owner_id: int | None = Query(None), user: User = Depends(require_user),
                     db: AsyncSession = Depends(get_session)):
    """Medier til medievælgeren: ejerens egne, eller – for et nyt, endnu ikke gemt indlæg – dem brugeren selv har uploadet."""
    await editor(owner_kind, user, db)
    return [content_media.public(m) for m in await content_media.owned(db, owner_kind, owner_id, user.id)]


@router.patch("/media/{mid}")
async def patch_media(mid: int, body: MediaPatch, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    m = await _media(db, mid, user)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(m, k, v)
    await db.commit()
    return content_media.public(m)


@router.delete("/media/{mid}")
async def delete_media(mid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    m = await _media(db, mid, user)
    await content_media.delete_media(db, m)
    await db.commit()
    return {"ok": True}
