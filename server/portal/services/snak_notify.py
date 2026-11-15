"""Snak: notifikationer og brugerens valg (docs/snak-modul-spec.md §8).

En bruger får aldrig flere beskeder om samme kommentar (prioritet: svar > kommentar på opslag > fulgt tråd). Beskeder om fulgte tråde og
reaktioner samles: højst én pr. tråd/mål pr. time (dedup-nøglen indeholder timen). Fejl her må aldrig vælte selve handlingen."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import features, notify
from portal.services.auth import SUPERADMIN, can
from portal.services.snak_reactions import TYPES

log = logging.getLogger("portal.snak")

#: valg i 🔔-dialogen: nøgle → tekst
PREFS = {
    "svar": "Svar på mine kommentarer",
    "kommentar": "Kommentarer på mine opslag",
    "foelg": "Nye kommentarer i tråde, jeg følger",
    "loesning": "Når min kommentar markeres som løsning",
    "reaktion": "Reaktioner på mine opslag og kommentarer",
    "skjul": "Når en moderator skjuler mit indhold",
}


def _hour() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


async def get_prefs(session: AsyncSession, user_id: int) -> dict:
    stored = (await session.execute(text("SELECT coalesce(prefs->'snak', '{}'::jsonb) FROM users WHERE id = :u"), {"u": user_id})).scalar() or {}
    followed = (await session.execute(text("""
        SELECT p.id, coalesce(p.titel, left(p.body_text, 60)) AS titel FROM sn_follows f JOIN sn_posts p ON p.id = f.post_id
        WHERE f.user_id = :u AND NOT f.muted AND p.status = 'synlig' ORDER BY f.created_at DESC LIMIT 50"""), {"u": user_id})).mappings().all()
    return {"valg": {k: bool(stored.get(k, True)) for k in PREFS}, "tekster": PREFS, "foelger": [dict(r) for r in followed]}


async def set_prefs(session: AsyncSession, user_id: int, valg: dict) -> None:
    for k, v in valg.items():
        if k in PREFS and isinstance(v, bool):
            await session.execute(text("""UPDATE users SET prefs = jsonb_set(coalesce(prefs, '{}'::jsonb), '{snak}',
                                          coalesce(prefs->'snak', '{}'::jsonb) || jsonb_build_object(CAST(:k AS text), CAST(:v AS boolean))) WHERE id = :u"""),
                                  {"k": k, "v": v, "u": user_id})
    await session.commit()


async def _eligible(session: AsyncSession, ids, kind: str, article: bool = False) -> list[int]:
    """Modtagere, der ser Snak-notifikationer (funktionsflag) og ikke har slået typen fra.

    article: kommentarsporet under en artikel – det følger artiklernes notifikationer (flaget «artikler_notifikationer» og valget
    «nye kommentarer på artikler») i stedet for Snaks, så det også virker for dem, der ikke har Snak."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return []
    flags = await features.load(session)
    out = []
    for u in (await session.execute(text("SELECT id FROM users WHERE id = ANY(:i) AND NOT blocked"), {"i": ids})).all():
        user = await session.get(User, u[0])
        if user is None:
            continue
        sa = user.role == SUPERADMIN
        prefs = user.prefs or {}
        if article:
            if not features.visible(flags, "artikler_notifikationer", superadmin=sa, insider=sa or can(user, "artikler")):
                continue
            if kind in ("svar", "foelg") and prefs.get("art_comments", True) is False:
                continue
        elif not features.visible(flags, "snak_notifikationer", superadmin=sa, insider=sa or can(user, "snak")):
            continue
        if prefs.get("snak", {}).get(kind, True) is False and not (article and kind in ("svar", "foelg")):
            continue
        out.append(user.id)
    return out


async def _article(session: AsyncSession, post_id: int | None) -> int | None:
    return (await session.execute(text("SELECT article_id FROM sn_posts WHERE id = :i"), {"i": post_id})).scalar() if post_id else None


def _url(post_id: int, article_id: int | None, comment_id: int | None = None) -> str:
    """Kommentarer under en artikel åbnes på artikelsiden (sk = Snak-kommentaren), ellers på opslaget."""
    if article_id:
        return f"/#/artikler/{article_id}" + (f"?sk={comment_id}" if comment_id else "")
    return f"/#/snak/{post_id}" + (f"?k={comment_id}" if comment_id else "")


async def _send(session: AsyncSession, ids, kind: str, article: bool = False, **kw) -> None:
    try:
        who = await _eligible(session, ids, kind, article)
        if who:
            await notify.notify(session, who, **kw)
    except Exception:  # noqa: BLE001 – en notifikation må aldrig få en kommentar eller reaktion til at fejle
        await session.rollback()
        log.exception("Snak: notifikation kunne ikke sendes")


def _name(user: User) -> str:
    return user.navn or user.obserkode


def _label(titel: str | None, body: str | None = None) -> str:
    return (titel or (body or "")[:60] or "opslag").strip()


async def comment_added(session: AsyncSession, author: User, c, post: dict) -> None:
    """Svar > kommentar på opslag > fulgt tråd. post: {id, user_id, titel}. c: den gemte kommentar."""
    aid = await _article(session, post["id"])
    art = aid is not None
    url = _url(post["id"], aid, c.id)
    done = {author.id}
    reply = set()
    if c.parent_id:
        owner = (await session.execute(text("SELECT user_id FROM sn_comments WHERE id = :i"), {"i": c.parent_id})).scalar()
        reply.add(owner)
    if c.reply_to_user_id:
        reply.add(c.reply_to_user_id)
    reply = {u for u in reply if u and u not in done}
    if reply:
        await _send(session, reply, "svar", art, title=f"{_name(author)} svarede på din kommentar", body=(c.body_text or "")[:200] or "Billede", url=url, dedup=f"snak:svar:{c.id}")
        done |= reply
    if post["user_id"] and post["user_id"] not in done:
        await _send(session, [post["user_id"]], "kommentar", title=f"{_name(author)} kommenterede dit opslag", body=(c.body_text or "")[:200] or "Billede", url=url, dedup=f"snak:kom:{c.id}")
        done.add(post["user_id"])
    followers = {r[0] for r in (await session.execute(text("SELECT user_id FROM sn_follows WHERE post_id = :p AND NOT muted"), {"p": post["id"]})).all()} - done
    if followers:
        await _send(session, followers, "foelg", art, title=f"Nye kommentarer: {_label(post['titel'])}"[:240], body=f"{_name(author)}: {(c.body_text or 'Billede')[:160]}", url=url,
                    dedup=f"snak:foelg:{post['id']}:{_hour()}")


async def reaction_added(session: AsyncSession, user: User, kind: str, target_id: int, post_id: int, owner_id: int | None, rtype: str) -> None:
    """Samlet pr. mål pr. time; ikke ved reaktion på eget indhold."""
    if not owner_id or owner_id == user.id:
        return
    counts = {t: n for t, n in (await session.execute(text("SELECT type, count(*) FROM sn_reactions WHERE kind = :k AND target_id = :t GROUP BY type"),
                                                       {"k": kind, "t": target_id})).all()}
    total = sum(counts.values())
    top = max(counts, key=counts.get) if counts else rtype
    emoji = TYPES.get(top, TYPES[rtype])[0]
    what = "dit opslag" if kind == "post" else "din kommentar"
    who = _name(user) if total <= 1 else f"{_name(user)} og {total - 1} {'anden' if total == 2 else 'andre'}"
    aid = await _article(session, post_id)
    await _send(session, [owner_id], "reaktion", aid is not None, title=f"{who} reagerede på {what} {emoji}", body="",
                url=_url(post_id, aid, target_id if kind == "comment" else None), dedup=f"snak:reak:{kind}:{target_id}:{_hour()}")


async def solution_marked(session: AsyncSession, marker: User, post_id: int, comment_id: int) -> None:
    owner = (await session.execute(text("SELECT user_id FROM sn_comments WHERE id = :i"), {"i": comment_id})).scalar()
    if owner and owner != marker.id:
        await _send(session, [owner], "loesning", title="Din kommentar blev markeret som løsning", body="", url=f"/#/snak/{post_id}?k={comment_id}", dedup=f"snak:loes:{comment_id}")


async def hidden(session: AsyncSession, kind: str, target_id: int, reason: str | None) -> None:
    if kind == "post":
        owner, post_id = (await session.execute(text("SELECT user_id, id FROM sn_posts WHERE id = :i"), {"i": target_id})).first() or (None, None)
    else:
        owner, post_id = (await session.execute(text("SELECT user_id, post_id FROM sn_comments WHERE id = :i"), {"i": target_id})).first() or (None, None)
    if owner:
        aid = await _article(session, post_id)
        await _send(session, [owner], "skjul", aid is not None, title="Dit opslag er skjult" if kind == "post" else "Din kommentar er skjult", body=(reason or "")[:300],
                    url=_url(post_id, aid), dedup=f"snak:skjul:{kind}:{target_id}")
