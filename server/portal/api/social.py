"""Likes (på fugle/tråde og kommentarer), aktivitetssporing og beskeder til admin (feedback/support)."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_perm, require_user
from portal.cache import kv, publish
from portal.db import SessionLocal, get_session
from portal.models import Like, SupportMessage, SupportThread, ThreadComment, User
from portal.services import activity
from portal.services.alerts import deliver
from portal.services.auth import can

router = APIRouter(prefix="/api", tags=["social"])
perm_aktivitet = require_perm("aktivitet")

THREAD_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}/\d+/\d+$")


# ---------------------------------------------------------------- likes


async def like_summary(db: AsyncSession, kind: str, keys: list[str], user: User | None) -> dict[str, dict]:
    """{nøgle: {count, liked, names}} – names er de første tre (til 'Christian, Jens og 3 andre')."""
    if not keys:
        return {}
    rows = (await db.execute(text("""
        SELECT l.key, count(*) AS n, bool_or(l.user_id = :me) AS liked,
               (array_agg(coalesce(u.navn, u.obserkode) ORDER BY l.created_at))[1:3] AS names
        FROM likes l JOIN users u ON u.id = l.user_id WHERE l.kind = :k AND l.key = ANY(:keys) GROUP BY l.key
    """), {"k": kind, "keys": keys, "me": user.id if user else -1})).all()
    return {key: {"count": n, "liked": bool(liked), "names": list(names or [])} for key, n, liked, names in rows}


class LikeIn(BaseModel):
    kind: str = Field(pattern="^(thread|comment)$")
    key: str = Field(max_length=64)


async def _validate_target(db: AsyncSession, kind: str, key: str) -> ThreadComment | None:
    if kind == "thread":
        if not THREAD_KEY.match(key):
            raise HTTPException(400, "Ugyldig tråd")
        return None
    if not key.isdigit():
        raise HTTPException(400, "Ugyldig kommentar")
    c = await db.get(ThreadComment, int(key))
    if c is None or c.hidden:
        raise HTTPException(404, "Kommentaren findes ikke")
    return c


@router.post("/likes")
async def toggle_like(body: LikeIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_session)):
    comment = await _validate_target(db, body.kind, body.key)
    existing = (await db.execute(select(Like).where(Like.user_id == user.id, Like.kind == body.kind,
                                                    Like.key == body.key))).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        liked = False
    else:
        db.add(Like(user_id=user.id, kind=body.kind, key=body.key))
        liked = True
    await db.commit()
    summary = (await like_summary(db, body.kind, [body.key], user)).get(body.key) or {"count": 0, "liked": False, "names": []}
    await publish("feed", {"type": "like", "kind": body.kind, "key": body.key, "count": summary["count"]})
    if liked:
        tasks.add_task(activity.track, user.id, "like", None, {"kind": body.kind, "key": body.key})
        if comment is not None and comment.user_id != user.id:
            tasks.add_task(_notify_comment_like, comment.id, comment.user_id, user.id)
        if body.kind == "thread":
            tasks.add_task(_notify_thread_like, body.key, user.id)
    return {"liked": liked, **summary}


# ---------------------------------------------------------------- notifikationer om likes og kommentarer


async def thread_owners(s: AsyncSession, day, artnr: int, loknr: int) -> set[int]:
    """Portalbrugere der har fuglen: observationer i tråden (DOFbasen) eller egne feltmeldinger."""
    rows = (await s.execute(text("""
        SELECT u.id FROM users u WHERE NOT u.blocked AND u.obserkode IN (
            SELECT obserkode FROM observations WHERE dato = :d AND artnr = :a AND loknr = :l)
        UNION SELECT user_id FROM field_reports WHERE dato = :d AND artnr = :a AND loknr = :l AND NOT hemmelig
    """), {"d": day, "a": artnr, "l": loknr})).all()
    return {r[0] for r in rows}


async def _thread_meta(s: AsyncSession, artnr: int, loknr: int) -> tuple[str, str]:
    row = (await s.execute(text("SELECT (SELECT navn FROM species WHERE artnr = :a), (SELECT navn FROM locations WHERE loknr = :l)"),
                           {"a": artnr, "l": loknr})).first()
    return (row[0] or "fugl", row[1] or "ukendt lokalitet") if row else ("fugl", "ukendt lokalitet")


async def _notify_thread_like(key: str, liker_id: int) -> None:
    d, a, l = key.split("/")
    day, artnr, loknr = date.fromisoformat(d), int(a), int(l)
    async with SessionLocal() as s:
        owners = await thread_owners(s, day, artnr, loknr) - {liker_id}
        who = await s.get(User, liker_id)
        if not owners or who is None:
            return
        art, lok = await _thread_meta(s, artnr, loknr)
        msg = {"title": f"👍 {who.navn or who.obserkode} synes godt om din {art}", "body": f"{lok} · {day:%d.%m.%Y}",
               "url": f"/#/traad/{key}", "tag": f"like-{key}", "kategori": None, "rule_id": None, "push": True}
        await deliver(s, None, {(u, f"like:t:{key}:{liker_id}"): msg for u in owners})


async def _notify_comment_like(comment_id: int, owner_id: int, liker_id: int) -> None:
    async with SessionLocal() as s:
        c = await s.get(ThreadComment, comment_id)
        who = await s.get(User, liker_id)
        if c is None or who is None:
            return
        msg = {"title": f"👍 {who.navn or who.obserkode} synes godt om din kommentar", "body": c.body[:200],
               "url": f"/#/traad/{c.dato}/{c.artnr}/{c.loknr}", "tag": f"like-c{c.id}", "kategori": None,
               "rule_id": None, "push": True}
        await deliver(s, None, {(owner_id, f"like:c{c.id}:{liker_id}"): msg})


async def notify_comment(comment_id: int) -> None:
    """Ny kommentar → fuglens ejere og trådens følgere (ikke kommentatoren selv)."""
    from portal.services.alerts import follower_ids
    async with SessionLocal() as s:
        c = await s.get(ThreadComment, comment_id)
        if c is None:
            return
        who = await s.get(User, c.user_id)
        owners = await thread_owners(s, c.dato, c.artnr, c.loknr)
        followers = set(await follower_ids(s, (c.dato, c.artnr, c.loknr)))
        recipients = (owners | followers) - {c.user_id}
        if not recipients:
            return
        art, lok = await _thread_meta(s, c.artnr, c.loknr)
        name = (who.navn or who.obserkode) if who else "Nogen"
        msg = {"title": f"💬 {name} kommenterede {art}, {lok}", "body": c.body[:300],
               "url": f"/#/traad/{c.dato}/{c.artnr}/{c.loknr}", "tag": f"comment-{c.dato}-{c.artnr}-{c.loknr}",
               "kategori": None, "rule_id": None, "push": True}
        await deliver(s, None, {(u, f"c:{c.id}"): msg for u in recipients})


@router.get("/likes")
async def who_liked(kind: str, key: str, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    if kind not in ("thread", "comment"):
        raise HTTPException(400)
    rows = (await db.execute(text("""
        SELECT coalesce(u.navn, u.obserkode) AS navn, u.obserkode, l.created_at AS at
        FROM likes l JOIN users u ON u.id = l.user_id WHERE l.kind = :k AND l.key = :key ORDER BY l.created_at DESC
    """), {"k": kind, "key": key})).mappings().all()
    return {"count": len(rows), "liked": bool(user and any(r["obserkode"] == user.obserkode for r in rows)),
            "users": [dict(r) for r in rows]}


# ---------------------------------------------------------------- aktivitet (sidevisninger)


class ViewIn(BaseModel):
    path: str = Field(max_length=200)


@router.post("/activity")
async def page_view(body: ViewIn, request: Request, user: User | None = Depends(optional_user)):
    who = f"u{user.id}" if user else (request.client.host if request.client else "?")
    path = body.path.split("?")[0][:200] or "/"
    # Samme side fra samme bruger tælles højst én gang pr. minut
    if await kv().set(f"act:view:{who}:{path}", "1", nx=True, ex=60):
        await activity.track(user.id if user else None, "view", path)
    return {"ok": True}


# ---------------------------------------------------------------- support og feedback

KINDS = {"feedback": "Feedback", "support": "Hjælp/support", "fejl": "Fejl", "ide": "Idé", "moderation": "Moderation"}


class NewThreadIn(BaseModel):
    subject: str = Field(min_length=2, max_length=160)
    kind: str = Field("feedback", pattern="^(feedback|support|fejl|ide)$")
    body: str = Field(min_length=2, max_length=5000)
    context: dict | None = None


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


def _ctx(ctx: dict | None, request: Request) -> dict:
    out = {k: str(v)[:300] for k, v in (ctx or {}).items() if k in ("path", "version", "screen")}
    out["browser"] = (request.headers.get("user-agent") or "")[:300]
    return out


async def _admins(db: AsyncSession, exclude: int | None = None, kind: str | None = None) -> list[int]:
    """Dem der skal have besked – aldrig afsenderen selv. Alle samtaler går til dem med support-modulet,
    moderationssamtaler også til dem med moderation."""
    users = (await db.execute(select(User).where(~User.blocked, or_(User.role != "user", User.permissions.isnot(None))))).scalars().all()
    return [u.id for u in users if u.id != exclude and (can(u, "support") or (kind == "moderation" and can(u, "moderation")))]


def _staff(t: SupportThread, user: User) -> bool:
    """Må svare som 'admin' i samtalen: administratorer altid, moderatorer i moderationssamtaler."""
    return can(user, "support") or (t.kind == "moderation" and can(user, "moderation"))


async def _thread_json(db: AsyncSession, t: SupportThread, viewer: User) -> dict:
    msgs = (await db.execute(text("""
        SELECT m.id, m.body, m.from_admin, m.created_at, m.context, coalesce(u.navn, u.obserkode) AS navn
        FROM support_messages m LEFT JOIN users u ON u.id = m.user_id WHERE m.thread_id = :t ORDER BY m.created_at
    """), {"t": t.id})).mappings().all()
    owner = await db.get(User, t.user_id)
    is_admin = _staff(t, viewer) and viewer.id != t.user_id
    return {"id": t.id, "subject": t.subject, "kind": t.kind, "status": t.status, "created_at": t.created_at,
            "updated_at": t.updated_at, "user": {"navn": owner.navn if owner else None, "obserkode": owner.obserkode if owner else None},
            "messages": [{**dict(m), "context": m["context"] if is_admin or (m["from_admin"] and t.kind == "moderation") else None}
                         for m in msgs]}


async def _notify(user_ids: list[int], title: str, body: str, url: str, dedup: str) -> None:
    async with SessionLocal() as s:
        msg = {"title": title[:240], "body": body[:480], "url": url, "tag": dedup, "kategori": None, "rule_id": None, "push": True}
        await deliver(s, None, {(u, dedup): msg for u in user_ids})


@router.get("/support")
async def my_threads(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT t.id, t.subject, t.kind, t.status, t.unread_user AS unread, t.updated_at,
               (SELECT body FROM support_messages m WHERE m.thread_id = t.id ORDER BY m.created_at DESC LIMIT 1) AS last
        FROM support_threads t WHERE t.user_id = :u ORDER BY t.updated_at DESC
    """), {"u": user.id})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/support/unread")
async def unread(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    mine = (await db.execute(text("SELECT count(*) FROM support_threads WHERE user_id = :u AND unread_user"),
                             {"u": user.id})).scalar()
    out = {"user": mine}
    if can(user, "support") or can(user, "moderation"):
        out["admin"] = (await db.execute(text("SELECT count(*) FROM support_threads WHERE unread_admin AND (:all OR kind = 'moderation')"),
                                         {"all": can(user, "support")})).scalar()
    return out


@router.post("/support")
async def new_thread(body: NewThreadIn, request: Request, tasks: BackgroundTasks, user: User = Depends(require_user),
                     db: AsyncSession = Depends(get_session)):
    if not await kv().set(f"support:rl:{user.id}", "1", nx=True, ex=30):
        raise HTTPException(429, "Vent lidt, før du sender en ny besked")
    t = SupportThread(user_id=user.id, subject=body.subject.strip(), kind=body.kind, unread_admin=True)
    db.add(t)
    await db.flush()
    db.add(SupportMessage(thread_id=t.id, user_id=user.id, from_admin=False, body=body.body.strip(),
                          context=_ctx(body.context, request)))
    await db.commit()
    who = user.navn or user.obserkode
    tasks.add_task(_notify, await _admins(db, user.id), f"{KINDS[body.kind]} fra {who}: {t.subject}", body.body,
                   f"/#/admin?tab=support&t={t.id}", f"support:{t.id}:0")
    tasks.add_task(activity.track, user.id, "support", None, {"thread": t.id, "kind": body.kind})
    return await _thread_json(db, t, user)


async def _visible_thread(db: AsyncSession, thread_id: int, user: User) -> SupportThread:
    t = await db.get(SupportThread, thread_id)
    if t is None or (t.user_id != user.id and not _staff(t, user)):
        raise HTTPException(404, "Samtalen findes ikke")
    return t


@router.get("/support/{thread_id}")
async def get_thread(thread_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    t = await _visible_thread(db, thread_id, user)
    if t.user_id == user.id and t.unread_user:
        t.unread_user = False
    elif _staff(t, user) and t.user_id != user.id and t.unread_admin:
        t.unread_admin = False
    await db.commit()
    return await _thread_json(db, t, user)


@router.post("/support/{thread_id}/messages")
async def reply(thread_id: int, body: MessageIn, request: Request, tasks: BackgroundTasks,
                user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    t = await _visible_thread(db, thread_id, user)
    from_admin = user.id != t.user_id
    m = SupportMessage(thread_id=t.id, user_id=user.id, from_admin=from_admin, body=body.body.strip(),
                       context=None if from_admin else _ctx(None, request))
    db.add(m)
    t.status = "open"
    t.updated_at = datetime.now(timezone.utc)
    if from_admin:
        t.unread_user, t.unread_admin = True, False
    else:
        t.unread_admin = True
    await db.commit()
    who = user.navn or user.obserkode
    if from_admin:
        sender = "moderator" if t.kind == "moderation" else "DOF.tool"
        tasks.add_task(_notify, [t.user_id], f"Svar fra {sender}: {t.subject}", body.body, f"/#/support?t={t.id}",
                       f"support:{t.id}:{m.id}")
    else:
        tab = "moderation&vis=beskeder" if t.kind == "moderation" else "support"
        tasks.add_task(_notify, await _admins(db, user.id, t.kind), f"Nyt svar fra {who}: {t.subject}", body.body,
                       f"/#/admin?tab={tab}&t={t.id}", f"support:{t.id}:{m.id}")
    return await _thread_json(db, t, user)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|closed)$")


@router.patch("/support/{thread_id}")
async def set_status(thread_id: int, body: StatusIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    t = await _visible_thread(db, thread_id, user)
    t.status = body.status
    await db.commit()
    return await _thread_json(db, t, user)


@router.get("/admin/support")
async def admin_threads(status: str = "open", kind: str = "", user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not can(user, "support"):
        if not can(user, "moderation"):
            raise HTTPException(403, "Kræver adgang til Support eller Moderation")
        kind = "moderation"  # med kun moderation ses kun moderationssamtaler
    rows = (await db.execute(text("""
        SELECT t.id, t.subject, t.kind, t.status, t.unread_admin AS unread, t.updated_at, t.created_at,
               coalesce(u.navn, u.obserkode) AS navn, u.obserkode,
               (SELECT count(*) FROM support_messages m WHERE m.thread_id = t.id) AS n
        FROM support_threads t JOIN users u ON u.id = t.user_id
        WHERE (:s = 'all' OR t.status = :s) AND (:k = '' OR t.kind = :k)
        ORDER BY t.unread_admin DESC, t.updated_at DESC LIMIT 200
    """), {"s": status, "k": kind})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/admin/activity")
async def admin_activity(days: int = 30, user: User = Depends(perm_aktivitet), db: AsyncSession = Depends(get_session)):
    return await activity.overview(db, max(1, min(days, 180)))


@router.get("/admin/activity/user/{user_id}")
async def user_activity(user_id: int, user: User = Depends(perm_aktivitet), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT kind, path, detail, created_at FROM activity WHERE user_id = :u ORDER BY created_at DESC LIMIT 300
    """), {"u": user_id})).mappings().all()
    return [dict(r) for r in rows]
