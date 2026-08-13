from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import AdminUserRequest, BlacklistRequest, PageViewRequest
from backend.app.core.config import get_settings
from backend.app.db.models import (
    AdminUser,
    BlacklistedUser,
    FlashcardModel,
    Observation,
    PageViewEvent,
    Trip,
    UserPreference,
)
from backend.app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin_key(token: str | None) -> None:
    if token != get_settings().admin_api_key:
        raise HTTPException(status_code=403, detail="Ugyldig admin nøgle.")


def _require_superadmin_key(token: str | None) -> None:
    if token != get_settings().superadmin_api_key:
        raise HTTPException(status_code=403, detail="Ugyldig superadmin nøgle.")


@router.get("/overview")
async def admin_overview(
    db: AsyncSession = Depends(get_db),
) -> dict:
    alerts_queue_size = await db.scalar(
        select(func.count(Observation.id)).where(
            Observation.category.in_(["SU", "SUB", "bemaerk"])
        )
    )
    active_users = await db.scalar(select(func.count(UserPreference.user_id)))
    pending_trip_requests = await db.scalar(select(func.count(Trip.id)))
    total_flashcards = await db.scalar(select(func.count(FlashcardModel.id)))
    blacklisted_users = await db.scalar(select(func.count(BlacklistedUser.user_id)))
    return {
        "alerts_queue_size": alerts_queue_size or 0,
        "active_users": active_users or 0,
        "pending_trip_requests": pending_trip_requests or 0,
        "flashcards": total_flashcards or 0,
        "blacklisted_users": blacklisted_users or 0,
    }


@router.post("/blacklist")
async def blacklist_user(
    payload: BlacklistRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    _require_admin_key(x_admin_key)
    existing = await db.get(BlacklistedUser, payload.user_id)
    if existing is None:
        db.add(BlacklistedUser(user_id=payload.user_id, reason=payload.reason))
    else:
        existing.reason = payload.reason
    await db.commit()
    return {"status": "ok", "user_id": payload.user_id}


@router.delete("/blacklist/{user_id}")
async def unblacklist_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    _require_admin_key(x_admin_key)
    existing = await db.get(BlacklistedUser, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Bruger ikke blacklistet.")
    await db.delete(existing)
    await db.commit()
    return {"status": "ok", "user_id": user_id}


@router.get("/blacklist")
async def list_blacklist(
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    _require_admin_key(x_admin_key)
    result = await db.execute(select(BlacklistedUser).order_by(BlacklistedUser.created_at.desc()))
    return {
        "items": [
            {"user_id": item.user_id, "reason": item.reason, "created_at": item.created_at}
            for item in result.scalars().all()
        ]
    }


@router.get("/admins")
async def list_admins(
    db: AsyncSession = Depends(get_db),
    x_superadmin_key: str | None = Header(default=None),
) -> dict:
    _require_superadmin_key(x_superadmin_key)
    result = await db.execute(select(AdminUser).order_by(AdminUser.created_at.asc()))
    return {
        "items": [
            {"user_id": item.user_id, "role": item.role, "created_at": item.created_at}
            for item in result.scalars().all()
        ]
    }


@router.post("/admins")
async def add_admin(
    payload: AdminUserRequest,
    db: AsyncSession = Depends(get_db),
    x_superadmin_key: str | None = Header(default=None),
) -> dict:
    _require_superadmin_key(x_superadmin_key)
    existing = await db.get(AdminUser, payload.user_id)
    if existing is None:
        db.add(AdminUser(user_id=payload.user_id, role=payload.role))
    else:
        existing.role = payload.role
    await db.commit()
    return {"status": "ok", "user_id": payload.user_id, "role": payload.role}


@router.delete("/admins/{user_id}")
async def remove_admin(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    x_superadmin_key: str | None = Header(default=None),
) -> dict:
    _require_superadmin_key(x_superadmin_key)
    existing = await db.get(AdminUser, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Admin ikke fundet.")
    await db.delete(existing)
    await db.commit()
    return {"status": "ok", "user_id": user_id}


@router.get("/is-admin/{user_id}")
async def is_admin(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    _require_admin_key(x_admin_key)
    admin = await db.get(AdminUser, user_id)
    return {"user_id": user_id, "is_admin": admin is not None, "role": admin.role if admin else None}


@router.post("/pageviews")
async def log_pageview(payload: PageViewRequest, db: AsyncSession = Depends(get_db)) -> dict:
    db.add(
        PageViewEvent(
            path=payload.path,
            user_id=payload.user_id,
            referrer=payload.referrer,
            device_id=payload.device_id,
        )
    )
    await db.commit()
    return {"status": "ok"}


@router.get("/traffic/summary")
async def pageview_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    _require_admin_key(x_admin_key)
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(
            PageViewEvent.path,
            func.count(PageViewEvent.id).label("views"),
            func.count(func.distinct(PageViewEvent.user_id)).label("unique_users"),
        )
        .where(PageViewEvent.created_at >= since)
        .group_by(PageViewEvent.path)
        .order_by(func.count(PageViewEvent.id).desc())
    )
    rows = result.all()
    total = sum(row.views for row in rows)
    return {
        "from": since.isoformat(),
        "to": datetime.now(tz=timezone.utc).isoformat(),
        "total_views": total,
        "paths": [
            {"path": row.path, "views": row.views, "unique_users": row.unique_users}
            for row in rows
        ],
    }
