"""Notifikationer i artikelmodulet (docs/artikler-modul-spec.md §8.3). Kaldes i baggrunden, aldrig under import."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from portal.db import SessionLocal
from portal.services import auth, content_render, features, notify
from portal.timeutil import TZ


async def _enabled(s) -> bool:
    flags = await features.load(s)
    return flags.get("artikler") != "off" and flags.get("artikler_notifikationer") != "off"


async def on_publish(article_id: int) -> None:
    """Artikel publiceret første gang → dem der abonnerer på nye artikler."""
    async with SessionLocal() as s:
        if not await _enabled(s):
            return
        a = (await s.execute(text("SELECT title, lead, left(body_text, 400) AS t, status FROM art_articles WHERE id = :i"), {"i": article_id})).mappings().first()
        if a is None or a["status"] != "publiceret":
            return
        users = [r[0] for r in (await s.execute(text("SELECT user_id FROM art_subscriptions WHERE kind = 'alle' AND NOT muted"))).all()]
        await notify.notify(s, users, title=f"📰 Ny artikel: {a['title']}", body=a["lead"] or content_render.teaser(a["t"] or "", 200),
                            url=f"/#/artikler/{article_id}", dedup=f"art:ny:{article_id}")


async def on_comment(comment_id: int) -> None:
    """Ny kommentar → abonnenter på artiklen (højst én pr. time pr. artikel) og forfatteren af den kommentar, der svares på."""
    async with SessionLocal() as s:
        if not await _enabled(s):
            return
        c = (await s.execute(text("""
            SELECT c.id, c.article_id, c.parent_id, c.user_id, c.body_md, c.status, a.title, coalesce(u.navn, u.obserkode) AS who,
                   (SELECT p.user_id FROM art_comments p WHERE p.id = c.parent_id) AS parent_user
            FROM art_comments c JOIN art_articles a ON a.id = c.article_id LEFT JOIN users u ON u.id = c.user_id WHERE c.id = :i"""), {"i": comment_id})).mappings().first()
        if c is None or c["status"] != "synlig" or c["user_id"] is None:
            return
        url = f"/#/artikler/{c['article_id']}?k={c['id']}"
        hour = datetime.now(TZ).strftime("%Y%m%d%H")
        body = content_render.teaser(c["body_md"], 240)
        reply_to = c["parent_user"] if c["parent_user"] and c["parent_user"] != c["user_id"] else None
        if reply_to:
            await notify.notify(s, [reply_to], title=f"💬 {c['who']} svarede på din kommentar til «{c['title']}»", body=body, url=url, dedup=f"art:svar:{c['id']}")
        subs = {r[0] for r in (await s.execute(text("""
            SELECT x.user_id FROM art_subscriptions x JOIN users u ON u.id = x.user_id
            WHERE x.kind = 'artikel' AND x.ref = :a AND NOT x.muted AND coalesce((u.prefs->>'art_comments')::boolean, true)"""), {"a": c["article_id"]})).all()}
        subs -= {c["user_id"], reply_to}
        await notify.notify(s, subs, title=f"💬 Ny kommentar til «{c['title']}»", body=f"{c['who']}: {body}", url=url,
                            dedup=f"art:k:{c['article_id']}:{hour}", tag=f"art-k-{c['article_id']}")


async def on_auto_hidden(comment_id: int) -> None:
    """En kommentar er skjult automatisk efter anmeldelser → moderatorerne."""
    async with SessionLocal() as s:
        c = (await s.execute(text("SELECT c.article_id, c.body_md, a.title FROM art_comments c JOIN art_articles a ON a.id = c.article_id WHERE c.id = :i"),
                             {"i": comment_id})).mappings().first()
        if c is None:
            return
        staff = await auth.staff_ids(s, "moderation")
        await notify.notify(s, staff, title=f"🚩 Anmeldt kommentar skjult: «{c['title']}»", body=content_render.teaser(c["body_md"], 200),
                            url="/#/admin/moderation", dedup=f"art:anm:{comment_id}")


async def on_hidden(comment_id: int, reason: str) -> None:
    """Moderator har skjult en kommentar og vil give forfatteren besked."""
    async with SessionLocal() as s:
        c = (await s.execute(text("SELECT c.user_id, a.title FROM art_comments c JOIN art_articles a ON a.id = c.article_id WHERE c.id = :i"), {"i": comment_id})).mappings().first()
        if c is None or c["user_id"] is None:
            return
        await notify.notify(s, [c["user_id"]], title=f"Din kommentar til «{c['title']}» er skjult af en moderator", body=reason or "", url="/#/artikler",
                            dedup=f"art:skjult:{comment_id}")
