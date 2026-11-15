"""Kommentarer på Snak-opslag: forhåndsvisning i feedet, trådside, skrivning, ret, slet og moderation (docs/snak-modul-spec.md §2.3, §7).

Topniveau-kommentarer står nyeste øverst; svar kronologisk under dem. Kun ét niveau: et svar på et svar lægges under samme
topniveau-kommentar med «Svar til Navn»."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.models import SnComment, User
from portal.services import content_render, moderation, snak, snak_reactions

MAX_LEN = 5000
PAGE = 50
SORTS = {"nyeste": "c.created_at DESC, c.id DESC", "aeldste": "c.created_at, c.id", "populaere": "c.n_reactions DESC, c.created_at DESC, c.id DESC"}


class CommentError(snak.SnakError):
    pass


def _author(r) -> dict:
    if r["user_id"] is None:
        return {"navn": r["eksempel_navn"] or "Slettet bruger", "eksempel": True}
    return {"navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"], "user_id": r["user_id"]}


def comment_out(r, viewer_id: int, staff: bool) -> dict:
    gone = r["status"] == "slettet"
    hidden = r["status"] == "skjult"
    mine = r["user_id"] == viewer_id and r["user_id"] is not None
    out = {"id": r["id"], "post_id": r["post_id"], "parent_id": r["parent_id"], "author": _author(r), "mine": mine, "status": r["status"],
           "html": "" if gone or (hidden and not staff and not mine) else r["body_html"], "created_at": snak._iso(r["created_at"]),
           "edited_at": snak._iso(r["edited_at"]), "md": r["body_md"] if mine and not gone else None, "reply_to_navn": r["reply_to_navn"], "n_svar": r.get("n_svar", 0), "n_reactions": r["n_reactions"]}
    if hidden:
        out["hidden_reason"] = r["hidden_reason"] if staff or mine else None
    return out


BASE = """SELECT c.id, c.post_id, c.parent_id, c.user_id, c.eksempel_navn, c.reply_to_navn, c.body_md, c.body_html, c.status, c.hidden_reason, c.n_reactions,
                 c.created_at, c.edited_at, u.navn, u.obserkode
          FROM sn_comments c LEFT JOIN users u ON u.id = c.user_id"""
TOP_BASE = BASE.replace("c.edited_at,", "c.edited_at, (SELECT count(*) FROM sn_comments s WHERE s.parent_id = c.id AND s.status = 'synlig') AS n_svar,", 1)
# Synlig for andre: ikke skjult/slettet og ikke fra udelukket bruger med skjulte kommentarer. Forfatter og moderator ser mere.
SHOW = "(c.status = 'synlig' AND " + moderation.VISIBLE + ")"


async def _attach(session: AsyncSession, rows, viewer_id: int, staff: bool) -> list[dict]:
    items = [comment_out({**dict(r)}, viewer_id, staff) for r in rows]
    media = await snak.media_for(session, "comment", [i["id"] for i in items])
    for i in items:
        i["images"] = media.get(i["id"], []) if i["html"] or i["status"] == "synlig" else []
    react = await snak_reactions.summary(session, "comment", [i["id"] for i in items], viewer_id)
    for i in items:
        i["reactions"] = react[i["id"]]
    return items


async def previews(session: AsyncSession, post_ids: list[int], viewer_id: int, staff: bool, n: int, m: int) -> dict[int, dict]:
    """De n nyeste synlige topniveau-kommentarer pr. opslag, hver med de m nyeste svar (ældste først) – ét kald for alle opslag."""
    out = {pid: {"items": [], "total_top": 0} for pid in post_ids}
    tops = (await session.execute(text(f"""
        SELECT * FROM (SELECT c.id, c.post_id, c.parent_id, c.user_id, c.eksempel_navn, c.reply_to_navn, c.body_md, c.body_html, c.status, c.hidden_reason,
                              c.n_reactions, c.created_at, c.edited_at, u.navn, u.obserkode,
                              row_number() OVER (PARTITION BY c.post_id ORDER BY c.created_at DESC, c.id DESC) AS rn,
                              count(*) OVER (PARTITION BY c.post_id) AS total_top,
                              (SELECT count(*) FROM sn_comments s WHERE s.parent_id = c.id AND s.status = 'synlig') AS n_svar
               FROM sn_comments c LEFT JOIN users u ON u.id = c.user_id
               WHERE c.post_id = ANY(:ids) AND c.parent_id IS NULL AND {SHOW}) t WHERE rn <= :n ORDER BY post_id, rn"""),
        {"ids": post_ids, "viewer": viewer_id, "n": n})).mappings().all()
    if not tops:
        return out
    for r in tops:
        out[r["post_id"]]["total_top"] = r["total_top"]
    replies = (await session.execute(text(f"""
        SELECT * FROM (SELECT c.id, c.post_id, c.parent_id, c.user_id, c.eksempel_navn, c.reply_to_navn, c.body_md, c.body_html, c.status, c.hidden_reason,
                              c.n_reactions, c.created_at, c.edited_at, u.navn, u.obserkode,
                              row_number() OVER (PARTITION BY c.parent_id ORDER BY c.created_at DESC, c.id DESC) AS rn
               FROM sn_comments c LEFT JOIN users u ON u.id = c.user_id
               WHERE c.parent_id = ANY(:tops) AND {SHOW}) t WHERE rn <= :m ORDER BY created_at, id"""),
        {"tops": [r["id"] for r in tops], "viewer": viewer_id, "m": m})).mappings().all() if m > 0 else []
    items = await _attach(session, [*tops, *replies], viewer_id, staff)
    by_id = {i["id"]: i for i in items}
    for i in items:
        i["svar"] = []
    for r in replies:
        by_id[r["id"]]["parent_id"] = r["parent_id"]
        by_id[r["parent_id"]]["svar"].append(by_id[r["id"]])
    for r in tops:
        out[r["post_id"]]["items"].append(by_id[r["id"]])
    return out


async def thread(session: AsyncSession, post_id: int, viewer_id: int, staff: bool, *, sort: str = "nyeste", skip: int = 0, around: int | None = None) -> dict:
    """Trådsidens kommentarer: topniveau i portioner à 50 (``around`` udvider portionen, så kommentaren er med), alle svar kronologisk."""
    order = SORTS.get(sort)
    if order is None:
        raise CommentError(400, "Ukendt sortering")
    cond = f"c.post_id = :p AND c.parent_id IS NULL AND ({SHOW} OR c.user_id = :viewer OR :staff)"
    args = {"p": post_id, "viewer": viewer_id, "staff": staff}
    total = (await session.execute(text(f"SELECT count(*) FROM sn_comments c WHERE {cond}"), args)).scalar() or 0
    limit = PAGE
    if around:
        pos = (await session.execute(text(f"""SELECT rn FROM (SELECT c.id, row_number() OVER (ORDER BY {order}) AS rn FROM sn_comments c WHERE {cond}) t
                                              WHERE id = (SELECT coalesce(parent_id, id) FROM sn_comments WHERE id = :a AND post_id = :p)"""), {**args, "a": around})).scalar()
        if pos and pos > skip + limit:
            limit = pos - skip
    tops = (await session.execute(text(f"""{TOP_BASE}
                                           WHERE {cond} AND (c.status <> 'slettet' OR EXISTS (SELECT 1 FROM sn_comments s WHERE s.parent_id = c.id AND s.status = 'synlig'))
                                           ORDER BY {order} LIMIT :lim OFFSET :off"""), {**args, "lim": limit, "off": skip})).mappings().all()
    replies = (await session.execute(text(f"""{BASE} WHERE c.parent_id = ANY(:tops) AND c.status <> 'slettet' AND ({SHOW} OR c.user_id = :viewer OR :staff)
                                              ORDER BY c.created_at, c.id"""), {"tops": [r["id"] for r in tops], "viewer": viewer_id, "staff": staff})).mappings().all() if tops else []
    items = await _attach(session, [*tops, *replies], viewer_id, staff)
    by_id = {i["id"]: i for i in items}
    top_items = []
    for i in items:
        i["svar"] = []
    for r in replies:
        by_id[r["parent_id"]]["svar"].append(by_id[r["id"]])
    for r in tops:
        top_items.append(by_id[r["id"]])
    return {"items": top_items, "total": total, "more": skip + limit < total, "next_skip": skip + limit}


async def add(session: AsyncSession, user: User, post_id: int, body: str, parent_id: int | None, *, staff: bool, media_id: int | None = None) -> SnComment:
    body = (body or "").strip()
    if not body and not media_id:
        raise CommentError(400, "Skriv en kommentar")
    if len(body) > MAX_LEN:
        raise CommentError(400, f"Kommentaren er for lang (højst {MAX_LEN} tegn)")
    post = (await session.execute(text("SELECT id, status, locked FROM sn_posts WHERE id = :i"), {"i": post_id})).mappings().first()
    if post is None or post["status"] != "synlig":
        raise CommentError(404, "Opslaget findes ikke")
    if post["locked"] and not staff:
        raise CommentError(403, "Tråden er låst af en moderator")
    ban = await moderation.active_ban(session, user.id)
    if ban:
        until = f" til {ban['until'][:10]}" if ban["until"] else ""
        raise CommentError(403, f"Du er udelukket fra at kommentere{until}: {ban['reason']}")
    cfg = await snak.settings(session)
    key = f"snak:comment:rate:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 60)
    if n > cfg["kommentarer_pr_minut"]:
        raise CommentError(429, "Du kommenterer for hurtigt – vent lidt")
    reply_uid = reply_navn = None
    if parent_id:
        p = (await session.execute(text("""SELECT c.id, c.parent_id, c.post_id, c.user_id, c.status, coalesce(u.navn, u.obserkode, c.eksempel_navn) AS navn
                                           FROM sn_comments c LEFT JOIN users u ON u.id = c.user_id WHERE c.id = :i"""), {"i": parent_id})).mappings().first()
        if p is None or p["post_id"] != post_id or p["status"] != "synlig":
            raise CommentError(400, "Kommentaren, du svarer på, findes ikke")
        reply_uid, reply_navn = p["user_id"], p["navn"]
        if p["parent_id"]:  # svar på et svar: hele tråden hænger under topniveau-kommentaren
            parent_id = p["parent_id"]
        else:
            reply_navn = None  # svar direkte på topniveau-kommentaren behøver ikke «Svar til»
    rendered = content_render.render(body, "kommentar")
    c = SnComment(post_id=post_id, parent_id=parent_id, user_id=user.id, body_md=body, body_html=rendered.html, body_text=rendered.text,
                  reply_to_user_id=reply_uid if reply_navn else None, reply_to_navn=reply_navn)
    session.add(c)
    await session.flush()
    if media_id:
        await snak.adopt_media(session, "comment", c.id, user.id, [media_id], limit=1)
    await session.execute(text("UPDATE sn_posts SET n_comments = n_comments + 1, last_activity_at = now() WHERE id = :p"), {"p": post_id})
    await snak.follow(session, user.id, post_id)
    await session.commit()
    await publish("snak", {"type": "snak_comment", "post_id": post_id, "id": c.id, "parent_id": parent_id})
    return c


async def _own(session: AsyncSession, user: User, cid: int) -> SnComment:
    c = await session.get(SnComment, cid)
    if c is None or c.user_id is None or c.status == "slettet":
        raise CommentError(404, "Kommentaren findes ikke")
    if c.user_id != user.id:
        raise CommentError(403, "Du kan kun rette dine egne kommentarer")
    return c


async def edit(session: AsyncSession, user: User, cid: int, body: str) -> SnComment:
    c = await _own(session, user, cid)
    cfg = await snak.settings(session)
    if c.status != "synlig":
        raise CommentError(400, "Kommentaren kan ikke rettes")
    if datetime.now(timezone.utc) - c.created_at > timedelta(minutes=cfg["ret_minutter"]):
        raise CommentError(403, "Kommentaren kan ikke længere rettes")
    body = (body or "").strip()
    if not body or len(body) > MAX_LEN:
        raise CommentError(400, "Ugyldig kommentar")
    rendered = content_render.render(body, "kommentar")
    c.body_md, c.body_html, c.body_text, c.edited_at = body, rendered.html, rendered.text, datetime.now(timezone.utc)
    await session.commit()
    await publish("snak", {"type": "snak_mod", "kind": "comment", "id": c.id, "post_id": c.post_id})
    return c


async def _hide_count(session: AsyncSession, c: SnComment, was_visible: bool, now_visible: bool) -> None:
    """n_comments tæller synlige kommentarer."""
    if was_visible != now_visible:
        await session.execute(text("UPDATE sn_posts SET n_comments = greatest(n_comments + :d, 0) WHERE id = :p"), {"d": 1 if now_visible else -1, "p": c.post_id})


async def remove_own(session: AsyncSession, user: User, cid: int) -> None:
    c = await _own(session, user, cid)
    was = c.status == "synlig"
    c.status = "slettet"
    await _hide_count(session, c, was, False)
    await session.commit()
    await snak.forget_media(session, comment_id=c.id)
    await publish("snak", {"type": "snak_mod", "kind": "comment", "id": c.id, "post_id": c.post_id})


async def moderate(session: AsyncSession, moderator: User, cid: int, action: str, reason: str = "") -> SnComment:
    c = await session.get(SnComment, cid)
    if c is None or c.status == "slettet":
        raise CommentError(404, "Kommentaren findes ikke")
    was = c.status == "synlig"
    if action == "skjul":
        c.status, c.hidden_reason, c.hidden_by, c.hidden_at = "skjult", (reason or "").strip()[:500] or "Skjult af en moderator", moderator.id, datetime.now(timezone.utc)
    elif action == "vis":
        c.status, c.hidden_reason, c.hidden_by, c.hidden_at = "synlig", None, None, None
    else:
        raise CommentError(400, "Ukendt handling")
    await _hide_count(session, c, was, c.status == "synlig")
    await session.commit()
    await snak.forget_media(session, comment_id=c.id)
    await publish("snak", {"type": "snak_mod", "kind": "comment", "id": c.id, "post_id": c.post_id})
    return c
