"""Arkivets kommentarer til artikler: visning og moderation (docs/artikler-modul-spec.md §8).

Kommentarerne fra netfugl.dk (og portalens fra før Snak) bliver på artiklen, kun til læsning. Nye kommentarer skrives i artiklens
Snak-opslag (services/snak_articles, snak_comments) – det er fælles for artiklen og opslaget."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import ArtComment, User
from portal.services import moderation


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
           "reported": bool(r["reported"]), "html": "" if deleted else r["body_html"]}
    if hidden and not moderator:
        out["html"] = ""
    if hidden:
        out["hidden_reason"] = r["hidden_reason"] if moderator or r["user_id"] == viewer_id else None
    if moderator:
        out["report_count"] = r["report_count"]
    return out


async def list_comments(session: AsyncSession, article_id: int, viewer_id: int, moderator: bool) -> dict:
    """Arkivkommentarer (netfugl.dk, kun til læsning) og portalens egne, hver som et træ med ét niveau svar."""
    rows = (await session.execute(text(f"""
        SELECT c.id, c.parent_id, c.user_id, c.archive_author, c.body_html, c.status, c.hidden_reason, c.report_count, c.created_at, c.edited_at,
               u.navn, u.obserkode, EXISTS (SELECT 1 FROM art_comment_reports r WHERE r.comment_id = c.id AND r.user_id = :viewer) AS reported
        FROM art_comments c LEFT JOIN users u ON u.id = c.user_id
        WHERE c.article_id = :a AND {moderation.VISIBLE} ORDER BY c.created_at, c.id"""), {"a": article_id, "viewer": viewer_id})).mappings().all()
    items = [_row(r, viewer_id, moderator) for r in rows]
    by_parent: dict[int, list[dict]] = {}
    for it in items:
        if it["parent_id"]:
            by_parent.setdefault(it["parent_id"], []).append(it)
    out = {"archive": [], "comments": []}
    for it in items:
        if it["parent_id"]:
            continue
        it["svar"] = [x for x in by_parent.get(it["id"], []) if moderator or x["status"] == "synlig" or x["mine"]]
        gone = it["status"] == "slettet" or (it["status"] == "skjult" and not moderator and not it["mine"])
        if gone and not it["svar"]:
            continue  # slettet/skjult uden svar vises slet ikke
        (out["archive"] if it["archive"] else out["comments"]).append(it)
    return out


async def article_visible(session: AsyncSession, article_id: int, can_edit: bool) -> dict:
    """Artiklen, hvis brugeren må se den (publiceret, eller brugeren er redaktør); ellers CommentError 404."""
    return await _article_open(session, article_id, can_edit)


async def _article_open(session: AsyncSession, article_id: int, can_edit: bool) -> dict:
    a = (await session.execute(text("SELECT id, status, comments_open, title FROM art_articles WHERE id = :i"), {"i": article_id})).mappings().first()
    if a is None or (a["status"] != "publiceret" and not can_edit):
        raise CommentError(404, "Artiklen findes ikke")
    return dict(a)


# ---------------------------------------------------------------- moderation

async def queue(session: AsyncSession, tab: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """tab: anmeldte (åbne anmeldelser) · skjulte · seneste."""
    where = {"anmeldte": "c.report_count > 0 AND c.status <> 'slettet'", "skjulte": "c.status = 'skjult'", "seneste": "c.status <> 'slettet'"}.get(tab)
    if where is None:
        raise CommentError(400, "Ukendt fane")
    rows = (await session.execute(text(f"""
        SELECT c.id, c.article_id, a.title, c.user_id, c.archive_author, c.body_md, c.status, c.hidden_reason, c.report_count, c.created_at,
               u.navn, u.obserkode,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('reason', r.reason, 'note', r.note, 'af', coalesce(ru.navn, ru.obserkode)) ORDER BY r.created_at), '[]'::jsonb)
                FROM art_comment_reports r JOIN users ru ON ru.id = r.user_id WHERE r.comment_id = c.id AND r.resolved_at IS NULL) AS reports
        FROM art_comments c JOIN art_articles a ON a.id = c.article_id LEFT JOIN users u ON u.id = c.user_id WHERE {where}
        ORDER BY (c.report_count > 0 AND c.status <> 'slettet') DESC, c.created_at DESC LIMIT :lim OFFSET :off"""), {"lim": limit, "off": offset})).mappings().all()
    return [{"id": r["id"], "article_id": r["article_id"], "article": r["title"], "user_id": r["user_id"], "archive": r["user_id"] is None,
             "navn": r["archive_author"] if r["user_id"] is None else (r["navn"] or r["obserkode"]), "obserkode": r["obserkode"], "body": r["body_md"],
             "status": r["status"], "hidden_reason": r["hidden_reason"], "report_count": r["report_count"], "created_at": _iso(r["created_at"]),
             "reports": r["reports"]} for r in rows]


async def moderate(session: AsyncSession, moderator: User, cid: int, action: str, reason: str = "") -> ArtComment:
    c = await session.get(ArtComment, cid)
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
    await session.execute(text("""UPDATE art_comment_reports SET resolved_at = :now, resolved_by = :m, resolution = :r WHERE comment_id = :c AND resolved_at IS NULL"""),
                          {"now": now, "m": moderator.id, "r": resolution, "c": cid})
    c.report_count = 0
    await session.commit()
    await moderation.comments_changed(f"art:{c.article_id}", action, c.id)
    return c
