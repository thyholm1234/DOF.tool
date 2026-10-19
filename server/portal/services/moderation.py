"""Moderation: udelukkelse fra kommentarer og live-besked om ændringer i kommentarspor."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import publish

# SQL-betingelse: brugeren (kolonnen) har en gældende udelukkelse
ACTIVE = "(b.until IS NULL OR b.until > now())"
# Kommentarer fra udelukkede brugere med hide_comments ses kun af dem selv
VISIBLE = f"""(c.user_id = :viewer OR NOT EXISTS (
    SELECT 1 FROM comment_bans b WHERE b.user_id = c.user_id AND b.hide_comments AND {ACTIVE}))"""


async def active_ban(session: AsyncSession, user_id: int) -> dict | None:
    row = (await session.execute(text(f"SELECT reason, until FROM comment_bans b WHERE b.user_id = :u AND {ACTIVE}"),
                                 {"u": user_id})).mappings().first()
    return {"reason": row["reason"], "until": row["until"].isoformat() if row["until"] else None} if row else None


async def comments_changed(thread: str, action: str, comment_id: int | None = None) -> None:
    """Fortæl åbne tråde (via SSE), at kommentarsporet er ændret – de henter selv den nye version.
    thread '*' betyder alle tråde (fx når en udelukkelse skjuler/viser en brugers kommentarer)."""
    await publish("feed", {"type": "comment", "thread": thread, "action": action, "id": comment_id})
