from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import (
    CommunityAdminCreate,
    CommunityContactCreate,
    CommunityDocumentCreate,
    CommunityEventCreate,
    CommunityNewsCreate,
    EventCommentCreate,
    PushSubscriptionCreate,
)
from backend.app.core.config import get_settings
from backend.app.db.models import (
    CommunityContact,
    CommunityDocument,
    CommunityEvent,
    CommunityNews,
    CommunityUser,
    EventComment,
    EventRegistration,
    PushSubscription,
)
from backend.app.db.session import get_db
from backend.app.services.community_auth import (
    get_current_user,
    hash_password,
    require_admin,
    require_superadmin,
)

router = APIRouter(tags=["community"])


async def _event_payload(event: CommunityEvent, db: AsyncSession, user_id: str | None = None) -> dict:
    registered = await db.scalar(
        select(func.count(EventRegistration.id)).where(EventRegistration.event_id == event.id)
    )
    user_registered = False
    if user_id:
        user_registered = await db.scalar(
            select(EventRegistration.id).where(
                EventRegistration.event_id == event.id,
                EventRegistration.user_id == user_id,
            )
        ) is not None
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "location": event.location,
        "category": event.category,
        "image_url": event.image_url,
        "signup_url": event.signup_url,
        "max_participants": event.max_participants,
        "recurrence_rule": event.recurrence_rule,
        "recurrence_until": event.recurrence_until,
        "registered_count": registered or 0,
        "user_registered": user_registered,
    }


@router.get("/community/overview")
async def community_overview(db: AsyncSession = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    events_result = await db.execute(
        select(CommunityEvent)
        .where(CommunityEvent.starts_at >= now)
        .order_by(CommunityEvent.starts_at.asc())
        .limit(6)
    )
    news_result = await db.execute(
        select(CommunityNews)
        .order_by(CommunityNews.published_at.desc())
        .limit(5)
    )
    contacts_result = await db.execute(
        select(CommunityContact).order_by(CommunityContact.sort_order, CommunityContact.name)
    )
    documents_result = await db.execute(
        select(CommunityDocument).order_by(CommunityDocument.created_at.desc()).limit(6)
    )
    return {
        "events": [await _event_payload(item, db) for item in events_result.scalars().all()],
        "news": [
            {
                "id": item.id,
                "title": item.title,
                "body_markdown": item.body_markdown,
                "category": item.category,
                "image_url": item.image_url,
                "published_at": item.published_at,
            }
            for item in news_result.scalars().all()
        ],
        "contacts": [
            {
                "id": item.id,
                "name": item.name,
                "role": item.role,
                "email": item.email,
                "phone": item.phone,
                "image_url": item.image_url,
            }
            for item in contacts_result.scalars().all()
        ],
        "documents": [
            {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "category": item.category,
                "file_url": item.file_url,
            }
            for item in documents_result.scalars().all()
        ],
    }


@router.get("/community/events")
async def list_events(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(CommunityEvent).order_by(CommunityEvent.starts_at.asc()))
    return [await _event_payload(item, db) for item in result.scalars().all()]


@router.get("/community/events/{event_id}")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(CommunityEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Arrangementet blev ikke fundet.")
    comments_result = await db.execute(
        select(EventComment, CommunityUser.display_name)
        .join(CommunityUser, CommunityUser.id == EventComment.user_id)
        .where(EventComment.event_id == event_id)
        .order_by(EventComment.created_at.asc())
    )
    payload = await _event_payload(event, db)
    payload["comments"] = [
        {"id": comment.id, "body": comment.body, "author": name, "created_at": comment.created_at}
        for comment, name in comments_result.all()
    ]
    return payload


@router.post("/community/events/{event_id}/register")
async def register_for_event(
    event_id: str,
    user: CommunityUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    event = await db.get(CommunityEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Arrangementet blev ikke fundet.")
    existing = await db.scalar(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id, EventRegistration.user_id == user.id
        )
    )
    if existing:
        return {"status": "ok", "registered": True}
    registered_count = await db.scalar(
        select(func.count(EventRegistration.id)).where(EventRegistration.event_id == event_id)
    )
    if event.max_participants and (registered_count or 0) >= event.max_participants:
        raise HTTPException(status_code=409, detail="Arrangementet er fuldt booket.")
    db.add(EventRegistration(event_id=event_id, user_id=user.id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Du er allerede tilmeldt.")
    return {"status": "ok", "registered": True}


@router.delete("/community/events/{event_id}/register")
async def unregister_from_event(
    event_id: str,
    user: CommunityUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    registration = await db.scalar(
        select(EventRegistration).where(
            EventRegistration.event_id == event_id, EventRegistration.user_id == user.id
        )
    )
    if registration:
        await db.delete(registration)
        await db.commit()
    return {"status": "ok", "registered": False}


@router.post("/community/events/{event_id}/comments", status_code=201)
async def add_event_comment(
    event_id: str,
    payload: EventCommentCreate,
    user: CommunityUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await db.get(CommunityEvent, event_id) is None:
        raise HTTPException(status_code=404, detail="Arrangementet blev ikke fundet.")
    comment = EventComment(event_id=event_id, user_id=user.id, body=payload.body.strip())
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {"id": comment.id, "body": comment.body, "author": user.display_name, "created_at": comment.created_at}


@router.post("/community/push-subscriptions")
async def save_push_subscription(
    payload: PushSubscriptionCreate,
    user: CommunityUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    subscription = await db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
    )
    if subscription is None:
        subscription = PushSubscription(
            user_id=user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.get("p256dh", ""),
            auth=payload.keys.get("auth", ""),
        )
        db.add(subscription)
    else:
        subscription.user_id = user.id
        subscription.p256dh = payload.keys.get("p256dh", "")
        subscription.auth = payload.keys.get("auth", "")
    await db.commit()
    return {"status": "ok"}


@router.get("/community/push-key")
async def get_push_key() -> dict:
    return {"vapid_public_key": get_settings().vapid_public_key}


@router.post("/community/admin/events", status_code=201)
async def create_event(
    payload: CommunityEventCreate,
    user: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    event_data = payload.model_dump()
    if event_data["starts_at"].tzinfo is None:
        event_data["starts_at"] = event_data["starts_at"].replace(tzinfo=timezone.utc)
    if event_data["ends_at"] is not None and event_data["ends_at"].tzinfo is None:
        event_data["ends_at"] = event_data["ends_at"].replace(tzinfo=timezone.utc)
    if event_data["recurrence_until"] is not None and event_data["recurrence_until"].tzinfo is None:
        event_data["recurrence_until"] = event_data["recurrence_until"].replace(tzinfo=timezone.utc)
    event = CommunityEvent(**event_data, created_by=user.id)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return await _event_payload(event, db)


@router.post("/community/admin/news", status_code=201)
async def create_news(
    payload: CommunityNewsCreate,
    user: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    news = CommunityNews(**payload.model_dump(), created_by=user.id)
    db.add(news)
    await db.commit()
    await db.refresh(news)
    return {"id": news.id, "title": news.title, "published_at": news.published_at}


@router.post("/community/admin/contacts", status_code=201)
async def create_contact(
    payload: CommunityContactCreate,
    _: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    contact = CommunityContact(**payload.model_dump())
    db.add(contact)
    await db.commit()
    return {"status": "ok", "id": contact.id}


@router.post("/community/admin/documents", status_code=201)
async def create_document(
    payload: CommunityDocumentCreate,
    user: CommunityUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    document = CommunityDocument(**payload.model_dump(), created_by=user.id)
    db.add(document)
    await db.commit()
    return {"status": "ok", "id": document.id}


@router.post("/community/admin/admins", status_code=201)
async def create_admin(
    payload: CommunityAdminCreate,
    _: CommunityUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    email = payload.email.strip().lower()
    if await db.scalar(select(CommunityUser).where(func.lower(CommunityUser.email) == email)):
        raise HTTPException(status_code=409, detail="E-mailen er allerede i brug.")
    admin = CommunityUser(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    return {"status": "ok", "id": admin.id, "email": admin.email, "role": admin.role}