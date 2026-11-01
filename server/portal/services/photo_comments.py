"""Kommentarer til fotos: visning, skrivning, anmeldelser, moderation og interne noter (docs/netfugl-afloesning-spec.md §7.5, §9.2).

Kopi af article_comments med fotos som ejer. Indstillinger (rettetid, anmeldelsestærskel, til/fra) deles med Artikler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.models import GalComment, User
from portal.services import article_settings, content_render, moderation

MAX_LEN = 5000
REASONS = {"chikane", "spam", "forkert", "andet"}
RATE_PER_MINUTE = 5


class CommentError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _iso(v):
    return v.isoformat() if v else None


def _row(r, viewer_id: int, moderator: bool) -> dict:
    archive = r["user_id"] is None
    deleted = r["status"] == "slettet"
    hidden = r["status"] == "skjult"
    out = {"id": r["id"], "parent_id": r["parent_id"], "archive": archive, "created_at": _iso(r["created_at"]), "edited_at": _iso(r["edited_at"]),
           "status": r["status"], "mine": r["user_id"] == viewer_id and not archive,
           "author": {"navn": r["archive_author"]} if archive else {"navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"], "user_id": r["user_id"]},
           "intern": bool(r["intern"]), "reported": bool(r["reported"]), "html": "" if deleted else r["body_html"]}
    if hidden and not moderator:
        out["html"] = ""
    if hidden:
        out["hidden_reason"] = r["hidden_reason"] if moderator or r["user_id"] == viewer_id else None
    if moderator:
        out["report_count"] = r["report_count"]
    return out


async def list_comments(session: AsyncSession, photo_id: int, viewer_id: int, moderator: bool) -> dict:
    """Arkivkommentarer (netfugl.dk, kun til læsning), portalens egne og – kun for moderatorer – interne noter."""
    rows = (await session.execute(text(f"""
        SELECT c.id, c.parent_id, c.user_id, c.archive_author, c.body_html, c.status, c.hidden_reason, c.report_count, c.created_at, c.edited_at, c.intern,
               u.navn, u.obserkode, EXISTS (SELECT 1 FROM gal_comment_reports r WHERE r.comment_id = c.id AND r.user_id = :viewer) AS reported
        FROM gal_comments c LEFT JOIN users u ON u.id = c.user_id
        WHERE c.photo_id = :a AND (:mod OR NOT c.intern) AND {moderation.VISIBLE} ORDER BY c.created_at, c.id"""), {"a": photo_id, "viewer": viewer_id, "mod": moderator})).mappings().all()
    items = [_row(r, viewer_id, moderator) for r in rows]
    by_parent: dict[int, list[dict]] = {}
    for it in items:
        if it["parent_id"]:
            by_parent.setdefault(it["parent_id"], []).append(it)
    out = {"archive": [], "comments": [], "interne": []}
    for it in items:
        if it["parent_id"]:
            continue
        it["svar"] = [x for x in by_parent.get(it["id"], []) if moderator or x["status"] == "synlig" or x["mine"]]
        gone = it["status"] == "slettet" or (it["status"] == "skjult" and not moderator and not it["mine"])
        if gone and not it["svar"]:
            continue  # slettet/skjult uden svar vises slet ikke
        (out["interne"] if it["intern"] else out["archive"] if it["archive"] else out["comments"]).append(it)
    return out


async def _photo_open(session: AsyncSession, photo_id: int, user: User, moderator: bool) -> dict:
    a = (await session.execute(text("SELECT id, status, user_id, titel FROM gal_photos WHERE id = :i"), {"i": photo_id})).mappings().first()
    if a is None or (a["status"] != "publiceret" and not moderator and a["user_id"] != user.id):
        raise CommentError(404, "Fotoet findes ikke")
    return dict(a)


async def add(session: AsyncSession, user: User, photo_id: int, body: str, parent_id: int | None, *, moderator: bool, intern: bool = False) -> GalComment:
    body = (body or "").strip()
    if not body:
        raise CommentError(400, "Skriv en kommentar")
    if len(body) > MAX_LEN:
        raise CommentError(400, f"Kommentaren er for lang (højst {MAX_LEN} tegn)")
    if intern and not moderator:
        raise CommentError(403, "Kun moderatorer kan skrive interne noter")
    cfg = await article_settings.get(session)
    await _photo_open(session, photo_id, user, moderator)
    if not cfg["comments_enabled"] and not intern:
        raise CommentError(403, "Der kan ikke kommenteres lige nu")
    ban = await moderation.active_ban(session, user.id)
    if ban:
        until = f" til {ban['until'][:10]}" if ban["until"] else ""
        raise CommentError(403, f"Du er udelukket fra at kommentere{until}: {ban['reason']}")
    key = f"photocomment:rate:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 60)
    if n > RATE_PER_MINUTE:
        raise CommentError(429, "Du kommenterer for hurtigt – vent lidt")
    if parent_id:
        p = (await session.execute(text("SELECT id, parent_id, photo_id, user_id, status, intern FROM gal_comments WHERE id = :i"), {"i": parent_id})).mappings().first()
        if p is None or p["photo_id"] != photo_id or p["status"] != "synlig" or p["intern"] != intern:
            raise CommentError(400, "Kommentaren, du svarer på, findes ikke")
        parent_id = p["parent_id"] or p["id"]  # kun ét niveau, som på netfugl.dk
    rendered = content_render.render(body, "kommentar")
    c = GalComment(photo_id=photo_id, parent_id=parent_id, user_id=user.id, body_md=body, body_html=rendered.html, intern=intern)
    session.add(c)
    await session.flush()
    await _recount(session, photo_id)
    await session.commit()
    if not intern:  # interne noter sendes aldrig som live-hændelse
        await moderation.comments_changed(f"foto:{photo_id}", "add", c.id)
    return c


async def _recount(session: AsyncSession, photo_id: int) -> None:
    await session.execute(text("UPDATE gal_photos SET comments = (SELECT count(*) FROM gal_comments WHERE photo_id = :p AND status = 'synlig' AND NOT intern) WHERE id = :p"), {"p": photo_id})


async def _own(session: AsyncSession, user: User, cid: int) -> GalComment:
    c = await session.get(GalComment, cid)
    if c is None or c.user_id is None:
        raise CommentError(404, "Kommentaren findes ikke")
    if c.user_id != user.id:
        raise CommentError(403, "Du kan kun rette dine egne kommentarer")
    return c


async def edit(session: AsyncSession, user: User, cid: int, body: str) -> GalComment:
    c = await _own(session, user, cid)
    cfg = await article_settings.get(session)
    if c.status != "synlig":
        raise CommentError(400, "Kommentaren kan ikke rettes")
    if datetime.now(timezone.utc) - c.created_at > timedelta(minutes=cfg["comment_edit_minutes"]):
        raise CommentError(403, f"Kommentarer kan kun rettes i de første {cfg['comment_edit_minutes']} minutter")
    body = (body or "").strip()
    if not body or len(body) > MAX_LEN:
        raise CommentError(400, "Ugyldig kommentar")
    c.body_md, c.body_html, c.edited_at = body, content_render.render(body, "kommentar").html, datetime.now(timezone.utc)
    await session.commit()
    await moderation.comments_changed(f"foto:{c.photo_id}", "edit", c.id)
    return c


async def remove_own(session: AsyncSession, user: User, cid: int) -> None:
    c = await _own(session, user, cid)
    c.status = "slettet"
    await session.flush()
    await _recount(session, c.photo_id)
    await session.commit()
    await moderation.comments_changed(f"foto:{c.photo_id}", "delete", c.id)


async def report(session: AsyncSession, user: User, cid: int, reason: str, note: str) -> bool:
    """Anmeld en kommentar. Returnerer True, hvis kommentaren blev skjult automatisk (tærsklen er nået)."""
    if reason not in REASONS:
        raise CommentError(400, "Vælg en årsag")
    c = await session.get(GalComment, cid)
    if c is None or c.status == "slettet":
        raise CommentError(404, "Kommentaren findes ikke")
    if c.user_id == user.id:
        raise CommentError(400, "Du kan ikke anmelde din egen kommentar")
    cfg = await article_settings.get(session)
    await session.execute(text("""INSERT INTO gal_comment_reports (comment_id, user_id, reason, note) VALUES (:c, :u, :r, :n)
                                  ON CONFLICT (comment_id, user_id) DO NOTHING"""), {"c": cid, "u": user.id, "r": reason, "n": (note or "")[:500] or None})
    n = (await session.execute(text("SELECT count(*) FROM gal_comment_reports WHERE comment_id = :c AND resolved_at IS NULL"), {"c": cid})).scalar() or 0
    c.report_count = n
    auto = n >= cfg["report_threshold"] and c.status == "synlig"
    if auto:
        c.status, c.hidden_reason = "skjult", "Anmeldt – afventer moderator"
        await session.flush()
        await _recount(session, c.photo_id)
    await session.commit()
    if auto:
        await moderation.comments_changed(f"foto:{c.photo_id}", "hide", c.id)
    return auto


# ---------------------------------------------------------------- moderation

async def queue(session: AsyncSession, tab: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """tab: anmeldte (åbne anmeldelser) · skjulte · seneste."""
    where = {"anmeldte": "c.report_count > 0 AND c.status <> 'slettet'", "skjulte": "c.status = 'skjult'", "seneste": "c.status <> 'slettet'"}.get(tab)
    if where is None:
        raise CommentError(400, "Ukendt fane")
    rows = (await session.execute(text(f"""
        SELECT c.id, c.photo_id, a.titel, c.user_id, c.archive_author, c.body_md, c.status, c.hidden_reason, c.report_count, c.created_at,
               u.navn, u.obserkode,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('reason', r.reason, 'note', r.note, 'af', coalesce(ru.navn, ru.obserkode)) ORDER BY r.created_at), '[]'::jsonb)
                FROM gal_comment_reports r JOIN users ru ON ru.id = r.user_id WHERE r.comment_id = c.id AND r.resolved_at IS NULL) AS reports
        FROM gal_comments c JOIN gal_photos a ON a.id = c.photo_id LEFT JOIN users u ON u.id = c.user_id WHERE c.intern = false AND {where}
        ORDER BY (c.report_count > 0 AND c.status <> 'slettet') DESC, c.created_at DESC LIMIT :lim OFFSET :off"""), {"lim": limit, "off": offset})).mappings().all()
    return [{"id": r["id"], "photo_id": r["photo_id"], "titel": r["titel"], "user_id": r["user_id"], "archive": r["user_id"] is None,
             "navn": r["archive_author"] if r["user_id"] is None else (r["navn"] or r["obserkode"]), "obserkode": r["obserkode"], "body": r["body_md"],
             "status": r["status"], "hidden_reason": r["hidden_reason"], "report_count": r["report_count"], "created_at": _iso(r["created_at"]),
             "reports": r["reports"]} for r in rows]


async def moderate(session: AsyncSession, moderator: User, cid: int, action: str, reason: str = "") -> GalComment:
    c = await session.get(GalComment, cid)
    if c is None:
        raise CommentError(404, "Kommentaren findes ikke")
    now = datetime.now(timezone.utc)
    resolution = None
    if action == "skjul":
        c.status, c.hidden_reason, c.hidden_by = "skjult", reason[:500] or "Skjult af moderator", moderator.id
        resolution = "skjult"
    elif action == "gendan":
        c.status, c.hidden_reason, c.hidden_by = "synlig", None, None
        resolution = "afvist"
    elif action == "slet":
        c.status, c.hidden_reason, c.hidden_by = "slettet", reason[:500] or None, moderator.id
        resolution = "skjult"
    elif action == "afvis":  # anmeldelserne var ubegrundede
        if c.status == "skjult" and c.hidden_by is None:
            c.status, c.hidden_reason = "synlig", None  # var skjult automatisk
        resolution = "afvist"
    else:
        raise CommentError(400, "Ukendt handling")
    await session.execute(text("""UPDATE gal_comment_reports SET resolved_at = :now, resolved_by = :m, resolution = :r WHERE comment_id = :c AND resolved_at IS NULL"""),
                          {"now": now, "m": moderator.id, "r": resolution, "c": cid})
    c.report_count = 0
    await session.flush()
    await _recount(session, c.photo_id)
    await session.commit()
    await moderation.comments_changed(f"foto:{c.photo_id}", action, c.id)
    return c
