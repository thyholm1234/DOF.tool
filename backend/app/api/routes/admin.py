from __future__ import annotations

from datetime import datetime, timedelta, timezone
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import AdminUserRequest, BlacklistRequest, PageViewRequest
from backend.app.core.config import get_settings
from backend.app.db.models import (
    AdminUser,
    BlacklistedUser,
    CommunityUser,
    DailySync,
    EventComment,
    FlashcardModel,
    Observation,
    ObservationComment,
    PageViewEvent,
    Trip,
    UserPreference,
)
from backend.app.db.session import get_db
from backend.app.services.community_auth import require_admin, require_superadmin
from backend.app.services.daily_sync import sync_day

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


@router.get("/session/dashboard")
async def session_dashboard(
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    latest_sync = await db.scalar(select(DailySync).order_by(DailySync.sync_date.desc()).limit(1))
    return {
        "overview": await admin_overview(db),
        "latest_sync": {
            "date": latest_sync.sync_date if latest_sync else None,
            "status": latest_sync.status if latest_sync else "never",
            "processed_count": latest_sync.processed_count if latest_sync else 0,
            "error": latest_sync.error if latest_sync else None,
        },
    }


@router.get("/session/users")
async def session_users(
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CommunityUser).order_by(CommunityUser.created_at.desc()))
    return {"items": [{"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "created_at": user.created_at} for user in result.scalars().all()]}


@router.delete("/session/users/{user_id}")
async def delete_session_user(
    user_id: str,
    _: CommunityUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(CommunityUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Bruger ikke fundet.")
    if user.role == "superadmin":
        raise HTTPException(status_code=400, detail="Superadmin kan ikke slettes her.")
    await db.delete(user)
    await db.commit()
    return {"status": "ok", "user_id": user_id}


@router.get("/session/blacklist")
async def session_blacklist(
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(BlacklistedUser).order_by(BlacklistedUser.created_at.desc()))
    return {"items": [{"user_id": item.user_id, "reason": item.reason, "created_at": item.created_at} for item in result.scalars().all()]}


@router.post("/session/blacklist")
async def session_add_blacklist(
    payload: BlacklistRequest,
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = await db.get(BlacklistedUser, payload.user_id)
    if item is None:
        db.add(BlacklistedUser(user_id=payload.user_id, reason=payload.reason))
    else:
        item.reason = payload.reason
    await db.commit()
    return {"status": "ok", "user_id": payload.user_id}


@router.delete("/session/blacklist/{user_id}")
async def session_remove_blacklist(
    user_id: str,
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    item = await db.get(BlacklistedUser, user_id)
    if item:
        await db.delete(item)
        await db.commit()
    return {"status": "ok", "user_id": user_id}


@router.get("/session/comments")
async def session_comments(
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(ObservationComment).order_by(ObservationComment.created_at.desc()).limit(500))
    return {"items": [{"id": item.id, "day": item.day, "thread_id": item.thread_id, "user_id": item.user_id, "display_name": item.display_name, "body": item.body, "created_at": item.created_at} for item in result.scalars().all()]}


@router.delete("/session/comments/{comment_id}")
async def session_delete_comment(
    comment_id: str,
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    comment = await db.get(ObservationComment, comment_id)
    if comment:
        await db.delete(comment)
        await db.commit()
    return {"status": "ok", "comment_id": comment_id}


@router.get("/session/traffic")
async def session_traffic(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(select(PageViewEvent.path, func.count(PageViewEvent.id).label("views")).where(PageViewEvent.created_at >= since).group_by(PageViewEvent.path).order_by(func.count(PageViewEvent.id).desc()))
    rows = result.all()
    return {"hours": hours, "total_views": sum(row.views for row in rows), "paths": [{"path": row.path, "views": row.views} for row in rows]}


@router.get("/session/pageview-stats")
async def session_pageview_stats(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await session_traffic(hours, _, db)


@router.get("/session/admins")
async def session_admins(
    _: CommunityUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CommunityUser).where(CommunityUser.role.in_(["admin", "superadmin"])).order_by(CommunityUser.created_at.asc()))
    return {"items": [{"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "created_at": user.created_at} for user in result.scalars().all()]}


@router.patch("/session/admins/{user_id}")
async def session_change_admin_role(
    user_id: str,
    payload: dict,
    _: CommunityUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(CommunityUser, user_id)
    if user is None or user.role == "superadmin":
        raise HTTPException(status_code=404, detail="Admin ikke fundet eller beskyttet.")
    role = payload.get("role")
    if role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="Ugyldig rolle.")
    user.role = role
    await db.commit()
    return {"status": "ok", "user_id": user_id, "role": role}


@router.post("/session/sync/today")
async def session_sync_today(_: CommunityUser = Depends(require_admin)) -> dict:
    target = datetime.now(timezone.utc).date()
    processed = await sync_day(target)
    return {"status": "ok", "target_date": target.isoformat(), "processed": processed}
