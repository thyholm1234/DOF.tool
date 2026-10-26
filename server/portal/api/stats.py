"""Statistik-modulet (#/statistik). Slås til/fra under Admin → Funktioner (statistik).

Fanen Arter pr. år bruger /api/lists/regions; her ligger det, der kun findes i modulet."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import features, stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


async def need_stats(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "statistik", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("/nfl")
async def nfl(_: User = Depends(need_stats), db: AsyncSession = Depends(get_session)):
    return await stats.nfl(db)
