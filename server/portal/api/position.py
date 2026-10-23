"""Brugerens position (fra appen i baggrunden eller browseren) og 'Nær mig'-området, der følger positionen.

Positionen bruges kun til brugerens egne filtre: et radius-område (`areas.ref = 'me'`) flyttes, hver gang positionen
ændrer sig, så feed, lister og alarmregler automatisk gælder omkring, hvor brugeren er. Den kan slås fra og slettes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.api.geo import area_out
from portal.cache import worker_command
from portal.db import get_session
from portal.models import Area, User, UserLocation
from portal.services import features
from portal.services.areas import scope_for_area, set_geometry
from portal.services.auth import SUPERADMIN

router = APIRouter(prefix="/api", tags=["position"])
ME = "me"
MOVE_M = 150  # mindre flytning end dette opdaterer ikke områdets geometri


async def require_position(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    flags = await features.load(db)
    if not features.visible(flags, "position", superadmin=user.role == SUPERADMIN, insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100_000)
    source: str = Field("app", pattern="^(app|web)$")


def _out(loc: UserLocation | None) -> dict:
    if loc is None:
        return {"location": None}
    return {"location": {"lat": loc.lat, "lon": loc.lon, "accuracy_m": loc.accuracy_m, "source": loc.source,
                         "updated_at": loc.updated_at}}


@router.get("/me/location")
async def get_location(user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    return _out(await db.get(UserLocation, user.id))


@router.post("/me/location")
async def put_location(body: LocationIn, user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    """Gem seneste position og flyt brugerens 'Nær mig'-områder med."""
    loc = await db.get(UserLocation, user.id)
    moved = True
    if loc is None:
        db.add(UserLocation(user_id=user.id, lat=body.lat, lon=body.lon, accuracy_m=body.accuracy_m, source=body.source))
    else:
        moved = (await db.execute(text("""
            SELECT ST_Distance(ST_SetSRID(ST_MakePoint(:a, :b), 4326)::geography, ST_SetSRID(ST_MakePoint(:c, :d), 4326)::geography) > :m
        """), {"a": loc.lon, "b": loc.lat, "c": body.lon, "d": body.lat, "m": MOVE_M})).scalar()
        loc.lat, loc.lon, loc.accuracy_m, loc.source = body.lat, body.lon, body.accuracy_m, body.source
        loc.updated_at = datetime.now(timezone.utc)
    if moved:
        await db.execute(text("""
            UPDATE areas SET geom = ST_Buffer(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, radius_m, 32)::geometry
            WHERE user_id = :u AND kind = 'radius' AND ref = :me
        """), {"lon": body.lon, "lat": body.lat, "u": user.id, "me": ME})
    await db.commit()
    return {"ok": True, "moved": bool(moved)}


@router.delete("/me/location")
async def delete_location(user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    """Stop med at gemme positionen. 'Nær mig'-området bliver stående, men flyttes ikke mere."""
    loc = await db.get(UserLocation, user.id)
    if loc is not None:
        await db.delete(loc)
        await db.commit()
    return {"ok": True}


class NearMeIn(BaseModel):
    radius_km: float = Field(gt=0, le=100)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


@router.put("/areas/near-me")
async def near_me(body: NearMeIn, user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    """Opret eller ret brugerens 'Nær mig'-område: en radius om den seneste position, som flytter sig med brugeren."""
    loc = await db.get(UserLocation, user.id)
    if body.lat is not None and body.lon is not None:
        center = (body.lat, body.lon)
    elif loc is not None:
        center = (loc.lat, loc.lon)
    else:
        raise HTTPException(400, "Positionen kendes ikke endnu – tillad position og prøv igen")
    radius_m = int(round(body.radius_km * 1000))
    name = f"Nær mig ({body.radius_km:g} km)".replace(".", ",")
    area = (await db.execute(select(Area).where(Area.user_id == user.id, Area.kind == "radius", Area.ref == ME))).scalars().first()
    if area is None:
        area = Area(user_id=user.id, name=name, kind="radius", ref=ME, radius_m=radius_m, use_for_lists=False)
        db.add(area)
        await db.flush()
    else:
        area.name, area.radius_m = name, radius_m
    await set_geometry(db, area, None, center)
    await db.commit()
    await worker_command("area_counts", scope=scope_for_area(area).key)
    return await area_out(db, area)
