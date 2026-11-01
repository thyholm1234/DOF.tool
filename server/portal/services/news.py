"""Portalens nyheder («Nyt fra portalen») er en artikeltype: rækker i art_articles med kind = 'nyhed'.

De redigeres under Redaktion → Nyheder (modulet «indhold») med den fælles markdown-editor og vises på forsiden og i Artikler-modulet
sammen med de øvrige artikler. Den gamle tabel ``news`` flyttes over én gang (``migrate``); art_articles.news_id husker nyhedens gamle
nummer, så gamle links (#/nyheder/<id>) virker. Medier har stadig owner_kind 'nyhed' (redigeres med «indhold»), men owner_id er artiklens id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, kv
from portal.models import ArtArticle, User
from portal.services import article_authors, article_edit, content_render

COLS = """a.id, a.title, a.lead, a.body_md AS body, a.body_html, a.render_version, coalesce(a.published_at, a.dato) AS created_at, a.status, a.publish_at,
    a.updated_at, a.version, a.cover_media_id, (SELECT u.navn FROM users u WHERE u.id = a.created_by) AS author,
    m.thumb_key, m.medium_key, m.storage_key AS cover_key, m.kind AS cover_kind"""
FROM = "FROM art_articles a LEFT JOIN content_media m ON m.id = a.cover_media_id"
MIGRATED = "news:migrated:v1"


def _legacy_to_markdown(body: str) -> str:
    """Gamle nyheder var ren tekst, hvor et linjeskift var et linjeskift: bevar det som markdown-hårdt linjeskift."""
    lines = body.replace("\r\n", "\n").split("\n")
    return "\n".join(line + "  " if line.strip() and i + 1 < len(lines) and lines[i + 1].strip() else line for i, line in enumerate(lines))


# ---------------------------------------------------------------- flytning fra tabellen news

async def migrate(session: AsyncSession) -> int:
    """Flyt nyheder fra den gamle tabel news til art_articles (kind = 'nyhed'). Kan køres igen; returnerer antal flyttede."""
    if await kv().get(MIGRATED):
        return 0
    if (await session.execute(text("SELECT to_regclass('news')"))).scalar() is None:
        await kv().set(MIGRATED, "1")
        return 0
    await session.execute(text("SELECT pg_advisory_xact_lock(470110)"))  # to processer må ikke flytte samtidig
    rows = (await session.execute(text("""
        SELECT n.* FROM news n WHERE NOT EXISTS (SELECT 1 FROM art_articles a WHERE a.news_id = n.id) ORDER BY n.id"""))).mappings().all()
    # Mediernes ejere slås op før flytningen: owner_id skifter fra nyhedens til artiklens nummer, og de to nummerserier kan overlappe
    media: dict[int, list[int]] = {}
    for mid, owner in (await session.execute(text("SELECT id, owner_id FROM content_media WHERE owner_kind = 'nyhed' AND owner_id IS NOT NULL"))).all():
        media.setdefault(owner, []).append(mid)
    moved = (await session.execute(text("SELECT news_id FROM art_articles WHERE news_id IS NOT NULL"))).scalars().all()
    for nid in moved:
        media.pop(nid, None)  # allerede flyttet
    for r in rows:
        md = _legacy_to_markdown(r["body"]) if r["render_version"] == 0 else r["body"]
        rendered = await content_render.render_for(session, md, "nyhed")
        cover = r["cover_media_id"] if r["cover_media_id"] and (await session.execute(text("SELECT 1 FROM content_media WHERE id = :i"), {"i": r["cover_media_id"]})).scalar() else None
        art = ArtArticle(kind="nyhed", news_id=r["id"], title=r["title"][:300], lead=r["lead"], body_md=md, body_html=rendered.html,
                         body_text=content_render.to_text(rendered.html), render_version=content_render.RENDER_VERSION, dato=r["created_at"],
                         published_at=r["created_at"] if r["status"] == "publiceret" and r["published"] else None,
                         status=r["status"] if r["published"] or r["status"] != "publiceret" else "kladde", publish_at=r["publish_at"],
                         cover_media_id=cover, source="lokal", comments_open=False, locally_edited=True, version=r["version"] or 1,
                         created_by=r["author_id"], updated_by=r["updated_by"], created_at=r["created_at"], updated_at=r["updated_at"] or r["created_at"])
        session.add(art)
        await session.flush()
        if ids := media.get(r["id"]):
            await session.execute(text("UPDATE content_media SET owner_id = :a WHERE id = ANY(:ids)"), {"a": art.id, "ids": ids})
        navn = (await session.execute(text("SELECT navn FROM users WHERE id = :u"), {"u": r["author_id"]})).scalar() if r["author_id"] else None
        if navn:
            art.authors = await article_authors.set_for_article(session, art.id, [{"navn": navn, "user_id": r["author_id"]}])
    await session.commit()
    await kv().set(MIGRATED, "1")
    if rows:
        await bump("articles")
    return len(rows)


# ---------------------------------------------------------------- læsning

async def _ensure_rendered(session: AsyncSession, rows: list) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        if r["render_version"] < content_render.RENDER_VERSION:
            rendered = await content_render.render_for(session, r["body"], "nyhed")
            await session.execute(text("UPDATE art_articles SET body_html = :h, body_text = :t, render_version = :v WHERE id = :i"),
                                  {"h": rendered.html, "t": content_render.to_text(rendered.html), "v": content_render.RENDER_VERSION, "i": r["id"]})
            r["body_html"] = rendered.html
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
           "author": r["author"], "cover": cover, "body": r["body"], "published": r["status"] == "publiceret", "status": r["status"],
           "publish_at": r["publish_at"].isoformat() if r["publish_at"] else None, "version": r["version"]}
    if full:
        out["html"] = r["body_html"]
    return out


async def _rows(session: AsyncSession, where: str, params: dict, limit: int) -> list[dict]:
    await migrate(session)
    rows = (await session.execute(text(f"SELECT {COLS} {FROM} WHERE a.kind = 'nyhed' AND {where} ORDER BY coalesce(a.published_at, a.dato) DESC, a.id DESC LIMIT :n"),
                                  {**params, "n": limit})).mappings().all()
    return await _ensure_rendered(session, rows)


async def public_list(session: AsyncSession, limit: int = 10) -> list[dict]:
    return [_out(r, False) for r in await _rows(session, "a.status = 'publiceret'", {}, limit)]


async def public_get(session: AsyncSession, nid: int) -> dict | None:
    """nid er artiklens id – eller nyhedens gamle nummer fra tabellen news (gamle links)."""
    rows = await _rows(session, "a.status = 'publiceret' AND a.id = :i", {"i": nid}, 1) \
        or await _rows(session, "a.status = 'publiceret' AND a.news_id = :i", {"i": nid}, 1)
    return _out(rows[0], True) if rows else None


async def admin_list(session: AsyncSession) -> list[dict]:
    return [_out(r, False) for r in await _rows(session, "TRUE", {}, 200)]


async def admin_get(session: AsyncSession, nid: int) -> dict | None:
    rows = await _rows(session, "a.id = :i", {"i": nid}, 1)
    return _out(rows[0], True) if rows else None


# ---------------------------------------------------------------- redigering

async def _get(session: AsyncSession, nid: int) -> ArtArticle | None:
    art = await session.get(ArtArticle, nid)
    return art if art is not None and art.kind == "nyhed" else None


async def save(session: AsyncSession, user: User, data: dict, nid: int | None = None) -> ArtArticle:
    """Opret eller ret en nyhed. data: title, lead, body, cover_media_id, status (kladde/planlagt/publiceret), publish_at, version."""
    if not data["title"].strip():
        raise article_edit.Invalid("Nyheden skal have en titel")
    if data["status"] not in ("kladde", "planlagt", "publiceret"):
        raise article_edit.Invalid("Ukendt status")
    now = datetime.now(timezone.utc)
    if data["status"] == "planlagt" and (data.get("publish_at") is None or data["publish_at"] <= now):
        raise article_edit.Invalid("En planlagt nyhed skal have et tidspunkt i fremtiden")
    await migrate(session)
    art = await _get(session, nid) if nid else None
    if nid and art is None:
        raise article_edit.Invalid("Nyheden findes ikke")
    if art is None:
        art = ArtArticle(kind="nyhed", source="lokal", dato=now, created_by=user.id, comments_open=False, locally_edited=True, version=1)
        session.add(art)
    elif data.get("version") is not None and data["version"] != art.version:
        raise article_edit.Conflict(art.version, None)
    else:
        art.version += 1
    art.title, art.lead, art.body_md = data["title"].strip()[:200], (data.get("lead") or "").strip() or None, data["body"]
    art.cover_media_id, art.status = data.get("cover_media_id"), data["status"]
    art.publish_at = data.get("publish_at") if data["status"] == "planlagt" else None
    if data["status"] == "publiceret" and art.published_at is None:
        art.published_at = now
    art.updated_at, art.updated_by = now, user.id
    rendered = await content_render.render_for(session, art.body_md, "nyhed")
    art.body_html, art.body_text, art.render_version = rendered.html, content_render.to_text(rendered.html), content_render.RENDER_VERSION
    await session.flush()
    if not art.authors:
        art.authors = await article_authors.set_for_article(session, art.id, [{"navn": user.navn or user.obserkode, "user_id": user.id}])
    ids = set(content_render.media_ids(art.body_md)) | ({art.cover_media_id} if art.cover_media_id else set())
    if ids:
        await session.execute(text("UPDATE content_media SET owner_id = :n WHERE id = ANY(:ids) AND owner_kind = 'nyhed' AND owner_id IS NULL AND created_by IS NOT DISTINCT FROM :u"),
                              {"n": art.id, "ids": list(ids), "u": user.id})
    await session.commit()
    await bump("articles")
    return art


async def delete(session: AsyncSession, nid: int) -> None:
    from portal.models import ContentMedia
    from portal.services import content_media

    art = await _get(session, nid)
    if art is None:
        return
    if art.news_id:  # også den gamle række, så en ny flytning ikke henter nyheden tilbage
        await session.execute(text("DELETE FROM news WHERE id = :i"), {"i": art.news_id})
    for m in (await session.execute(select(ContentMedia).where(ContentMedia.owner_kind == "nyhed", ContentMedia.owner_id == nid))).scalars().all():
        await content_media.delete_media(session, m)
    await session.execute(text("DELETE FROM art_articles WHERE id = :i AND kind = 'nyhed'"), {"i": nid})
    await session.commit()
    await bump("articles")
