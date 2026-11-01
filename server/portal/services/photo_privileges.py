"""Uploadprivilegier (docs/netfugl-afloesning-spec.md §7.1): standard · månedlig · ubegrænset · udelukket. Admins og fotomoderatorer er altid ubegrænsede."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import GalPrivilege, User

LEVELS = ("standard", "maanedlig", "ubegraenset", "udelukket")
TEXTS = {"standard": "Standard – fotos godkendes af en moderator", "maanedlig": "Månedlig – de første fotos hver måned publiceres uden godkendelse",
         "ubegraenset": "Ubegrænset – fotos publiceres uden godkendelse", "udelukket": "Udelukket – kan ikke uploade fotos"}


async def level(session: AsyncSession, user_id: int) -> str:
    return (await session.execute(select(GalPrivilege.niveau).where(GalPrivilege.user_id == user_id))).scalar_one_or_none() or "standard"


async def set_level(session: AsyncSession, admin: User, user_id: int, niveau: str, note: str | None) -> None:
    if niveau not in LEVELS:
        raise ValueError("Ukendt niveau")
    if (await session.get(User, user_id)) is None:
        raise LookupError("Brugeren findes ikke")
    row = await session.get(GalPrivilege, user_id)
    if niveau == "standard" and not note:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(GalPrivilege(user_id=user_id, niveau=niveau, note=(note or None) and note[:300], set_by=admin.id))
    else:
        row.niveau, row.note, row.set_by = niveau, (note or None) and note[:300], admin.id
        await session.execute(text("UPDATE gal_privileges SET set_at = now() WHERE user_id = :u"), {"u": user_id})
    await session.commit()


async def clear(session: AsyncSession, user_id: int) -> None:
    row = await session.get(GalPrivilege, user_id)
    if row is not None:
        await session.delete(row)
        await session.commit()


async def listing(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT p.user_id, p.niveau, p.note, p.set_at, u.navn, u.obserkode, a.navn AS af FROM gal_privileges p JOIN users u ON u.id = p.user_id
        LEFT JOIN users a ON a.id = p.set_by ORDER BY p.set_at DESC"""))).mappings().all()
    return [{"user_id": r["user_id"], "navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"], "niveau": r["niveau"], "note": r["note"],
             "set_at": r["set_at"].isoformat(), "af": r["af"]} for r in rows]
