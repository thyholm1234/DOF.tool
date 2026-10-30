"""Admin → Data og Vejr: datapaneler for DOFbasen og vejrarkivet (planer, kørsler, måneder, kø)."""

from __future__ import annotations

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    perm_data,
    perm_vejr,
)
from portal.api.deps import require_user
from portal.cache import worker_command
from portal.db import get_session
from portal.dof import gateway
from portal.models import (
    User,
)
from portal.services import (
    activity,
    datasync,
    weather_archive,
)
from portal.services.auth import (
    MODULES,
    can,
)

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------- datapaneler: DOFbasen og vejrarkiv



class PlanIn(BaseModel):
    tiers: dict[str, dict] = Field(default_factory=dict)
    start_year: int | None = None
    workers: int | None = None


async def _panel(db: AsyncSession, source: str) -> dict:
    plan = await datasync.get_plan(db, source)
    data = await datasync.dof_status(db) if source == "dof" else await weather_archive.status(db)
    extra = {}
    if source == "dof":
        g = await gateway.status()
        extra = {"queue_length": g.get("queue_length"), "calls_today": g.get(f"calls:{date.today().isoformat()}", 0),
                 "min_interval": g.get("min_interval")}
    return datasync.jsonable({**data, **extra, "plan": plan, "tiers": datasync.TIERS,
                              "last_runs": await datasync.last_runs(source),
                              "per_day": datasync.calls_per_day(source, plan["tiers"])})


def _source_perm(source: str, user: User) -> None:
    """Datapanelerne deles mellem DOFbasen (modulet data) og vejrarkivet (modulet vejr)."""
    if source not in ("dof", "weather"):
        raise HTTPException(404)
    module = "data" if source == "dof" else "vejr"
    if not can(user, module):
        raise HTTPException(403, f"Kræver adgang til {MODULES[module].split(':')[0]}")


@router.get("/admin/data/{source}")
async def data_panel(source: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    return await _panel(db, source)


@router.put("/admin/data/{source}/plan")
async def save_plan(source: str, body: PlanIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    try:
        await datasync.save_plan(db, source, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await _panel(db, source)


@router.post("/admin/data/{source}/run/{tier}")
async def run_tier(source: str, tier: str, user: User = Depends(require_user)):
    _source_perm(source, user)
    if tier not in dict(datasync.TIERS):
        raise HTTPException(400, "Ukendt vindue")
    if source == "dof" and tier == "today":
        # Dagen i dag: én live-hentning med det samme, uden at vente på minimumsintervallet
        if not await gateway.request_live_now():
            raise HTTPException(429, f"Dagen er lige bestilt – vent {gateway.LIVE_NOW_COOLDOWN} sekunder")
        return {"queued": True, "now": True}
    await worker_command("run_plan", source=source, tier=tier)
    return {"queued": True}


class MonthIn(BaseModel):
    year: int
    month: int = Field(ge=1, le=12)


@router.post("/admin/data/dof/month")
async def dof_month(body: MonthIn, user: User = Depends(perm_data)):
    from portal.dof.jobs import enqueue_month

    return {"job": await enqueue_month(body.year, body.month, gateway.NORMAL)}


@router.delete("/admin/data/{source}/month/{year}/{month}")
async def delete_month(source: str, year: int, month: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    if not 1 <= month <= 12:
        raise HTTPException(404)
    try:
        n = await (datasync if source == "dof" else weather_archive).delete_month(db, year, month)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await activity.track(user.id, "data_delete", None, {"source": source, "month": f"{year}-{month:02d}", "rows": n})
    return {"deleted": n}


class WeatherMonthsIn(BaseModel):
    months: list[tuple[int, int]] = Field(default_factory=list, max_length=600)
    only_missing: bool = False


@router.post("/admin/data/weather/months")
async def weather_months(body: WeatherMonthsIn, user: User = Depends(perm_vejr)):
    return {"queued": await weather_archive.enqueue(body.months, only_missing=body.only_missing)}


@router.delete("/admin/data/weather/queue")
async def weather_clear(user: User = Depends(perm_vejr)):
    return {"removed": await weather_archive.clear_queue()}
