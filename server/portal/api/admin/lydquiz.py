"""Admin → Funktioner → Lydquiz (superadmin): xeno-canto-nøgle og filtre til lydquizzen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import superadmin
from portal.db import get_session
from portal.models import User
from portal.services import activity, sound_quiz

router = APIRouter(prefix="/api/admin/lydquiz", tags=["admin"])


class SettingsIn(BaseModel):
    xc_key: str | None = Field(None, max_length=100)
    clear_key: bool = False
    min_quality: str | None = None
    area: str | None = None
    per_species: int | None = None
    max_length: int | None = None
    extra: str | None = None


async def _out(db: AsyncSession) -> dict:
    recs = (await db.execute(text("SELECT source, count(*), count(DISTINCT artnr) FROM snd_recordings WHERE enabled GROUP BY source"))).all()
    return {**sound_quiz.public_settings(await sound_quiz.settings(db)), "stats": {s: {"optagelser": n, "arter": a} for s, n, a in recs}}


@router.get("")
async def get_settings(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    return await _out(db)


@router.put("")
async def put_settings(body: SettingsIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    try:
        await sound_quiz.save_settings(db, body.model_dump())
    except (sound_quiz.SoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "lydquiz", None, {k: v for k, v in body.model_dump().items() if k != "xc_key"})
    return await _out(db)


@router.post("/test")
async def test(artnr: int | None = None, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Prøv xeno-canto med de gemte indstillinger på en art (standard: Rødhals) – viser søgningen og antal træf, uden at gemme noget."""
    row = (await db.execute(text("SELECT navn, latin FROM species WHERE (artnr = CAST(:a AS int) OR (CAST(:a AS int) IS NULL AND latin = 'Erithacus rubecula')) AND NOT excluded LIMIT 1"),
                            {"a": artnr})).mappings().first()
    if row is None:
        raise HTTPException(404, "Arten findes ikke")
    s = await sound_quiz.settings(db)
    try:
        found = await sound_quiz.xc_search(row["latin"], s)
    except sound_quiz.SoundError as exc:
        return {"ok": False, "fejl": str(exc), "soegning": sound_quiz.xc_query(row["latin"], s)}
    return {"ok": True, "art": row["navn"], "antal": len(found), "soegning": sound_quiz.xc_query(row["latin"], s)}


@router.delete("/cache")
async def clear(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Glem de automatisk hentede xeno-canto-optagelser, så de hentes igen med de aktuelle filtre."""
    return {"slettet": await sound_quiz.clear_cache(db)}
