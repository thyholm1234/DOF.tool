"""Alarmregler, notifikationsindbakke og Web Push-abonnementer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import AlertRule, Area, Notification, PushSubscription, User
from portal.services.push import ensure_vapid, send_to_user

router = APIRouter(prefix="/api", tags=["alerts"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    area_id: int | None = None
    level: str = Field("SUB", pattern="^(SU|SUB|bemaerk|local|all)$")
    local_max: int = Field(5, ge=1, le=1000)
    min_antal: int = Field(1, ge=1, le=100000)
    species_include: list[int] = Field(default_factory=list, max_length=200)
    species_exclude: list[int] = Field(default_factory=list, max_length=500)
    push: bool = True
    enabled: bool = True


def rule_out(r: AlertRule, area: Area | None) -> dict:
    return {"id": r.id, "name": r.name, "area_id": r.area_id, "area_name": area.name if area else "Hele Danmark",
            "level": r.level, "local_max": r.local_max, "min_antal": r.min_antal, "species_include": r.species_include or [],
            "species_exclude": r.species_exclude or [], "push": r.push, "enabled": r.enabled}


async def _check_area(db: AsyncSession, user: User, body: RuleIn) -> Area | None:
    if body.area_id is None:
        if body.level in ("local", "all") and not body.species_include:
            raise HTTPException(400, "Lokale hits og 'alle' kræver et område (eller en artsliste)")
        return None
    area = await db.get(Area, body.area_id)
    if area is None or area.user_id != user.id:
        raise HTTPException(404, "Område findes ikke")
    return area


@router.get("/alerts/rules")
async def list_rules(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rules = (await db.execute(select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id))).scalars().all()
    areas = {a.id: a for a in (await db.execute(select(Area).where(Area.user_id == user.id))).scalars()}
    return [rule_out(r, areas.get(r.area_id)) for r in rules]


@router.post("/alerts/rules")
async def create_rule(body: RuleIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    area = await _check_area(db, user, body)
    if (await db.execute(text("SELECT count(*) FROM alert_rules WHERE user_id = :u"), {"u": user.id})).scalar() >= 40:
        raise HTTPException(400, "Højst 40 regler")
    rule = AlertRule(user_id=user.id, **body.model_dump())
    db.add(rule)
    await db.commit()
    return rule_out(rule, area)


@router.put("/alerts/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rule = await db.get(AlertRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404)
    area = await _check_area(db, user, body)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    await db.commit()
    return rule_out(rule, area)


@router.delete("/alerts/rules/{rule_id}")
async def delete_rule(rule_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.id))
    await db.commit()
    return {"ok": True}


@router.get("/notifications")
async def notifications(limit: int = 50, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Notification).where(Notification.user_id == user.id)
                             .order_by(Notification.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    unread = (await db.execute(text("SELECT count(*) FROM notifications WHERE user_id = :u AND NOT read"), {"u": user.id})).scalar()
    return {"unread": unread, "items": [
        {"id": n.id, "title": n.title, "body": n.body, "url": n.url, "kategori": n.kategori, "read": n.read,
         "pushed": n.pushed, "created_at": n.created_at.isoformat()} for n in rows]}


@router.post("/notifications/read")
async def mark_read(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)).values(read=True))
    await db.commit()
    return {"ok": True}


@router.get("/push/key")
async def push_key(db: AsyncSession = Depends(get_session)):
    return {"key": await ensure_vapid(db)}


class SubIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict[str, str]


@router.post("/push/subscribe")
async def subscribe(body: SubIn, request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not body.endpoint.startswith("https://") or "p256dh" not in body.keys or "auth" not in body.keys:
        raise HTTPException(400, "Ugyldigt abonnement")
    existing = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))).scalar_one_or_none()
    if existing:
        existing.user_id = user.id
        existing.p256dh = body.keys["p256dh"]
        existing.auth = body.keys["auth"]
    else:
        db.add(PushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys["p256dh"], auth=body.keys["auth"],
                                user_agent=(request.headers.get("user-agent") or "")[:300]))
    await db.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
async def unsubscribe(body: dict, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id,
                                                    PushSubscription.endpoint == str(body.get("endpoint", ""))))
    await db.commit()
    return {"ok": True}


@router.get("/push/devices")
async def devices(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    subs = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))).scalars().all()
    return [{"id": s.id, "user_agent": s.user_agent, "created_at": s.created_at, "last_ok": s.last_ok,
             "endpoint_host": s.endpoint.split("/")[2]} for s in subs]


@router.delete("/push/devices/{sub_id}")
async def delete_device(sub_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(PushSubscription).where(PushSubscription.id == sub_id, PushSubscription.user_id == user.id))
    await db.commit()
    return {"ok": True}


@router.post("/push/test")
async def push_test(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    n = await send_to_user(db, user.id, {"title": "Testnotifikation", "body": "Push virker på denne enhed 🎉",
                                         "url": "/#/alarmer", "tag": "test"})
    await db.commit()
    return {"delivered": n}
