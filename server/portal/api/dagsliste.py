"""Dagslisten (#/dagsliste): den traditionelle liste over dagens observationer med samme filtre som DOFbasens observationsside –
visning, dato, sortering, summering og afdeling. Slås til/fra under Admin → Funktioner (dagsliste)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import dofbasen as svc
from portal.services import features
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/dagsliste", tags=["dagsliste"])


async def need_dagsliste(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "dagsliste", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("/afdelinger")
async def afdelinger(user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    return [dict(r) for r in (await db.execute(text("SELECT id, navn FROM afdelinger ORDER BY id"))).mappings()]


@router.get("")
async def dagsliste(dato: date | None = None, visning: str = Query("fugle", pattern=svc.VISNING_RE), lok: int | None = None, lokalitet: int | None = None,
                    summering: str = Query("tur", pattern="^(tur|dato|ingen)$"), user: User = Depends(need_dagsliste), db: AsyncSession = Depends(get_session)):
    d = dato or dk_today()
    if d > dk_today():
        raise HTTPException(422, "Datoen ligger i fremtiden")
    return await svc.day_observations(db, user.obserkode, d, visning, lok, lokalitet, summering)
