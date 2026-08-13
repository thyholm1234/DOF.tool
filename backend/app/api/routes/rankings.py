from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import MatrixResponse, YearRankingRow
from backend.app.db.models import Observation
from backend.app.db.session import get_db

router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.get("/yearly", response_model=list[YearRankingRow])
async def get_yearly_rankings(
    year: int | None = None,
    matrikel_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[YearRankingRow]:
    target_year = year or datetime.now().year
    start = date(target_year, 1, 1)
    end = date(target_year, 12, 31)

    query = (
        select(
            Observation.observer_code,
            func.count(func.distinct(Observation.species)).label("species_count"),
        )
        .where(
            Observation.observed_on >= start,
            Observation.observed_on <= end,
            Observation.observer_code.is_not(None),
        )
        .group_by(Observation.observer_code)
        .order_by(func.count(func.distinct(Observation.species)).desc())
    )
    if matrikel_only:
        query = query.where(Observation.is_matrikel.is_(True))

    result = await db.execute(query)
    rows = result.all()
    rankings: list[YearRankingRow] = []
    for idx, row in enumerate(rows, start=1):
        rankings.append(
            YearRankingRow(
                year=target_year,
                rank=idx,
                observer_name=row.observer_code,
                species_count=row.species_count or 0,
            )
        )
    return rankings


@router.get("/matrix", response_model=MatrixResponse)
async def get_observer_species_matrix(
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> MatrixResponse:
    target_year = year or datetime.now().year
    start = date(target_year, 1, 1)
    end = date(target_year, 12, 31)
    result = await db.execute(
        select(
            Observation.species,
            Observation.observer_code,
            func.min(Observation.observed_on).label("first_seen"),
        )
        .where(
            Observation.observed_on >= start,
            Observation.observed_on <= end,
            Observation.observer_code.is_not(None),
        )
        .group_by(Observation.species, Observation.observer_code)
    )
    rows = result.all()

    species = sorted({row.species for row in rows if row.species})
    observers = sorted({row.observer_code for row in rows if row.observer_code})

    first_seen_map: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        first_seen_map[row.species][row.observer_code] = row.first_seen.strftime("%d-%m-%Y")

    matrix = [
        [first_seen_map.get(sp, {}).get(observer, "") for observer in observers]
        for sp in species
    ]
    totals = [
        sum(1 for sp in species if first_seen_map.get(sp, {}).get(observer)) for observer in observers
    ]
    return MatrixResponse(arter=species, koder=observers, matrix=matrix, totals=totals)

