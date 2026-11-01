"""Redaktion: én samlet oversigt over alt, der venter på en moderator på tværs af modulerne (fotos, kommentarer, kryds).

Hvert modul afgør selv, hvem der må se dets kø (adgangsobjekterne fra photo_access, article_access og kl_access);
her tælles kun. Alle tabeller ligger i brugergruppen, men hver kø tælles i sin egen sætning.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import article_access, kl_access, photo_access


async def _count(session: AsyncSession, sql: str) -> int:
    return int((await session.execute(text(sql))).scalar() or 0)


async def queues(session: AsyncSession, user: User) -> list[dict]:
    """Køerne brugeren har adgang til: [{key, modul, navn, antal, href}] i fast rækkefølge."""
    out: list[dict] = []
    fa = await photo_access.context(session, user)
    if fa.can_moderate and fa.on("fotos"):
        out.append({"key": "fotos", "modul": "Fotos", "navn": "Fotos der afventer godkendelse", "href": "#/fotos/admin/koe",
                    "antal": await _count(session, "SELECT count(*) FROM gal_photos WHERE status = 'afventer'")})
    if fa.can_moderate_comments and fa.on("fotos") and fa.on("fotos_kommentarer"):
        out.append({"key": "fotokommentarer", "modul": "Fotos", "navn": "Anmeldte fotokommentarer", "href": "#/fotos/admin/kommentarer",
                    "antal": await _count(session, "SELECT count(*) FROM gal_comments WHERE intern = false AND report_count > 0 AND status <> 'slettet'")})
    aa = await article_access.context(session, user)
    if aa.can_moderate and aa.on("artikler") and aa.on("artikler_kommentarer"):
        out.append({"key": "artikelkommentarer", "modul": "Artikler", "navn": "Anmeldte artikelkommentarer", "href": "#/moderation?tab=moderation&vis=artikler",
                    "antal": await _count(session, "SELECT count(*) FROM art_comments WHERE report_count > 0 AND status <> 'slettet'")})
    ka = await kl_access.context(session, user)
    if ka.admin and ka.on("krydslister"):
        out.append({"key": "kryds", "modul": "Krydslister", "navn": "Manuelle kryds der afventer godkendelse", "href": "#/lister/admin/godkendelse",
                    "antal": await _count(session, "SELECT count(*) FROM kl_kryds WHERE status = 'afventer' AND NOT hidden")})
    return out
