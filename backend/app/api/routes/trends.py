from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import TrendSignal
from backend.app.db.models import Observation
from backend.app.db.session import get_db

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("/signals", response_model=list[TrendSignal])
async def get_current_trends(
    days: int = Query(default=7, ge=3, le=30),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[TrendSignal]:
    now = datetime.now(tz=timezone.utc)
    recent_start = now - timedelta(days=days)
    baseline_start = recent_start - timedelta(days=days)

    recent = await db.execute(
        select(Observation.species, func.count(Observation.id).label("cnt"))
        .where(Observation.observed_at >= recent_start)
        .group_by(Observation.species)
    )
    baseline = await db.execute(
        select(Observation.species, func.count(Observation.id).label("cnt"))
        .where(Observation.observed_at >= baseline_start, Observation.observed_at < recent_start)
        .group_by(Observation.species)
    )

    recent_map = {row.species: row.cnt for row in recent.all()}
    baseline_map = {row.species: row.cnt for row in baseline.all()}
    signals: list[TrendSignal] = []
    for species, recent_count in recent_map.items():
        baseline_count = baseline_map.get(species, 0)
        score = (recent_count + 1) / (baseline_count + 1)
        if recent_count < 2:
            continue
        if score >= 1.25:
            note = "Stigende aktivitet ift. forrige periode."
        elif score <= 0.75:
            note = "Faldende aktivitet ift. forrige periode."
        else:
            note = "Stabil aktivitet."
        signals.append(
            TrendSignal(
                species=species,
                trend_score=round(score, 2),
                recent_count=recent_count,
                baseline_count=baseline_count,
                note=note,
            )
        )

    signals.sort(key=lambda item: item.trend_score, reverse=True)
    return signals[:limit]

