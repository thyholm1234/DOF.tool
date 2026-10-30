"""Fælles notifikationer: gem i indbakken (dedupliceret), send live (SSE) og som push med respekt for stille timer.

Et tyndt lag oven på ``alerts.deliver``, så moduler uden alarmregler (artikler m.fl.) ikke selv skal bygge beskeden og slå
brugernes indstillinger op."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services.alerts import deliver


async def notify(session: AsyncSession, user_ids, *, title: str, body: str, url: str, dedup: str, push: bool = True, tag: str | None = None) -> int:
    """Send samme besked til flere brugere. dedup er unik pr. bruger (samme nøgle sendes aldrig to gange). Returnerer antal push."""
    ids = sorted({int(u) for u in user_ids})
    if not ids:
        return 0
    rows = (await session.execute(text("SELECT id, obserkode, prefs FROM users WHERE id = ANY(:ids) AND NOT blocked"), {"ids": ids})).all()
    users = {r[0]: {"obserkode": r[1], "push_enabled": (r[2] or {}).get("push_enabled", True), "quiet": (r[2] or {}).get("quiet_hours")} for r in rows}
    msg = {"title": title[:240], "body": body[:480], "url": url, "tag": tag or dedup, "kategori": None, "rule_id": None, "push": push}
    return await deliver(session, users, {(u, dedup): msg for u in users})
