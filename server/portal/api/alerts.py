"""Alarmregler, notifikationsindbakke og Web Push-abonnementer."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import Afdeling, AlertRule, Area, Kommune, Notification, PushSubscription, User
from portal.services.push import ensure_vapid, send_to_user

router = APIRouter(prefix="/api", tags=["alerts"])

LEVEL_ORDER = ("SU", "SUB", "bemaerk", "local")


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    area_id: int | None = None
    kommuner: list[str] = Field(default_factory=list, max_length=98)
    afdelinger: list[int] = Field(default_factory=list, max_length=30)
    level: str = Field("SU,SUB", pattern="^(all|(SU|SUB|bemaerk|local)(,(SU|SUB|bemaerk|local))*)$")
    local_max: int = Field(5, ge=1, le=1000)
    min_antal: int = Field(1, ge=1, le=100000)
    species_include: list[int] = Field(default_factory=list, max_length=200)
    species_exclude: list[int] = Field(default_factory=list, max_length=500)
    push: bool = True
    enabled: bool = True


def rule_out(r: AlertRule, area: Area | None, names: dict[str, str]) -> dict:
    regions = [names.get(f"kom:{k}", k) for k in r.kommuner or []] + [names.get(f"afd:{a}", str(a)) for a in r.afdelinger or []]
    area_name = area.name if area else ", ".join(regions) if regions else "Hele Danmark"
    return {"id": r.id, "name": r.name, "area_id": r.area_id, "kommuner": r.kommuner or [], "afdelinger": r.afdelinger or [],
            "area_name": area_name,
            "level": r.level, "local_max": r.local_max, "min_antal": r.min_antal, "species_include": r.species_include or [],
            "species_exclude": r.species_exclude or [], "push": r.push, "enabled": r.enabled}


async def _region_names(db: AsyncSession) -> dict[str, str]:
    kom = (await db.execute(select(Kommune.kode, Kommune.navn))).all()
    afd = (await db.execute(select(Afdeling.id, Afdeling.navn))).all()
    return {**{f"kom:{k}": n for k, n in kom}, **{f"afd:{i}": n for i, n in afd}}


async def _check_area(db: AsyncSession, user: User, body: RuleIn) -> Area | None:
    if body.level != "all":
        body.level = ",".join(k for k in LEVEL_ORDER if k in body.level.split(","))
    body.kommuner = sorted(set(body.kommuner))
    body.afdelinger = sorted(set(body.afdelinger))
    if body.kommuner or body.afdelinger:
        if body.area_id is not None:
            raise HTTPException(400, "Vælg enten et eget område eller kommuner/lokalafdelinger")
        names = await _region_names(db)
        if any(f"kom:{k}" not in names for k in body.kommuner) or any(f"afd:{a}" not in names for a in body.afdelinger):
            raise HTTPException(400, "Ukendt kommune eller lokalafdeling")
        return None
    if body.area_id is None:
        if ("local" in body.level or body.level == "all") and not body.species_include:
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
    names = await _region_names(db)
    return [rule_out(r, areas.get(r.area_id), names) for r in rules]


@router.post("/alerts/rules")
async def create_rule(body: RuleIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    area = await _check_area(db, user, body)
    if (await db.execute(text("SELECT count(*) FROM alert_rules WHERE user_id = :u"), {"u": user.id})).scalar() >= 40:
        raise HTTPException(400, "Højst 40 regler")
    rule = AlertRule(user_id=user.id, **body.model_dump())
    db.add(rule)
    await db.commit()
    return rule_out(rule, area, await _region_names(db))


@router.put("/alerts/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rule = await db.get(AlertRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(404)
    area = await _check_area(db, user, body)
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    await db.commit()
    return rule_out(rule, area, await _region_names(db))


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


class ReadIn(BaseModel):
    ids: list[int] | None = Field(None, max_length=500)
    url: str | None = Field(None, max_length=300)


@router.post("/notifications/read")
async def mark_read(body: ReadIn | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Uden indhold: alle læst. Med `ids`: de valgte. Med `url`: notifikationer der peger på siden man har åbnet
    (fx tråden) – adressen sammenlignes uden ?-parametre."""
    q = update(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))
    if body and body.ids is not None:
        q = q.where(Notification.id.in_(body.ids))
    elif body and body.url:
        path = body.url.split("?")[0]
        q = q.where(func.split_part(Notification.url, "?", 1) == path)
    elif body and (body.ids is not None or body.url is not None):
        return {"ok": True, "changed": 0, "unread": await _unread(db, user)}
    res = await db.execute(q.values(read=True))
    await db.commit()
    return {"ok": True, "changed": res.rowcount or 0, "unread": await _unread(db, user)}


async def _unread(db: AsyncSession, user: User) -> int:
    return (await db.execute(text("SELECT count(*) FROM notifications WHERE user_id = :u AND NOT read"), {"u": user.id})).scalar() or 0


@router.get("/push/key")
async def push_key(db: AsyncSession = Depends(get_session)):
    return {"key": await ensure_vapid(db)}


class SubIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict[str, str]


# Browserens push-tjenester – serveren POSTer til endpointet, så andre værter afvises (SSRF)
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com",
              "web.push.apple.com", ".push.apple.com")


def _push_host_ok(endpoint: str) -> bool:
    try:
        u = urlsplit(endpoint)
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        return False
    return u.scheme == "https" and port in (None, 443) and any(
        host == h.lstrip(".") or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


@router.post("/push/subscribe")
async def subscribe(body: SubIn, request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not _push_host_ok(body.endpoint) or "p256dh" not in body.keys or "auth" not in body.keys:
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
