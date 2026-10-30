"""Moderation: kommentarer, udelukkelse fra kommentarer og beskeder fra moderatorer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    perm_moderation,
)
from portal.db import get_session
from portal.models import (
    CommentBan,
    SupportMessage,
    SupportThread,
    User,
)
from portal.services import (
    activity,
    moderation,
)
from portal.services.auth import (
    can,
    modules,
)

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/admin/comments")
async def comments(status: str = "alle", q: str = "", user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    """Kommentarer til moderation (moderator og op). status: alle / skjulte / synlige."""
    rows = (await db.execute(text("""
        SELECT c.id, c.dato, c.artnr, c.loknr, c.body, c.hidden, c.created_at, c.user_id, u.obserkode, u.navn, u.role,
               s.navn AS art, l.navn AS lok,
               (SELECT count(*) FROM likes k WHERE k.kind = 'comment' AND k.key = c.id::text) AS likes,
               EXISTS (SELECT 1 FROM comment_bans b WHERE b.user_id = c.user_id AND {ACTIVE}) AS banned
        FROM thread_comments c JOIN users u ON u.id = c.user_id LEFT JOIN species s ON s.artnr = c.artnr
        LEFT JOIN locations l ON l.loknr = c.loknr
        WHERE (:st = 'alle' OR (:st = 'skjulte' AND c.hidden) OR (:st = 'synlige' AND NOT c.hidden))
          AND (:q = '' OR c.body ILIKE :like OR u.navn ILIKE :like OR u.obserkode ILIKE :like OR s.navn ILIKE :like)
        ORDER BY c.created_at DESC LIMIT 300
    """.replace("{ACTIVE}", moderation.ACTIVE)), {"st": status, "q": q, "like": f"%{q}%"})).mappings().all()
    return [{**dict(r), "staff": r["role"] != "user"} for r in rows]


@router.patch("/admin/comments/{cid}")
async def moderate(cid: int, body: dict, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    hidden = bool(body.get("hidden"))
    row = (await db.execute(text("UPDATE thread_comments SET hidden = :h WHERE id = :id RETURNING dato, artnr, loknr"),
                            {"h": hidden, "id": cid})).first()
    if row is None:
        raise HTTPException(404)
    await db.commit()
    await activity.track(user.id, "moderation", None, {"comment": cid, "hidden": hidden})
    await moderation.comments_changed(f"{row[0].isoformat()}/{row[1]}/{row[2]}", "hidden" if hidden else "shown", cid)
    return {"ok": True}


# ---------------------------------------------------------------- udelukkelse fra kommentarer (som DOF.not's blacklist)


@router.get("/admin/bans")
async def bans(user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text(f"""
        SELECT b.user_id, b.reason, b.evidence, b.hide_comments, b.until, b.created_at, u.navn, u.obserkode,
               coalesce(m.navn, m.obserkode) AS by_navn, {moderation.ACTIVE} AS active,
               (SELECT count(*) FROM thread_comments c WHERE c.user_id = b.user_id) AS n_comments
        FROM comment_bans b JOIN users u ON u.id = b.user_id LEFT JOIN users m ON m.id = b.created_by
        ORDER BY {moderation.ACTIVE} DESC, b.created_at DESC
    """))).mappings().all()
    return [dict(r) for r in rows]


class BanIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    days: int | None = Field(None, ge=1, le=3650)  # None = indtil videre
    hide_comments: bool = True
    comment_id: int | None = None
    notify: bool = True


@router.put("/admin/bans/{user_id}")
async def ban(user_id: int, body: BanIn, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Brugeren findes ikke")
    if target.id == user.id:
        raise HTTPException(400, "Du kan ikke udelukke dig selv")
    if modules(target) and not can(user, "brugere"):  # personale udelukkes kun af dem, der styrer brugere
        raise HTTPException(403, "Kun dem med adgang til Brugere kan udelukke andre med admin-adgang")
    evidence = None
    if body.comment_id:
        evidence = (await db.execute(text("SELECT body FROM thread_comments WHERE id = :c AND user_id = :u"),
                                     {"c": body.comment_id, "u": target.id})).scalar()
    b = await db.get(CommentBan, target.id) or CommentBan(user_id=target.id)
    b.reason, b.hide_comments, b.created_by = body.reason.strip(), body.hide_comments, user.id
    b.until = datetime.now(timezone.utc) + timedelta(days=body.days) if body.days else None
    b.evidence = evidence or b.evidence
    b.created_at = datetime.now(timezone.utc)
    db.add(b)
    await db.commit()
    await activity.track(user.id, "ban", None, {"user": target.id, "days": body.days, "hide": body.hide_comments})
    await moderation.comments_changed("*", "ban")
    if body.notify:
        from portal.api.social import _notify
        until = f" til og med {b.until.date().strftime('%d.%m.%Y')}" if b.until else ""
        await _notify([target.id], "Du er udelukket fra at kommentere", f"{body.reason.strip()}{until}. Skriv til os under Feedback og support, hvis du har spørgsmål.",
                      "/#/support", f"ban:{target.id}:{int(b.created_at.timestamp())}")
    return {"ok": True}


@router.delete("/admin/bans/{user_id}")
async def unban(user_id: int, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    b = await db.get(CommentBan, user_id)
    if b is None:
        raise HTTPException(404)
    await db.delete(b)
    await db.commit()
    await activity.track(user.id, "unban", None, {"user": user_id})
    await moderation.comments_changed("*", "unban")
    return {"ok": True}


# ---------------------------------------------------------------- beskeder fra moderatorer (samme modul som support)


class ModMessageIn(BaseModel):
    subject: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=2, max_length=5000)
    comment_id: int | None = None


@router.post("/admin/users/{user_id}/message")
async def message_user(user_id: int, body: ModMessageIn, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    """Start en samtale med en bruger. Den ligger i brugerens 'Feedback og support', og svar ses af alle moderatorer."""
    from portal.api.social import _notify
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Brugeren findes ikke")
    ctx = None
    if body.comment_id:
        c = (await db.execute(text("SELECT body, dato, artnr, loknr FROM thread_comments WHERE id = :c"), {"c": body.comment_id})).first()
        if c:
            ctx = {"comment": c[0][:500], "path": f"#/traad/{c[1].isoformat()}/{c[2]}/{c[3]}"}
    t = SupportThread(user_id=target.id, subject=body.subject.strip(), kind="moderation", unread_user=True, unread_admin=False)
    db.add(t)
    await db.flush()
    db.add(SupportMessage(thread_id=t.id, user_id=user.id, from_admin=True, body=body.body.strip(), context=ctx))
    await db.commit()
    await activity.track(user.id, "mod_message", None, {"user": target.id, "thread": t.id})
    await _notify([target.id], f"Besked fra moderator: {t.subject}", body.body, f"/#/support?t={t.id}", f"support:{t.id}:0")
    return {"thread": t.id}
