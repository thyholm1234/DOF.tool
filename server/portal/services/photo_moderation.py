"""Fotomoderation: kø (afventer/afvist/afpubliceret), godkend, afvis med begrundelse, afpublicér og lås (docs/netfugl-afloesning-spec.md §7)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import GalPhoto, User
from portal.services import moderation, notify, photo_settings
from portal.timeutil import TZ

QUEUE_STATUS = ("afventer", "afvist", "afpubliceret")


class ModError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def rare_artnrs(session: AsyncSession, artnrs: list[int]) -> set[int]:
    """Arter, der er sjældne i Danmark (30-grænsen gælder ikke): DK-listens sjældenhed «sjælden»/«SU» når krydslisterne findes, ellers DOFbasens SU-klasse."""
    if not artnrs:
        return set()
    out = {r[0] for r in (await session.execute(text("SELECT artnr FROM species WHERE artnr = ANY(:a) AND klass = 'SU'"), {"a": artnrs})).all()}
    if (await session.execute(text("SELECT to_regclass('kl_species_list_entries')"))).scalar() is not None:
        out |= {r[0] for r in (await session.execute(text("""
            SELECT DISTINCT t.artnr FROM kl_taxa t JOIN kl_species_list_entries e ON e.taxon_id = t.id JOIN kl_species_lists l ON l.id = e.list_id AND l.key = 'dk'
            WHERE t.artnr = ANY(:a) AND e.sjaeldenhed IN ('sjaelden', 'su')"""), {"a": artnrs})).all()}
    return out


async def queue(session: AsyncSession, status: str, limit: int = 30, offset: int = 0) -> dict:
    if status not in QUEUE_STATUS:
        raise ModError(400, "Ukendt fane")
    cfg = await photo_settings.get(session)
    counts = {r[0]: r[1] for r in (await session.execute(text("SELECT status, count(*) FROM gal_photos WHERE status = ANY(:s) GROUP BY 1"), {"s": list(QUEUE_STATUS)})).all()}
    rows = (await session.execute(text("""
        SELECT p.id, p.titel, p.dato, p.lokalitet, p.status, p.afvist_grund, p.uploaded_at, p.locked_by IS NOT NULL AS laast, p.user_id, p.photographer_id,
               ph.navn AS fotograf, u.obserkode, m.thumb_key, m.storage_key, m.medium_key, (SELECT c.navn FROM gal_countries c WHERE c.id = p.land_id) AS land,
               (SELECT coalesce(jsonb_agg(jsonb_build_object('navn', b.navn, 'artnr', b.artnr) ORDER BY b.pos), '[]'::jsonb) FROM gal_birds b WHERE b.photo_id = p.id) AS birds,
               (SELECT count(*) FROM gal_photos x WHERE x.status = 'publiceret' AND EXISTS (SELECT 1 FROM gal_birds xb WHERE xb.photo_id = x.id
                  AND xb.artnr = (SELECT b0.artnr FROM gal_birds b0 WHERE b0.photo_id = p.id AND b0.pos = 0))) AS n_art
        FROM gal_photos p LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id LEFT JOIN users u ON u.id = p.user_id LEFT JOIN content_media m ON m.id = p.media_id
        WHERE p.status = :s ORDER BY p.uploaded_at, p.id LIMIT :lim OFFSET :off"""), {"s": status, "lim": limit, "off": offset})).mappings().all()
    rare = await rare_artnrs(session, [b["artnr"] for r in rows for b in (r["birds"] or [])[:1] if b["artnr"]])
    out = []
    for r in rows:
        main = (r["birds"] or [{}])[0]
        k = lambda key: ("/media/art/" + key) if key else None  # noqa: E731
        out.append({"id": r["id"], "titel": r["titel"], "dato": r["dato"].isoformat() if r["dato"] else None, "lokalitet": r["lokalitet"], "land": r["land"], "status": r["status"],
                    "afvist_grund": r["afvist_grund"], "uploadet": r["uploaded_at"].isoformat(), "laast": r["laast"], "arter": r["birds"], "thumb": k(r["thumb_key"] or r["storage_key"]),
                    "medium": k(r["medium_key"] or r["storage_key"]), "fotograf": {"id": r["photographer_id"], "navn": r["fotograf"], "obserkode": r["obserkode"]},
                    "n_art": r["n_art"], "graense": cfg["max_pr_art"], "over_graense": bool(main.get("artnr")) and r["n_art"] >= cfg["max_pr_art"] and main["artnr"] not in rare,
                    "sjaelden": main.get("artnr") in rare})
    return {"status": status, "total": counts.get(status, 0), "antal": {s: counts.get(s, 0) for s in QUEUE_STATUS}, "rows": out, "afvis_grunde": cfg["afvis_grunde"]}


async def _photo(session: AsyncSession, pid: int) -> GalPhoto:
    p = await session.get(GalPhoto, pid)
    if p is None:
        raise ModError(404, "Fotoet findes ikke")
    return p


async def _art(session: AsyncSession, pid: int) -> str:
    return (await session.execute(text("SELECT navn FROM gal_birds WHERE photo_id = :p ORDER BY pos LIMIT 1"), {"p": pid})).scalar() or "fugl"


async def set_status(session: AsyncSession, mod: User, pid: int, status: str, grund: str | None = None) -> GalPhoto:
    """publiceret (godkend/genpublicér) · afvist (kræver ikke grund, men anbefales) · afpubliceret. Fotografen får besked, hvis fotoet har en bruger."""
    if status not in ("publiceret", "afvist", "afpubliceret", "afventer"):
        raise ModError(400, "Ukendt status")
    p = await _photo(session, pid)
    old = p.status
    grund = (grund or "").strip()[:1000] or None
    now = datetime.now(TZ)
    p.status, p.behandlet_af, p.behandlet_at = status, mod.id, now
    if status == "publiceret":
        p.afvist_grund = None
        p.published_at = p.published_at if old == "afpubliceret" and p.published_at else now
    elif status in ("afvist", "afpubliceret"):
        p.afvist_grund = grund
    else:
        p.afvist_grund, p.behandlet_af, p.behandlet_at = None, None, None
    await session.commit()
    await bump("photos")
    if p.user_id and p.user_id != mod.id and status != old:
        art = await _art(session, pid)
        if status == "publiceret":
            await notify.notify(session, [p.user_id], title=f"Dit foto af {art} er nu i galleriet", body=p.titel or art, url=f"/#/fotos/{pid}", dedup=f"foto:godkendt:{pid}")
        elif status in ("afvist", "afpubliceret"):
            verb = "blev ikke godkendt" if status == "afvist" else "er afpubliceret"
            await moderation.send_message(session, target_id=p.user_id, mod_id=mod.id, subject=f"Dit foto af {art} {verb}",
                                          body=(grund or "Ingen begrundelse angivet.") + "\n\nDu kan rette fotoet under Fotos → Mine fotos.",
                                          link=f"#/fotos/{pid}")
    return p


async def set_lock(session: AsyncSession, mod: User, pid: int, locked: bool) -> GalPhoto:
    p = await _photo(session, pid)
    p.locked_by, p.locked_at = (mod.id, datetime.now(TZ)) if locked else (None, None)
    await session.commit()
    return p
