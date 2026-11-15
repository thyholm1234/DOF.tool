"""Vedhæftede kort i Snak-opslag: artikel, foto, tråd, art, lokalitet og link (docs/snak-modul-spec.md §5.3).

Ved gem slås målet op og gemmes som et øjebliksbillede i ``sn_posts.attachment``. Ved visning tjekker ``refresh`` kun, at artikler og
fotos stadig er synlige, og at modulet bag kortet er slået til for brugeren. Observationer slås op i deres egen databasegruppe."""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services import dataset, snak_links
from portal.services.content_media import _url

TYPES = ("artikel", "foto", "traad", "art", "lokalitet", "link")
_TRAAD = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d+)/(\d+)$")


class AttachError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _clip(s, n) -> str:
    return (s or "").strip()[:n]


async def _image(session: AsyncSession, media_id: int | None) -> str | None:
    if not media_id:
        return None
    r = (await session.execute(text("SELECT medium_key, thumb_key, storage_key FROM content_media WHERE id = :i"), {"i": media_id})).first()
    return _url(r[0] or r[1] or r[2]) if r else None


async def resolve(session: AsyncSession, user_id: int, typ: str, ref: str, *, flags: dict | None = None) -> dict:
    """Øjebliksbillede af målet: {type, ref, title, teaser, image, url}. Fejl (AttachError), hvis målet ikke findes eller ikke må vedhæftes."""
    ref = str(ref).strip()
    if typ == "artikel" and ref.isdigit():
        r = (await session.execute(text("SELECT id, title, lead, cover_media_id, authors FROM art_articles WHERE id = :i AND status = 'publiceret'"), {"i": int(ref)})).mappings().first()
        if r:
            names = ", ".join(a.get("navn", "") if isinstance(a, dict) else str(a) for a in (r["authors"] or []))
            return {"type": typ, "ref": ref, "title": _clip(r["title"], 200), "teaser": _clip(r["lead"], 300), "meta": _clip(names, 200),
                    "image": await _image(session, r["cover_media_id"]), "url": f"#/artikler/{r['id']}"}
    elif typ == "foto" and ref.isdigit():
        r = (await session.execute(text("""SELECT p.id, p.titel, p.media_id, p.lokalitet, ph.navn AS fotograf,
                                          (SELECT string_agg(b.navn, ', ') FROM gal_birds b WHERE b.photo_id = p.id) AS arter
                                           FROM gal_photos p LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
                                           WHERE p.id = :i AND p.status = 'publiceret'"""), {"i": int(ref)})).mappings().first()
        if r:
            return {"type": typ, "ref": ref, "title": _clip(r["titel"] or r["arter"] or "Foto", 200), "teaser": _clip(r["lokalitet"], 300), "meta": _clip(r["fotograf"], 200),
                    "image": await _image(session, r["media_id"]), "url": f"#/fotos/{r['id']}"}
    elif typ == "art" and ref.isdigit():
        r = (await session.execute(text("SELECT artnr, navn, latin FROM species WHERE artnr = :i"), {"i": int(ref)})).mappings().first()
        if r:
            return {"type": typ, "ref": ref, "title": r["navn"], "teaser": r["latin"] or "", "image": None, "url": f"#/art/{r['artnr']}"}
    elif typ == "lokalitet" and ref.isdigit():
        r = (await session.execute(text("""SELECT l.loknr, l.navn, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
                                           WHERE l.loknr = :i AND l.merged_into IS NULL"""), {"i": int(ref)})).mappings().first()
        if r:
            return {"type": typ, "ref": ref, "title": r["navn"], "teaser": r["kommune"] or "", "image": None, "url": f"#/lok/{r['loknr']}"}
    elif typ == "traad" and (m := _TRAAD.match(ref)):
        try:
            d = date.fromisoformat(m[1])
        except ValueError:
            raise AttachError(400, "Ugyldig tråd") from None
        r = (await dataset.execute(session, """SELECT s.navn AS art, l.navn AS lokalitet, count(*) AS n, sum(o.antal) AS antal FROM observations o
                                               JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
                                               WHERE o.dato = :d AND o.artnr = :a AND o.loknr = :l AND o.hemmelig = 0 GROUP BY s.navn, l.navn""",
                                   {"d": d, "a": int(m[2]), "l": int(m[3])})).mappings().first()
        if r:
            return {"type": typ, "ref": ref, "title": f"{r['art']} · {r['lokalitet'] or ''}".strip(" ·"), "teaser": f"{r['antal'] or 0} ex. · {d.strftime('%d.%m.%Y')}",
                    "image": None, "url": f"#/traad/{ref}"}
    elif typ == "link":
        try:
            return await snak_links.preview(user_id, ref)
        except snak_links.LinkError as e:
            raise AttachError(e.status, e.message) from e
    else:
        raise AttachError(400, "Ukendt vedhæftning")
    raise AttachError(400, "Det valgte findes ikke, eller kan ikke vedhæftes")


async def search(session: AsyncSession, typ: str, q: str, *, limit: int = 12) -> list[dict]:
    """Søgning til vedhæft-vælgeren: [{ref, title, teaser}]."""
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    if typ == "artikel":
        rows = (await session.execute(text("SELECT id::text AS ref, title, coalesce(left(lead, 120), '') AS teaser FROM art_articles WHERE status = 'publiceret' AND title ILIKE :q ORDER BY dato DESC LIMIT :n"), {"q": like, "n": limit})).mappings().all()
    elif typ == "foto":
        rows = (await session.execute(text("""SELECT p.id::text AS ref, coalesce(p.titel, 'Foto') AS title, coalesce(ph.navn, '') AS teaser FROM gal_photos p
                                              LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
                                              WHERE p.status = 'publiceret' AND (p.titel ILIKE :q OR p.lokalitet ILIKE :q OR ph.navn ILIKE :q
                                                    OR EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.navn ILIKE :q))
                                              ORDER BY p.published_at DESC NULLS LAST LIMIT :n"""), {"q": like, "n": limit})).mappings().all()
    elif typ == "art":
        rows = (await session.execute(text("SELECT artnr::text AS ref, navn AS title, coalesce(latin, '') AS teaser FROM species WHERE NOT excluded AND (navn ILIKE :q OR latin ILIKE :q) ORDER BY sortering LIMIT :n"), {"q": like, "n": limit})).mappings().all()
    elif typ == "lokalitet":
        rows = (await session.execute(text("SELECT loknr::text AS ref, navn AS title, '' AS teaser FROM locations WHERE merged_into IS NULL AND navn ILIKE :q ORDER BY navn LIMIT :n"), {"q": like, "n": limit})).mappings().all()
    else:
        raise AttachError(400, "Ukendt type")
    return [dict(r) for r in rows]


async def refresh(session: AsyncSession, posts: list[dict], flags_on) -> None:
    """Markér kort, hvis mål ikke længere er synligt (``unavailable``) eller hvis modulet er slået fra (url fjernes). ``flags_on(key)`` → bool."""
    arts = [int(p["attachment"]["ref"]) for p in posts if p.get("attachment") and p["attachment"].get("type") == "artikel"]
    photos = [int(p["attachment"]["ref"]) for p in posts if p.get("attachment") and p["attachment"].get("type") == "foto"]
    ok_art = {r[0] for r in (await session.execute(text("SELECT id FROM art_articles WHERE id = ANY(:i) AND status = 'publiceret'"), {"i": arts})).all()} if arts else set()
    ok_photo = {r[0] for r in (await session.execute(text("SELECT id FROM gal_photos WHERE id = ANY(:i) AND status = 'publiceret'"), {"i": photos})).all()} if photos else set()
    module = {"artikel": "artikler", "foto": "fotos"}
    for p in posts:
        a = p.get("attachment")
        if not a:
            continue
        typ = a.get("type")
        a = p["attachment"] = dict(a)
        if (typ == "artikel" and int(a["ref"]) not in ok_art) or (typ == "foto" and int(a["ref"]) not in ok_photo):
            p["attachment"] = {"type": typ, "unavailable": True, "title": "Ikke tilgængeligt"}
        elif typ in module and not flags_on(module[typ]):
            a["url"] = None
