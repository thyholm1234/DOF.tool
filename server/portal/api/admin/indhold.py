"""Moderation → Nyheder: nyheder på forsiden, med den fælles markdown-editor (docs/artikler-modul-spec.md §7.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    perm_indhold,
)
from portal.api.deps import require_user
from portal.db import get_session
from portal.models import (
    User,
)
from portal.services import article_edit
from portal.services import news as news_svc
from portal.timeutil import TZ

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------- nyheder


class NewsIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=1, max_length=100000)  # markdown
    lead: str | None = Field(None, max_length=2000)
    cover_media_id: int | None = None
    status: Literal["kladde", "planlagt", "publiceret"] | None = None
    publish_at: datetime | None = None
    published: bool = True  # ældre klienter; bruges kun, når status mangler
    version: int | None = None

    def data(self) -> dict:
        d = self.model_dump()
        d["status"] = self.status or ("publiceret" if self.published else "kladde")
        if d["publish_at"] is not None and d["publish_at"].tzinfo is None:
            d["publish_at"] = d["publish_at"].replace(tzinfo=TZ)
        return d


@router.get("/news")
async def news(limit: int = Query(10, le=50), db: AsyncSession = Depends(get_session)):
    return await news_svc.public_list(db, limit)


@router.get("/news/{nid}")
async def get_news_item(nid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    n = await news_svc.public_get(db, nid)
    if n is None:
        raise HTTPException(404, "Nyheden findes ikke")
    return n


@router.get("/admin/news")
async def admin_news(user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    return await news_svc.admin_list(db)


@router.get("/admin/news/{nid}")
async def admin_news_item(nid: int, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    n = await news_svc.admin_get(db, nid)
    if n is None:
        raise HTTPException(404, "Nyheden findes ikke")
    return n


async def _save(db: AsyncSession, user: User, body: NewsIn, nid: int | None) -> dict:
    try:
        n = await news_svc.save(db, user, body.data(), nid)
    except article_edit.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except article_edit.Invalid as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": n.id, "version": n.version}


@router.post("/admin/news")
async def create_news(body: NewsIn, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    return await _save(db, user, body, None)


@router.put("/admin/news/{nid}")
async def update_news(nid: int, body: NewsIn, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    return await _save(db, user, body, nid)


@router.delete("/admin/news/{nid}")
async def delete_news(nid: int, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    await news_svc.delete(db, nid)
    return {"ok": True}
