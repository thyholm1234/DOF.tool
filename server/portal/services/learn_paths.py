"""Læring: forløb, hold og feltopgaver (docs/laering-spec.md §9–10). Udfyldes i fase 5–6."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User


async def catalog(db: AsyncSession, user: User | None) -> list[dict]:
    return []


async def after_progress(db: AsyncSession, user: User, course_id: int) -> None:
    return None
