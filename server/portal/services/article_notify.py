"""Notifikationer i artikelmodulet (docs/artikler-modul-spec.md §8.3). Kaldes i baggrunden, aldrig under import."""

from __future__ import annotations

from sqlalchemy import text

from portal.db import SessionLocal
from portal.services import content_render, features, notify


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


async def on_hidden(comment_id: int, reason: str) -> None:
    """Moderator har skjult en kommentar og vil give forfatteren besked."""
    async with SessionLocal() as s:
        c = (await s.execute(text("SELECT c.user_id, a.title FROM art_comments c JOIN art_articles a ON a.id = c.article_id WHERE c.id = :i"), {"i": comment_id})).mappings().first()
        if c is None or c["user_id"] is None:
            return
        await notify.notify(s, [c["user_id"]], title=f"Din kommentar til «{c['title']}» er skjult af en moderator", body=reason or "", url="/#/artikler",
                            dedup=f"art:skjult:{comment_id}")
