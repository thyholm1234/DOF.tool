"""Moderation: udelukkelse fra kommentarer og live-besked om ændringer i kommentarspor."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import publish
from portal.models import SupportMessage, SupportThread
from portal.services import notify

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


async def send_message(session: AsyncSession, *, target_id: int, mod_id: int, subject: str, body: str, context: dict | None = None,
                       link: str | None = None, commit: bool = True) -> SupportThread:
    """Moderatorbesked til en bruger: en samtale (SupportThread, kind «moderation») i brugerens «Feedback og support» plus en notifikation.
    Bruges af Admin → Brugere → Besked og af fotomoderationen (afvisning/afpublicering). link: valgfri adresse i appen, som vises i beskeden."""
    t = SupportThread(user_id=target_id, subject=subject.strip()[:200], kind="moderation", unread_user=True, unread_admin=False)
    session.add(t)
    await session.flush()
    ctx = dict(context or {})
    if link:
        ctx["path"] = link
    session.add(SupportMessage(thread_id=t.id, user_id=mod_id, from_admin=True, body=body.strip(), context=ctx or None))
    if commit:
        await session.commit()
    await notify.notify(session, [target_id], title=f"Besked fra moderator: {t.subject}", body=body, url=f"/#/support?t={t.id}", dedup=f"support:{t.id}:0")
    return t
