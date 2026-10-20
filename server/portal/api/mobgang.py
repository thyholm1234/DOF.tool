"""Mobgang-generator: brugerens gemte presets (selve lydmiksningen sker i browseren)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import MobPreset, User

router = APIRouter(prefix="/api/mobgang", tags=["mobgang"])

MAX_PRESETS = 100


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]


def preset_out(p: MobPreset) -> dict:
    return {"id": p.id, "name": p.name, "config": p.config or {}, "updated_at": p.updated_at}


def _check(config: dict) -> None:
    if len(str(config)) > 8000:
        raise HTTPException(400, "Opsætningen er for stor")


async def _own(db: AsyncSession, user: User, preset_id: int) -> MobPreset:
    p = await db.get(MobPreset, preset_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404)
    return p


@router.get("/presets")
async def presets(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(MobPreset).where(MobPreset.user_id == user.id).order_by(MobPreset.name))).scalars().all()
    return [preset_out(p) for p in rows]


@router.post("/presets")
async def create_preset(body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    n = (await db.execute(select(func.count()).select_from(MobPreset).where(MobPreset.user_id == user.id))).scalar_one()
    if n >= MAX_PRESETS:
        raise HTTPException(400, f"Du kan højst gemme {MAX_PRESETS} presets")
    p = MobPreset(user_id=user.id, name=body.name.strip(), config=body.config)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return preset_out(p)


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: int, body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    p = await _own(db, user, preset_id)
    p.name, p.config = body.name.strip(), body.config
    await db.commit()
    await db.refresh(p)
    return preset_out(p)


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.delete(await _own(db, user, preset_id))
    await db.commit()
    return {"ok": True}
