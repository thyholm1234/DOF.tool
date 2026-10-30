"""Moderation → Nyheder: nyheder på forsiden."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Query,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    perm_indhold,
)
from portal.db import get_session
from portal.models import (
    NewsPost,
    User,
)

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------- nyheder


class NewsIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    published: bool = True


@router.get("/news")
async def news(limit: int = Query(10, le=50), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT n.id, n.title, n.body, n.created_at, n.published, u.navn AS author FROM news n LEFT JOIN users u ON u.id = n.author_id
        WHERE n.published ORDER BY n.created_at DESC LIMIT :n
    """), {"n": limit})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/admin/news")
async def create_news(body: NewsIn, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    n = NewsPost(author_id=user.id, **body.model_dump())
    db.add(n)
    await db.commit()
    return {"id": n.id}


@router.delete("/admin/news/{nid}")
async def delete_news(nid: int, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(NewsPost).where(NewsPost.id == nid))
    await db.commit()
    return {"ok": True}
