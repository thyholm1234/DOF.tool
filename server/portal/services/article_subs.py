"""Brugerens notifikationsindstillinger i artikelmodulet og «følg artikel»."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_prefs(session: AsyncSession, user_id: int) -> dict:
    alle = (await session.execute(text("SELECT NOT muted FROM art_subscriptions WHERE user_id = :u AND kind = 'alle' AND ref = 0"), {"u": user_id})).scalar()
    kommentarer = (await session.execute(text("SELECT coalesce((prefs->>'art_comments')::boolean, true) FROM users WHERE id = :u"), {"u": user_id})).scalar()
    followed = (await session.execute(text("""
        SELECT a.id, a.title FROM art_subscriptions s JOIN art_articles a ON a.id = s.ref WHERE s.user_id = :u AND s.kind = 'artikel' AND NOT s.muted
        ORDER BY s.created_at DESC LIMIT 50"""), {"u": user_id})).mappings().all()
    return {"alle": bool(alle), "kommentarer": bool(kommentarer if kommentarer is not None else True), "foelger": [dict(r) for r in followed]}


async def set_prefs(session: AsyncSession, user_id: int, alle: bool | None, kommentarer: bool | None) -> None:
    if alle is not None:
        await session.execute(text("""INSERT INTO art_subscriptions (user_id, kind, ref, via, muted) VALUES (:u, 'alle', 0, 'manuel', :m)
                                      ON CONFLICT (user_id, kind, ref) DO UPDATE SET muted = :m"""), {"u": user_id, "m": not alle})
    if kommentarer is not None:
        await session.execute(text("UPDATE users SET prefs = jsonb_set(coalesce(prefs, '{}'::jsonb), '{art_comments}', to_jsonb(cast(:v AS boolean))) WHERE id = :u"),
                              {"u": user_id, "v": kommentarer})
    await session.commit()


async def follow(session: AsyncSession, user_id: int, article_id: int, on: bool) -> None:
    """Følg (on) eller følg ikke (off → muted, så en ny kommentar ikke tilmelder igen)."""
    await session.execute(text("""INSERT INTO art_subscriptions (user_id, kind, ref, via, muted) VALUES (:u, 'artikel', :a, 'manuel', :m)
                                  ON CONFLICT (user_id, kind, ref) DO UPDATE SET muted = :m"""), {"u": user_id, "a": article_id, "m": not on})
    await session.commit()
