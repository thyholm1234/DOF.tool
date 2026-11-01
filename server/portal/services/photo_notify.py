"""Notifikationer i fotomodulet (docs/netfugl-afloesning-spec.md §9.4). Kaldes i baggrunden, aldrig under import.

* svar på mine kommentarer på fotos · kommentarer på mine fotos (højst én pr. time pr. foto) – kan slås fra under Fotos → klokken
* «Mine fotos er godkendt/afvist» sendes fra photo_moderation og kan ikke slås fra."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.db import SessionLocal
from portal.services import content_render, features, notify
from portal.timeutil import TZ

PREFS = {"svar": "foto_replies", "kommentarer": "foto_comments"}


async def get_prefs(session: AsyncSession, user_id: int) -> dict:
    row = (await session.execute(text("SELECT prefs FROM users WHERE id = :u"), {"u": user_id})).scalar() or {}
    return {k: bool(row.get(v, True)) for k, v in PREFS.items()}


async def set_prefs(session: AsyncSession, user_id: int, **values: bool | None) -> dict:
    for k, v in values.items():
        if v is not None and k in PREFS:
            await session.execute(text("UPDATE users SET prefs = jsonb_set(coalesce(prefs, '{}'::jsonb), CAST(:p AS text[]), to_jsonb(cast(:v AS boolean))) WHERE id = :u"),
                                  {"p": [PREFS[k]], "v": bool(v), "u": user_id})
    await session.commit()
    return await get_prefs(session, user_id)


async def on_comment(comment_id: int) -> None:
    """Ny kommentar → forfatteren af den kommentar, der svares på, og fotoets ejer (ikke kommentatoren selv, ikke interne noter)."""
    async with SessionLocal() as s:
        flags = await features.load(s)
        if flags.get("fotos") == "off" or flags.get("fotos_kommentarer") == "off":
            return
        c = (await s.execute(text("""
            SELECT c.id, c.photo_id, c.user_id, c.body_md, c.status, c.intern, p.user_id AS owner, p.titel, coalesce(u.navn, u.obserkode) AS who,
                   (SELECT pc.user_id FROM gal_comments pc WHERE pc.id = c.parent_id) AS parent_user,
                   (SELECT b.navn FROM gal_birds b WHERE b.photo_id = p.id ORDER BY b.pos LIMIT 1) AS art
            FROM gal_comments c JOIN gal_photos p ON p.id = c.photo_id LEFT JOIN users u ON u.id = c.user_id WHERE c.id = :i"""), {"i": comment_id})).mappings().first()
        if c is None or c["status"] != "synlig" or c["user_id"] is None or c["intern"]:
            return
        what = c["titel"] or c["art"] or "foto"
        url, body = f"/#/fotos/{c['photo_id']}?k={c['id']}", content_render.teaser(c["body_md"], 240)
        prefs = {}

        async def ok(uid: int | None, key: str) -> bool:
            if uid is None or uid == c["user_id"]:
                return False
            if uid not in prefs:
                prefs[uid] = await get_prefs(s, uid)
            return prefs[uid][key]
        reply_to = c["parent_user"] if await ok(c["parent_user"], "svar") else None
        if reply_to:
            await notify.notify(s, [reply_to], title=f"💬 {c['who']} svarede på din kommentar til «{what}»", body=body, url=url, dedup=f"foto:svar:{c['id']}")
        if c["owner"] != reply_to and await ok(c["owner"], "kommentarer"):
            hour = datetime.now(TZ).strftime("%Y%m%d%H")  # højst én pr. time pr. foto
            await notify.notify(s, [c["owner"]], title=f"💬 Ny kommentar til dit foto «{what}»", body=f"{c['who']}: {body}", url=url,
                                dedup=f"foto:k:{c['photo_id']}:{hour}", tag=f"foto-k-{c['photo_id']}")
