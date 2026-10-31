"""Observatørsider (services/observers.py). Kræver login og funktionen profiler (Admin → Funktioner)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import features, observers

router = APIRouter(prefix="/api/observers", tags=["observers"])


async def need_profiles(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "profiler", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("")
async def search(q: str = Query(min_length=2, max_length=60), user: User = Depends(need_profiles), db: AsyncSession = Depends(get_session)):
    return await observers.search(db, q, user)


@router.get("/{kode}")
async def profile(kode: str, user: User = Depends(need_profiles), db: AsyncSession = Depends(get_session)):
    if not 2 <= len(kode) <= 16:
        raise HTTPException(404, "Observatøren findes ikke")
    p = await observers.profile(db, kode, user)
    if p is None:
        raise HTTPException(404, "Observatøren findes ikke")
    return p
