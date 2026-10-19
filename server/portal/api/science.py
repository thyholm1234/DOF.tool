"""Fænologi (tidligere/senere end normalt) og DMI-vejr."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, get_json, version
from portal.db import get_session
from portal.services import dmi, migration, phenology, weather_archive
from portal.timeutil import dk_today

router = APIRouter(prefix="/api", tags=["science"])


@router.get("/pheno/overview")
async def overview():
    data = await get_json("pheno:overview")
    return data or {"generated": None, "species": []}


@router.get("/pheno/migration")
async def migration_map(days_back: int = Query(0, ge=0, le=2), db: AsyncSession = Depends(get_session)):
    """Trækretning pr. fuglegruppe og vind kl. 12 for i dag eller op til 2 dage tilbage."""
    day = dk_today() - timedelta(days=days_back)
    data = await cached(f"mig:day:{day}:{await version('feed') if not days_back else 0}", 600 if not days_back else 6 * 3600,
                        lambda: migration.migration_day(db, day))
    return {**data, "wind": await migration.wind_noon(day)}


@router.get("/pheno/species/{artnr}")
async def species(artnr: int, db: AsyncSession = Depends(get_session)):
    return await cached(f"pheno:sp:{artnr}:{dk_today()}", 3600, lambda: phenology.species_detail(db, artnr))


@router.get("/pheno/arrivals")
async def arrivals(year: int | None = None, db: AsyncSession = Depends(get_session)):
    y = year or dk_today().year
    return await cached(f"pheno:arr:{y}:{dk_today()}", 3600, lambda: phenology.arrivals(db, y))


@router.get("/pheno/climate")
async def climate(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("SELECT year, month, mean_temp FROM climate_month ORDER BY year, month"))).all()
    spring = await phenology.spring_temps(db)
    return {"months": [{"year": y, "month": m, "temp": t} for y, m, t in rows],
            "spring": [{"year": y, "temp": round(t, 2)} for y, t in sorted(spring.items())]}


@router.get("/pheno/effort")
async def effort(days: int = Query(400, le=6000), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT dato, n_trips, n_observers FROM effort_day WHERE dato > current_date - CAST(:d AS integer) ORDER BY dato
    """), {"d": days})).all()
    return [{"dato": d.isoformat(), "trips": t, "observers": o} for d, t, o in rows]


@router.get("/weather/current")
async def weather_current():
    return await dmi.current_weather()


@router.get("/weather/grid")
async def weather_grid(days: int = Query(0, ge=0, le=7), back: int = Query(0, ge=0, le=1)):
    """Uden `days`: aktuel vind. Med `days`: middelvind kl. 6–18 for de døgn kortet viser."""
    if not days:
        return await dmi.wind_grid() or {"u": [], "v": []}
    end = dk_today() - timedelta(days=back)
    return await dmi.wind_grid_period(end - timedelta(days=days - 1), end) or {"u": [], "v": []}


@router.get("/weather/forecast")
async def weather_forecast():
    return {"spots": await dmi.forecast(), "explain": (
        "Tommelfingerregler: forår (mar–maj) giver sydlige/østlige vinde medvind til nordgående trækfugle; "
        "efterår (aug–nov) giver nordlige vinde medvind mod syd. Regn og hård vind trækker ned. "
        "Seawatch-tallet stiger med pålandsvind på kystlokaliteterne. Data: DMI HARMONIE (frie data).")}


@router.get("/weather/day")
async def weather_day(loknr: int, day: date = Query(alias="date"), db: AsyncSession = Depends(get_session)):
    """Dagsvejr i lokalitetens 10 km-kvadrat fra vejrarkivet (DMI's grid). null hvis måneden ikke er hentet."""
    return await weather_archive.day_for_location(db, loknr, day)
