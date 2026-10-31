"""Artikelmodulet, skrivesiden: oprette, rette, publicere, revisioner, emneord og arter (docs/artikler-modul-spec.md §7.2, §9)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import ArtArticle, ArtArticleTag, ArtRevision, ArtTag, ContentMedia, User
from portal.services import article_species, content_render

STATUSES = ("kladde", "planlagt", "publiceret", "skjult")
KEEP_REVISIONS = 50


class Conflict(Exception):
    """Artiklen er ændret af en anden, siden redaktøren åbnede den (optimistisk låsning)."""

    def __init__(self, current: int, by: str | None):
        super().__init__("Artiklen er ændret af " + (by or "en anden") + " imens")
        self.current, self.by = current, by


class Invalid(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def tag_ids(session: AsyncSession, names: list[str], matcher) -> list[int]:
    out: list[int] = []
    for raw in names:
        name = raw.strip()[:120]
        if not name:
            continue
        row = (await session.execute(select(ArtTag).where(ArtTag.navn_lc == name.lower()))).scalar_one_or_none()
        if row is None:
            from portal.services.article_import import classify_tag

            kind, artnr = classify_tag(name, matcher.exact)
            row = ArtTag(navn=name, navn_lc=name.lower(), kind=kind, artnr=artnr, hidden=kind == "type")
            session.add(row)
            await session.flush()
        if row.id not in out:
            out.append(row.id)
    return out


def _meta(art: ArtArticle, tags: list[str]) -> dict:
    return {"lead": art.lead, "authors": art.authors, "dato": art.dato.isoformat(), "tags": tags, "cover_media_id": art.cover_media_id,
            "cover_caption": art.cover_caption, "status": art.status, "publish_at": art.publish_at.isoformat() if art.publish_at else None,
            "comments_open": art.comments_open, "source_url": art.source_url, "kind": art.kind}


async def _tag_names(session: AsyncSession, aid: int) -> list[str]:
    return [r[0] for r in (await session.execute(text(
        "SELECT t.navn FROM art_article_tags x JOIN art_tags t ON t.id = x.tag_id WHERE x.article_id = :i ORDER BY t.navn"), {"i": aid})).all()]


def validate(data: dict) -> None:
    if not data["title"].strip():
        raise Invalid("Artiklen skal have en titel")
    if data["status"] not in STATUSES:
        raise Invalid("Ukendt status")
    if data["status"] == "planlagt":
        pa = data.get("publish_at")
        if pa is None:
            raise Invalid("En planlagt artikel skal have et tidspunkt")
        if pa <= _now():
            raise Invalid("Tidspunktet for planlagt publicering skal ligge i fremtiden")


async def _adopt_media(session: AsyncSession, art: ArtArticle, user_id: int | None) -> None:
    """Medier uploadet, før artiklen var gemt (owner_id er null), bliver artiklens."""
    ids = set(content_render.media_ids(art.body_md))
    if art.cover_media_id:
        ids.add(art.cover_media_id)
    if ids:
        await session.execute(text("UPDATE content_media SET owner_id = :a WHERE id = ANY(:ids) AND owner_kind = 'artikel' AND owner_id IS NULL AND created_by IS NOT DISTINCT FROM :u"),
                              {"a": art.id, "ids": list(ids), "u": user_id})


async def save(session: AsyncSession, user: User, data: dict, art: ArtArticle | None = None) -> tuple[ArtArticle, bool]:
    """Opret (art=None) eller ret en artikel. Returnerer (artikel, første gang publiceret).

    data: title, lead, body_md, authors, dato, tags (navne), cover_media_id, cover_caption, status, publish_at, featured_until,
    comments_open, source_url, kind og – ved ret – version (optimistisk låsning)."""
    validate(data)
    matcher = await article_species.load_matcher(session)
    created = art is None
    if created:
        art = ArtArticle(title=data["title"].strip()[:300], dato=data.get("dato") or _now(), source="lokal", created_by=user.id)
        session.add(art)
    else:
        if data.get("version") is not None and data["version"] != art.version:
            by = (await session.execute(text("SELECT coalesce(navn, obserkode) FROM users WHERE id = :u"), {"u": art.updated_by})).scalar() if art.updated_by else None
            raise Conflict(art.version, by)
        session.add(ArtRevision(article_id=art.id, version=art.version, title=art.title, body_md=art.body_md,
                                meta=_meta(art, await _tag_names(session, art.id)), user_id=art.updated_by))
        art.version += 1
    title = data["title"].strip()[:300]
    art.title, art.slug = title, content_render.slugify(title)
    art.lead = (data.get("lead") or "").strip() or None
    art.body_md = data.get("body_md") or ""
    art.authors = [{"navn": a["navn"].strip(), **({"user_id": a["user_id"]} if a.get("user_id") else {})} for a in data.get("authors") or [] if a.get("navn", "").strip()]
    if data.get("dato"):
        art.dato = data["dato"]
    art.cover_media_id, art.cover_caption = data.get("cover_media_id"), (data.get("cover_caption") or None)
    art.comments_open = bool(data.get("comments_open", True))
    art.source_url = data.get("source_url") or None
    art.featured_until = data.get("featured_until")
    art.kind = data.get("kind") or art.kind or "artikel"
    first_publish = False
    status = data["status"]
    art.status, art.publish_at = status, data.get("publish_at") if status == "planlagt" else None
    if status == "publiceret" and art.published_at is None:
        art.published_at, first_publish = _now(), art.source == "lokal"
    if art.source == "netfugl":
        art.locally_edited = True
    art.updated_by, art.updated_at = user.id, _now()
    await session.flush()
    await _adopt_media(session, art, user.id)
    rendered = await content_render.render_for(session, art.body_md, "artikel")
    art.body_html, art.body_text, art.render_version = rendered.html, rendered.text, content_render.RENDER_VERSION
    await session.execute(delete(ArtArticleTag).where(ArtArticleTag.article_id == art.id))
    session.add_all(ArtArticleTag(article_id=art.id, tag_id=t) for t in await tag_ids(session, data.get("tags") or [], matcher))
    await session.flush()
    await article_species.index_article(session, art.id, matcher)
    old = (await session.execute(select(ArtRevision.id).where(ArtRevision.article_id == art.id).order_by(ArtRevision.version.desc()).offset(KEEP_REVISIONS))).scalars().all()
    if old:
        await session.execute(delete(ArtRevision).where(ArtRevision.id.in_(old)))
    await session.commit()
    await bump("articles")
    return art, first_publish


async def delete_article(session: AsyncSession, art: ArtArticle) -> None:
    from portal.services import content_media

    for m in (await session.execute(select(ContentMedia).where(ContentMedia.owner_kind == "artikel", ContentMedia.owner_id == art.id))).scalars().all():
        await content_media.delete_media(session, m)
    await session.delete(art)
    await session.commit()
    await bump("articles")


async def revisions(session: AsyncSession, aid: int) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT r.id, r.version, r.title, r.created_at, coalesce(u.navn, u.obserkode) AS af FROM art_revisions r LEFT JOIN users u ON u.id = r.user_id
        WHERE r.article_id = :i ORDER BY r.version DESC"""), {"i": aid})).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


async def revision(session: AsyncSession, aid: int, rid: int) -> dict | None:
    r = (await session.execute(select(ArtRevision).where(ArtRevision.id == rid, ArtRevision.article_id == aid))).scalar_one_or_none()
    if r is None:
        return None
    rendered = await content_render.render_for(session, r.body_md, "artikel")
    return {"id": r.id, "version": r.version, "title": r.title, "body_md": r.body_md, "meta": r.meta, "html": rendered.html}


async def set_species(session: AsyncSession, aid: int, add: list[int], remove: list[int], restore: list[int]) -> None:
    """Redaktørens valg: tilføj en art (redaktoer), fjern en falsk positiv (fravalgt) eller fortryd et fravalg."""
    for n in add:
        await session.execute(text("""INSERT INTO art_species (article_id, artnr, source, hits) VALUES (:a, :n, 'redaktoer', 1)
                                      ON CONFLICT (article_id, artnr) DO UPDATE SET source = 'redaktoer'"""), {"a": aid, "n": n})
    for n in remove:
        await session.execute(text("""INSERT INTO art_species (article_id, artnr, source, hits) VALUES (:a, :n, 'fravalgt', 0)
                                      ON CONFLICT (article_id, artnr) DO UPDATE SET source = 'fravalgt'"""), {"a": aid, "n": n})
    if restore:
        await session.execute(text("DELETE FROM art_species WHERE article_id = :a AND artnr = ANY(:n) AND source = 'fravalgt'"), {"a": aid, "n": restore})
        await article_species.index_article(session, aid)
    await session.commit()
    await bump("articles")


# ---------------------------------------------------------------- emneord (admin)

async def tags_admin(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT t.id, t.navn, t.kind, t.artnr, t.hidden, t.netfugl_tag_id, count(x.article_id) AS n FROM art_tags t
        LEFT JOIN art_article_tags x ON x.tag_id = t.id GROUP BY t.id ORDER BY lower(t.navn)"""))).mappings().all()
    return [dict(r) for r in rows]


TAG_KINDS = ("type", "publikation", "aar", "emne", "sted", "art")


async def apply_tag(session: AsyncSession, t: ArtTag, *, navn: str | None, kind: str | None, artnr: int | None, hidden: bool | None,
                    batch_ids: set[int] = frozenset()) -> bool:
    """Ret ét emneord uden at committe. Returnerer True, hvis artiklernes arter skal genberegnes. ``batch_ids``: emneord der rettes
    i samme omgang (navnesammenfald mellem dem tjekkes af den, der kalder)."""
    navn = navn.strip()[:120] if navn is not None else None
    if navn and navn.lower() != t.navn_lc:
        clash = (await session.execute(select(ArtTag.id).where(ArtTag.navn_lc == navn.lower(), ArtTag.id != t.id,
                                                               ArtTag.id.notin_(batch_ids or {0})))).first()
        if clash:
            raise Invalid(f"Et emneord med navnet «{navn}» findes allerede – brug «Flet»")
        t.navn, t.navn_lc = navn, navn.lower()
    before = (t.kind, t.artnr)
    if kind is not None:
        if kind not in TAG_KINDS:
            raise Invalid(f"Ukendt slags «{kind}»")
        t.kind = kind
    if artnr is not None or kind == "art":
        t.artnr = artnr
    if hidden is not None:
        t.hidden = hidden
    return (t.kind, t.artnr) != before and (t.kind == "art" or before[0] == "art")


async def reindex_tag_articles(session: AsyncSession, tag_ids: set[int]) -> None:
    """Genberegn arterne for artiklerne med disse emneord (efter at et emneord er koblet til eller fra en art)."""
    if not tag_ids:
        return
    aids = (await session.execute(select(ArtArticleTag.article_id).where(ArtArticleTag.tag_id.in_(tag_ids)).distinct())).scalars().all()
    matcher = await article_species.load_matcher(session)
    for aid in aids:
        await article_species.index_article(session, aid, matcher)
    await session.commit()


async def tag_update(session: AsyncSession, tid: int, *, navn: str | None, kind: str | None, artnr: int | None, hidden: bool | None) -> None:
    t = await session.get(ArtTag, tid)
    if t is None:
        raise Invalid("Emneordet findes ikke")
    reindex = await apply_tag(session, t, navn=navn, kind=kind, artnr=artnr, hidden=hidden)
    await session.commit()
    await bump("articles")
    if reindex:
        await reindex_tag_articles(session, {tid})


async def tag_merge(session: AsyncSession, src: int, dst: int) -> int:
    """Flyt alle artikler fra emneord src til dst og slet src. Returnerer antal flyttede artikler."""
    if src == dst:
        raise Invalid("Vælg to forskellige emneord")
    moved = (await session.execute(text("""
        INSERT INTO art_article_tags (article_id, tag_id) SELECT article_id, :d FROM art_article_tags WHERE tag_id = :s ON CONFLICT DO NOTHING RETURNING article_id"""),
        {"s": src, "d": dst})).all()
    await session.execute(text("DELETE FROM art_tags WHERE id = :s"), {"s": src})
    await session.commit()
    await bump("articles")
    return len(moved)


# ---------------------------------------------------------------- planlagt publicering

async def publish_due(session: AsyncSession) -> list[int]:
    """Planlagte artikler og nyheder, hvis tid er kommet. Returnerer id'er på artikler, der er publiceret første gang."""
    rows = (await session.execute(text("""
        UPDATE art_articles SET status = 'publiceret', published_at = coalesce(published_at, publish_at, now()), publish_at = NULL
        WHERE status = 'planlagt' AND publish_at <= now() RETURNING id, source"""))).all()
    await session.execute(text("UPDATE news SET status = 'publiceret', published = true, publish_at = NULL WHERE status = 'planlagt' AND publish_at <= now()"))
    await session.commit()
    if rows:
        await bump("articles")
    return [r[0] for r in rows if r[1] == "lokal"]

