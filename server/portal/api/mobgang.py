"""Mobgang-generator: officielle og private presets (selve lydmiksningen sker i browseren).

Officielle presets ses af alle (også uden login) og vedligeholdes af redaktører og op.
Private presets ses kun af brugeren selv."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.db import get_session
from portal.models import MobPreset, Setting, User
from portal.services.auth import role_at_least

log = logging.getLogger("portal.mobgang")
router = APIRouter(prefix="/api/mobgang", tags=["mobgang"])

MAX_PRESETS = 100
OFFICIAL_ROLE = "editor"


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any]
    official: bool = False


def preset_out(p: MobPreset, user: User | None) -> dict:
    editable = role_at_least(user, OFFICIAL_ROLE) if p.official else bool(user and p.user_id == user.id)
    return {"id": p.id, "name": p.name, "config": p.config or {}, "official": p.official, "editable": editable,
            "updated_at": p.updated_at}


def _check(config: dict) -> None:
    if len(str(config)) > 8000:
        raise HTTPException(400, "Opsætningen er for stor")


def _may_official(user: User) -> None:
    if not role_at_least(user, OFFICIAL_ROLE):
        raise HTTPException(403, "Kun redaktører kan gemme officielle presets")


async def _editable(db: AsyncSession, user: User, preset_id: int) -> MobPreset:
    p = await db.get(MobPreset, preset_id)
    if p is None or (not p.official and p.user_id != user.id):
        raise HTTPException(404)
    if p.official:
        _may_official(user)
    return p


@router.get("/presets")
async def presets(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    """Officielle presets først, derefter brugerens egne."""
    cond = MobPreset.official if user is None else or_(MobPreset.official, MobPreset.user_id == user.id)
    rows = (await db.execute(select(MobPreset).where(cond).order_by(MobPreset.official.desc(), MobPreset.name))).scalars().all()
    return [preset_out(p, user) for p in rows]


@router.post("/presets")
async def create_preset(body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    if body.official:
        _may_official(user)
    else:
        n = (await db.execute(select(func.count()).select_from(MobPreset)
                              .where(MobPreset.user_id == user.id, ~MobPreset.official))).scalar_one()
        if n >= MAX_PRESETS:
            raise HTTPException(400, f"Du kan højst gemme {MAX_PRESETS} presets")
    p = MobPreset(user_id=user.id, name=body.name.strip(), config=body.config, official=body.official)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return preset_out(p, user)


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: int, body: PresetIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _check(body.config)
    p = await _editable(db, user, preset_id)
    if body.official != p.official:
        _may_official(user)  # gøre et privat preset officielt eller omvendt
    p.name, p.config, p.official = body.name.strip(), body.config, body.official
    await db.commit()
    await db.refresh(p)
    return preset_out(p, user)


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.delete(await _editable(db, user, preset_id))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- officielle startpresets

ALL = ("solsort", "fyrremejse", "hvidbrynet", "roedtoppet", "vindrossel", "gulbug", "graafluesnapper")


def _cfg(target: str = "", boost: int = 6, duck: int = -6, **species: int) -> dict:
    """species: art-id → antal individer (arter der ikke nævnes er slået fra)."""
    return {"v": 1, "master": 80, "target": target, "boost": boost, "duck": duck,
            "species": {s: {"on": s in species, "count": species.get(s, 1), "vol": 80} for s in ALL}}


SEED = [
    ("Alle arter – lige højt", _cfg(**{s: 1 for s in ALL})),
    ("Hvidbrynet Løvsanger i mejseflokken", _cfg("hvidbrynet", 9, -6, fyrremejse=3, roedtoppet=2, hvidbrynet=1, solsort=1)),
    ("Rødtoppet Fuglekonge i mejseflokken", _cfg("roedtoppet", 9, -6, fyrremejse=3, roedtoppet=1, hvidbrynet=1)),
    ("Efterårstræk: Vindrossel og Solsort", _cfg("vindrossel", 6, -6, vindrossel=4, solsort=2)),
    ("Sommerkrattet: Gulbug", _cfg("gulbug", 6, -6, gulbug=1, graafluesnapper=1, solsort=2, fyrremejse=1)),
    ("Sommerkrattet: Grå Fluesnapper", _cfg("graafluesnapper", 9, -9, graafluesnapper=1, gulbug=1, solsort=2, fyrremejse=1)),
]
SEED_KEY = "mobgang_seed_v1"


async def seed_presets(session: AsyncSession) -> None:
    """Opret de officielle startpresets én gang (redaktører kan derefter rette og slette dem)."""
    if await session.get(Setting, SEED_KEY) is not None:
        return
    for name, cfg in SEED:
        session.add(MobPreset(user_id=None, name=name, config=cfg, official=True))
    session.add(Setting(key=SEED_KEY, value={"n": len(SEED)}))
    await session.commit()
    log.info("Oprettede %s officielle mobgang-presets", len(SEED))
