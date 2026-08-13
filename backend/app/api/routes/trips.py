from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import TripAnnouncementCreate, TripAnnouncementRead, TripJoinRequest
from backend.app.db.models import Trip, TripParticipant
from backend.app.db.session import get_db

router = APIRouter(prefix="/trips", tags=["trips"])


async def _to_trip_read(db: AsyncSession, trip: Trip) -> TripAnnouncementRead:
    joined_count = await db.scalar(
        select(func.count(TripParticipant.id)).where(TripParticipant.trip_id == trip.id)
    )
    return TripAnnouncementRead(
        id=trip.id,
        title=trip.title,
        location=trip.location,
        starts_at=trip.starts_at,
        max_participants=trip.max_participants,
        description=trip.description,
        created_by=trip.created_by,
        joined_count=joined_count or 0,
    )


@router.get("", response_model=list[TripAnnouncementRead])
async def list_trips(db: AsyncSession = Depends(get_db)) -> list[TripAnnouncementRead]:
    result = await db.execute(select(Trip).order_by(Trip.starts_at.asc()))
    trips = result.scalars().all()
    return [await _to_trip_read(db, trip) for trip in trips]


@router.post("", response_model=TripAnnouncementRead)
async def create_trip(
    payload: TripAnnouncementCreate,
    creator_user_id: str,
    db: AsyncSession = Depends(get_db),
) -> TripAnnouncementRead:
    trip = Trip(
        title=payload.title,
        location=payload.location,
        starts_at=payload.starts_at,
        max_participants=payload.max_participants,
        description=payload.description,
        created_by=creator_user_id,
    )
    db.add(trip)
    await db.flush()
    db.add(TripParticipant(trip_id=trip.id, user_id=creator_user_id))
    await db.commit()
    await db.refresh(trip)
    return await _to_trip_read(db, trip)


@router.post("/{trip_id}/join", response_model=TripAnnouncementRead)
async def join_trip(
    trip_id: str,
    payload: TripJoinRequest,
    db: AsyncSession = Depends(get_db),
) -> TripAnnouncementRead:
    trip = await db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Tur ikke fundet.")

    current_count = await db.scalar(
        select(func.count(TripParticipant.id)).where(TripParticipant.trip_id == trip_id)
    )
    if (current_count or 0) >= trip.max_participants:
        raise HTTPException(status_code=409, detail="Turen er fuldt booket.")

    existing = await db.execute(
        select(TripParticipant).where(
            TripParticipant.trip_id == trip_id, TripParticipant.user_id == payload.user_id
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(TripParticipant(trip_id=trip_id, user_id=payload.user_id))
        await db.commit()

    return await _to_trip_read(db, trip)

