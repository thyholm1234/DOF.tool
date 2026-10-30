"""Portalens nyheder («Nyt fra portalen») med den fælles markdown-editor. Tabellen ``news`` er den samme som før; body er nu markdown."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import NewsPost, User
from portal.services import article_edit, content_render

COLS = """n.id, n.title, n.lead, n.body, n.body_html, n.render_version, n.created_at, n.published, n.status, n.publish_at, n.updated_at, n.version,
    n.cover_media_id, u.navn AS author, m.thumb_key, m.medium_key, m.storage_key AS cover_key, m.kind AS cover_kind"""
FROM = "FROM news n LEFT JOIN users u ON u.id = n.author_id LEFT JOIN content_media m ON m.id = n.cover_media_id"


def _legacy_to_markdown(body: str) -> str:
    """Gamle nyheder var ren tekst, hvor et linjeskift var et linjeskift: bevar det som markdown-hårdt linjeskift."""
    lines = body.replace("\r\n", "\n").split("\n")
    return "\n".join(line + "  " if line.strip() and i + 1 < len(lines) and lines[i + 1].strip() else line for i, line in enumerate(lines))


async def _ensure_rendered(session: AsyncSession, rows: list) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        if r["render_version"] < content_render.RENDER_VERSION:
            md = _legacy_to_markdown(r["body"]) if r["render_version"] == 0 else r["body"]
            rendered = await content_render.render_for(session, md, "nyhed")
            await session.execute(text("UPDATE news SET body = :b, body_html = :h, render_version = :v WHERE id = :i"),
                                  {"b": md, "h": rendered.html, "v": content_render.RENDER_VERSION, "i": r["id"]})
            r["body"], r["body_html"] = md, rendered.html
        out.append(r)
    await session.commit()
    return out


def _out(r: dict, full: bool) -> dict:
    cover = None
    if r.get("cover_key"):
        k = r["cover_key"]
        cover = {"thumb": "/media/art/" + (r["thumb_key"] or k), "medium": "/media/art/" + (r["medium_key"] or k)} if r["cover_kind"] == "billede" else None
    plain = content_render.to_text(r["body_html"])
    out = {"id": r["id"], "title": r["title"], "lead": r["lead"], "teaser": r["lead"] or content_render.teaser(plain, 200), "created_at": r["created_at"].isoformat(),
           "author": r["author"], "cover": cover, "body": r["body"], "published": r["published"], "status": r["status"],
           "publish_at": r["publish_at"].isoformat() if r["publish_at"] else None, "version": r["version"]}
    if full:
        out["html"] = r["body_html"]
    return out


async def public_list(session: AsyncSession, limit: int = 10) -> list[dict]:
    rows = (await session.execute(text(f"SELECT {COLS} {FROM} WHERE n.published AND n.status = 'publiceret' ORDER BY n.created_at DESC LIMIT :n"), {"n": limit})).mappings().all()
    return [_out(r, False) for r in await _ensure_rendered(session, rows)]


async def public_get(session: AsyncSession, nid: int) -> dict | None:
    rows = (await session.execute(text(f"SELECT {COLS} {FROM} WHERE n.id = :i AND n.published AND n.status = 'publiceret'"), {"i": nid})).mappings().all()
    return _out((await _ensure_rendered(session, rows))[0], True) if rows else None


async def admin_list(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text(f"SELECT {COLS} {FROM} ORDER BY n.created_at DESC LIMIT 200"))).mappings().all()
    return [_out(r, False) for r in await _ensure_rendered(session, rows)]


async def admin_get(session: AsyncSession, nid: int) -> dict | None:
    rows = (await session.execute(text(f"SELECT {COLS} {FROM} WHERE n.id = :i"), {"i": nid})).mappings().all()
    return _out((await _ensure_rendered(session, rows))[0], True) if rows else None


async def save(session: AsyncSession, user: User, data: dict, nid: int | None = None) -> NewsPost:
    """Opret eller ret en nyhed. data: title, lead, body, cover_media_id, status (kladde/planlagt/publiceret), publish_at, version."""
    if not data["title"].strip():
        raise article_edit.Invalid("Nyheden skal have en titel")
    if data["status"] not in ("kladde", "planlagt", "publiceret"):
        raise article_edit.Invalid("Ukendt status")
    if data["status"] == "planlagt" and (data.get("publish_at") is None or data["publish_at"] <= datetime.now(timezone.utc)):
        raise article_edit.Invalid("En planlagt nyhed skal have et tidspunkt i fremtiden")
    n = await session.get(NewsPost, nid) if nid else None
    if nid and n is None:
        raise article_edit.Invalid("Nyheden findes ikke")
    if n is None:
        n = NewsPost(author_id=user.id)
        session.add(n)
    elif data.get("version") is not None and data["version"] != n.version:
        raise article_edit.Conflict(n.version, None)
    else:
        n.version += 1
    n.title, n.lead, n.body = data["title"].strip()[:200], (data.get("lead") or "").strip() or None, data["body"]
    n.cover_media_id, n.status = data.get("cover_media_id"), data["status"]
    n.publish_at = data.get("publish_at") if data["status"] == "planlagt" else None
    n.published = data["status"] == "publiceret"
    n.updated_at, n.updated_by = datetime.now(timezone.utc), user.id
    rendered = await content_render.render_for(session, n.body, "nyhed")
    n.body_html, n.render_version = rendered.html, content_render.RENDER_VERSION
    await session.flush()
    ids = set(content_render.media_ids(n.body)) | ({n.cover_media_id} if n.cover_media_id else set())
    if ids:
        await session.execute(text("UPDATE content_media SET owner_id = :n WHERE id = ANY(:ids) AND owner_kind = 'nyhed' AND owner_id IS NULL AND created_by IS NOT DISTINCT FROM :u"),
                              {"n": n.id, "ids": list(ids), "u": user.id})
    await session.commit()
    return n


async def delete(session: AsyncSession, nid: int) -> None:
    from portal.models import ContentMedia
    from portal.services import content_media

    for m in (await session.execute(select(ContentMedia).where(ContentMedia.owner_kind == "nyhed", ContentMedia.owner_id == nid))).scalars().all():
        await content_media.delete_media(session, m)
    await session.execute(text("DELETE FROM news WHERE id = :i"), {"i": nid})
    await session.commit()
