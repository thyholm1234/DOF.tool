"""Brugerens position (fra appen i baggrunden eller browseren) og 'Nær mig'-området, der følger positionen.

Positionen bruges kun til brugerens egne filtre: et radius-område (`areas.ref = 'me'`) flyttes, hver gang positionen
ændrer sig, så feed, lister og alarmregler automatisk gælder omkring, hvor brugeren er. Den kan slås fra og slettes."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.api.geo import area_out
from portal.cache import kv, worker_command
from portal.db import get_session
from portal.models import Area, User, UserLocation
from portal.services import features
from portal.services.areas import scope_for_area, set_geometry
from portal.services.auth import SUPERADMIN

router = APIRouter(prefix="/api", tags=["position"])
ME = "me"
MOVE_M = 150  # mindre flytning end dette opdaterer ikke områdets geometri
TOKEN_TTL = 180 * 86400  # positionstokenet fornyes, hver gang appen starter delingen


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


async def store_location(db: AsyncSession, user_id: int, lat: float, lon: float, accuracy: float | None, source: str) -> bool:
    """Gem seneste position og flyt brugerens 'Nær mig'-områder med. Returnerer om områderne blev flyttet."""
    loc = await db.get(UserLocation, user_id)
    moved = True
    if loc is None:
        db.add(UserLocation(user_id=user_id, lat=lat, lon=lon, accuracy_m=accuracy, source=source))
    else:
        moved = (await db.execute(text("""
            SELECT ST_Distance(ST_SetSRID(ST_MakePoint(:a, :b), 4326)::geography, ST_SetSRID(ST_MakePoint(:c, :d), 4326)::geography) > :m
        """), {"a": loc.lon, "b": loc.lat, "c": lon, "d": lat, "m": MOVE_M})).scalar()
        loc.lat, loc.lon, loc.accuracy_m, loc.source = lat, lon, accuracy, source
        loc.updated_at = datetime.now(timezone.utc)
    if moved:
        await db.execute(text("""
            UPDATE areas SET geom = ST_Buffer(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, radius_m, 32)::geometry
            WHERE user_id = :u AND kind = 'radius' AND ref = :me
        """), {"lon": lon, "lat": lat, "u": user_id, "me": ME})
    await db.commit()
    return bool(moved)


@router.post("/me/location")
async def put_location(body: LocationIn, user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    return {"ok": True, "moved": await store_location(db, user.id, body.lat, body.lon, body.accuracy_m, body.source)}


@router.post("/me/location/token")
async def location_token(user: User = Depends(require_position)):
    """Token til appens positionstjeneste, der sender positionen direkte fra Android – også når appen er lukket, hvor
    login-cookien ikke er tilgængelig. Tokenet kan kun bruges til at melde en position."""
    old = await kv().get(f"loctok:user:{user.id}")
    if old:
        await kv().delete(f"loctok:{old}")
    token = secrets.token_urlsafe(32)
    await kv().set(f"loctok:{token}", user.id, ex=TOKEN_TTL)
    await kv().set(f"loctok:user:{user.id}", token, ex=TOKEN_TTL)
    return {"token": token}


class NativeLocationIn(BaseModel):
    """Det format @capgo/background-geolocation sender (feltnavne fra Android)."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float | None = None
    time: int | None = None


@router.post("/location/native")
async def native_location(body: NativeLocationIn, authorization: str = Header(""), db: AsyncSession = Depends(get_session)):
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await kv().get(f"loctok:{token}") if token else None
    user = await db.get(User, int(user_id)) if user_id else None
    if user is None or user.blocked or not user.approved or not (user.prefs or {}).get("share_location"):
        raise HTTPException(401, "Ugyldigt positionstoken")
    if body.accuracy is not None and body.accuracy > 500:
        return {"ok": True, "moved": False, "skipped": "unøjagtig"}
    return {"ok": True, "moved": await store_location(db, user.id, body.latitude, body.longitude, body.accuracy, "app")}


@router.delete("/me/location")
async def delete_location(user: User = Depends(require_position), db: AsyncSession = Depends(get_session)):
    """Stop med at gemme positionen. 'Nær mig'-området bliver stående, men flyttes ikke mere."""
    loc = await db.get(UserLocation, user.id)
    if loc is not None:
        await db.delete(loc)
        await db.commit()
    token = await kv().get(f"loctok:user:{user.id}")
    if token:
        await kv().delete(f"loctok:{token}", f"loctok:user:{user.id}")
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
    if (await db.execute(text("SELECT count(*) FROM areas WHERE user_id = :u"), {"u": user.id})).scalar() >= 30:
        raise HTTPException(400, "Højst 30 områder pr. bruger")
    radius_m = int(round(body.radius_km * 1000))
    name = f"Nær mig ({body.radius_km:g} km)".replace(".", ",")
    # Ét område pr. radius, så flere regler (fx 5 km for SU, 25 km for lokale hits) kan følge brugeren samtidig
    area = (await db.execute(select(Area).where(Area.user_id == user.id, Area.kind == "radius", Area.ref == ME,
                                                Area.radius_m == radius_m))).scalars().first()
    if area is None:
        area = Area(user_id=user.id, name=name, kind="radius", ref=ME, radius_m=radius_m, use_for_lists=False)
        db.add(area)
        await db.flush()
    await set_geometry(db, area, None, center)
    await db.commit()
    await worker_command("area_counts", scope=scope_for_area(area).key)
    return await area_out(db, area)
