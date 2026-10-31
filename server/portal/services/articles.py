"""Artikelmodulet, læsesiden: forside, søgning, emneord, arter, forfattere og artikelsiden (docs/artikler-modul-spec.md §5–6)."""

from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv, version
from portal.services import content_media, content_render
from portal.timeutil import dk_today

PER_PAGE = 20
MARK_START, MARK_END = "\x01", "\x02"
SORTS = {"relevans", "nyeste", "aeldste", "mest_laest", "mest_kommenteret", "hits"}

# Forsidebillede til et kort: valgt cover → første billede → miniature af PDF'ens side 1 (§6.1). Ingen af dem → pladsholder.
COVER = """
LEFT JOIN LATERAL (
    SELECT m.id AS cover_id, m.kind AS cover_kind,
           coalesce(m.thumb_key, CASE WHEN m.kind = 'billede' THEN m.storage_key END) AS cover_thumb,
           coalesce(m.medium_key, CASE WHEN m.kind = 'billede' THEN m.storage_key END) AS cover_medium,
           m.width AS cover_w, m.height AS cover_h, m.alt AS cover_alt
    FROM content_media m
    WHERE m.owner_kind = 'artikel' AND m.owner_id = a.id AND (m.id = a.cover_media_id OR m.kind = 'billede' OR (m.kind = 'pdf' AND m.thumb_key IS NOT NULL))
    ORDER BY (m.id = a.cover_media_id) DESC, (m.kind = 'billede') DESC, m.sort, m.id LIMIT 1
) c ON true"""
CARD = f"""
    a.id, a.title, a.kind, a.dato, a.published_at, a.authors, a.lead, a.status, a.featured_until,
    left(a.body_text, 700) AS body_start, CASE WHEN a.body_text = '' THEN left(a.ocr_text, 700) END AS ocr_start,
    a.views_imported + a.views AS views,
    (SELECT count(*) FROM art_comments k WHERE k.article_id = a.id AND k.status = 'synlig') AS kommentarer,
    c.cover_id, c.cover_kind, c.cover_thumb, c.cover_medium, c.cover_w, c.cover_h, c.cover_alt"""
ORDER_NEWEST = "coalesce(a.published_at, a.dato) DESC, a.id DESC"


def vis(can_edit: bool) -> str:
    return "TRUE" if can_edit else "a.status = 'publiceret'"


def _iso(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _url(key: str | None) -> str | None:
    return "/media/art/" + key if key else None


def _clean_ocr_start(s: str | None) -> str:
    return content_render.teaser(re.sub(r"\s+", " ", s or ""), 260)


def card(r, tags: list[dict] | None = None, arter: list[dict] | None = None) -> dict:
    r = dict(r)
    teaser = r["lead"] or content_render.teaser(r.pop("body_start") or "", 260) or _clean_ocr_start(r.get("ocr_start"))
    r.pop("body_start", None)
    r.pop("ocr_start", None)
    cover = None
    if r.get("cover_id"):
        cover = {"id": r["cover_id"], "kind": r["cover_kind"], "thumb": _url(r["cover_thumb"]), "medium": _url(r["cover_medium"]),
                 "w": r["cover_w"], "h": r["cover_h"], "alt": r["cover_alt"] or ""}
    return {"id": r["id"], "title": r["title"], "kind": r["kind"], "dato": _iso(r["dato"]), "published_at": _iso(r["published_at"]),
            "authors": [a.get("navn") for a in r["authors"] or [] if a.get("navn")], "teaser": teaser, "status": r["status"],
            "featured": bool(r.get("featured_until") and r["featured_until"] > datetime.now(r["featured_until"].tzinfo)),
            "views": r["views"], "kommentarer": r["kommentarer"], "cover": cover, "tags": tags or [], "arter": arter or []}


async def _decorate(session: AsyncSession, rows: list) -> list[dict]:
    """Kort med de tre vigtigste emneord og de fem vigtigste arter."""
    ids = [r["id"] for r in rows]
    tags: dict[int, list] = {}
    arter: dict[int, list] = {}
    if ids:
        for r in (await session.execute(text("""
            SELECT x.article_id, t.id, t.navn, t.kind FROM art_article_tags x JOIN art_tags t ON t.id = x.tag_id
            WHERE x.article_id = ANY(:ids) AND NOT t.hidden AND t.kind IN ('emne', 'sted', 'publikation') ORDER BY t.kind, t.navn"""), {"ids": ids})).mappings():
            if len(tags.setdefault(r["article_id"], [])) < 3:
                tags[r["article_id"]].append({"id": r["id"], "navn": r["navn"]})
        for r in (await session.execute(text("""
            SELECT s.article_id, s.artnr, sp.navn FROM art_species s JOIN species sp ON sp.artnr = s.artnr
            WHERE s.article_id = ANY(:ids) AND s.source <> 'fravalgt'
            ORDER BY (s.source IN ('emneord', 'redaktoer')) DESC, s.hits DESC, sp.sortering"""), {"ids": ids})).mappings():
            if len(arter.setdefault(r["article_id"], [])) < 5:
                arter[r["article_id"]].append({"artnr": r["artnr"], "navn": r["navn"]})
    return [card(r, tags.get(r["id"]), arter.get(r["id"])) for r in rows]


async def _cards(session: AsyncSession, where: str, params: dict, order: str = ORDER_NEWEST, limit: int = 12, offset: int = 0) -> list[dict]:
    rows = (await session.execute(text(f"SELECT {CARD} FROM art_articles a {COVER} WHERE {where} ORDER BY {order} LIMIT :lim OFFSET :off"),
                                  {**params, "lim": limit, "off": offset})).mappings().all()
    return await _decorate(session, rows)


# ---------------------------------------------------------------- søgning

def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snippet(src: str, q: str, width: int = 120) -> str:
    i = src.lower().find(q.lower())
    if i < 0:
        return ""
    a, b = max(0, i - width), min(len(src), i + len(q) + width)
    seg = re.sub(r"\s+", " ", src[a:b])
    esc = htmllib.escape(seg)
    qe = htmllib.escape(q)
    esc = re.sub(re.escape(qe), lambda m: f"<mark>{m.group(0)}</mark>", esc, flags=re.I)
    return ("… " if a else "") + esc + (" …" if b < len(src) else "")


def _filters(*, q, mode, tags, arter, forfatter, fra, til, type_, kind, har_billeder, har_pdf, can_edit, with_years=True) -> tuple[str, dict]:
    where, p = [vis(can_edit)], {}
    if q:
        if mode == "praecis":
            where.append("(a.title ILIKE :like ESCAPE '\\' OR a.body_text ILIKE :like ESCAPE '\\' OR a.ocr_text ILIKE :like ESCAPE '\\')")
            p["like"] = _like(q)
        else:
            where.append("a.search @@ websearch_to_tsquery('danish', :q)")
            p["q"] = q
    for n, t in enumerate(tags):
        where.append(f"EXISTS (SELECT 1 FROM art_article_tags x WHERE x.article_id = a.id AND x.tag_id = :t{n})")
        p[f"t{n}"] = t
    if arter:
        where.append("EXISTS (SELECT 1 FROM art_species s WHERE s.article_id = a.id AND s.artnr = ANY(:arter) AND s.source <> 'fravalgt')")
        p["arter"] = list(arter)
    if forfatter:
        where.append("a.authors @> jsonb_build_array(jsonb_build_object('navn', cast(:forf AS text)))")
        p["forf"] = forfatter
    if with_years and fra:
        where.append("a.dato >= make_date(:fra, 1, 1)")
        p["fra"] = fra
    if with_years and til:
        where.append("a.dato < make_date(:til + 1, 1, 1)")
        p["til"] = til
    if type_:
        where.append("EXISTS (SELECT 1 FROM art_article_tags x JOIN art_tags t ON t.id = x.tag_id WHERE x.article_id = a.id AND t.navn_lc = lower(:type))")
        p["type"] = type_
    if kind:
        where.append("a.kind = :kind")
        p["kind"] = kind
    if har_billeder:
        where.append("EXISTS (SELECT 1 FROM content_media m WHERE m.owner_kind = 'artikel' AND m.owner_id = a.id AND m.kind = 'billede')")
    if har_pdf:
        where.append("EXISTS (SELECT 1 FROM content_media m WHERE m.owner_kind = 'artikel' AND m.owner_id = a.id AND m.kind = 'pdf')")
    return " AND ".join(where), p


async def search(session: AsyncSession, *, q: str = "", mode: str = "ord", tags: list[int] | None = None, arter: list[int] | None = None,
                 forfatter: str = "", fra: int | None = None, til: int | None = None, type_: str = "", kind: str = "",
                 har_billeder: bool = False, har_pdf: bool = False, sort: str = "relevans", page: int = 1, can_edit: bool = False,
                 per: int = PER_PAGE) -> dict:
    q = q.strip()[:200]
    mode = "praecis" if mode == "praecis" else "ord"
    sort = sort if sort in SORTS else "relevans"
    args = dict(q=q, mode=mode, tags=tags or [], arter=arter or [], forfatter=forfatter, fra=fra, til=til, type_=type_, kind=kind,
                har_billeder=har_billeder, har_pdf=har_pdf, can_edit=can_edit)
    where, p = _filters(**args)
    if sort == "relevans" and not (q and mode == "ord"):
        sort = "nyeste"
    order = {
        "relevans": "ts_rank_cd(a.search, websearch_to_tsquery('danish', :q)) DESC, " + ORDER_NEWEST,
        "nyeste": ORDER_NEWEST,
        "aeldste": "a.dato ASC, a.id ASC",
        "mest_laest": "a.views_imported + a.views DESC, a.id DESC",
        "mest_kommenteret": "(SELECT count(*) FROM art_comments k WHERE k.article_id = a.id AND k.status = 'synlig') DESC, " + ORDER_NEWEST,
        "hits": "(SELECT max(s.hits) FROM art_species s WHERE s.article_id = a.id AND s.artnr = ANY(:arter)) DESC NULLS LAST, " + ORDER_NEWEST,
    }[sort if sort != "hits" or arter else "nyeste"]
    page = max(1, page)
    total = (await session.execute(text(f"SELECT count(*) FROM art_articles a WHERE {where}"), p)).scalar() or 0
    rows = (await session.execute(text(f"SELECT {CARD} FROM art_articles a {COVER} WHERE {where} ORDER BY {order} LIMIT :lim OFFSET :off"),
                                  {**p, "lim": per, "off": (page - 1) * per})).mappings().all()
    items = await _decorate(session, rows)
    if q and items:
        await _add_snippets(session, items, q, mode)
    fwhere, fp = _filters(**{**args, "fra": None, "til": None}, with_years=False)
    years = (await session.execute(text(f"SELECT extract(year FROM a.dato)::int AS aar, count(*) AS n FROM art_articles a WHERE {fwhere} GROUP BY 1 ORDER BY 1"), fp)).mappings().all()
    return {"total": total, "page": page, "per": per, "rows": items, "facets": {"aar": [dict(y) for y in years]}}


async def _add_snippets(session: AsyncSession, items: list[dict], q: str, mode: str) -> None:
    ids = [i["id"] for i in items]
    if mode == "ord":
        rows = (await session.execute(text("""
            SELECT id, ts_headline('danish', body_text, websearch_to_tsquery('danish', :q), cast(:opt AS text)) AS b,
                   CASE WHEN ocr_text IS NULL THEN NULL
                        ELSE ts_headline('danish', ocr_text, websearch_to_tsquery('danish', :q), cast(:opt AS text)) END AS o
            FROM art_articles WHERE id = ANY(:ids)"""), {"q": q, "ids": ids, "opt": f"MaxWords=35, MinWords=15, StartSel={MARK_START}, StopSel={MARK_END}"})).mappings().all()
        by = {r["id"]: r for r in rows}
        for it in items:
            r = by[it["id"]]
            for src, label in ((r["b"], None), (r["o"], "pdf")):
                if src and MARK_START in src:
                    esc = htmllib.escape(src).replace(MARK_START, "<mark>").replace(MARK_END, "</mark>")
                    it["headline"], it["headline_fra"] = esc, label
                    break
    else:
        rows = (await session.execute(text("SELECT id, title, body_text, ocr_text FROM art_articles WHERE id = ANY(:ids)"), {"ids": ids})).mappings().all()
        by = {r["id"]: r for r in rows}
        for it in items:
            r = by[it["id"]]
            for src, label in ((r["title"], None), (r["body_text"], None), (r["ocr_text"], "pdf")):
                if src and (snip := _snippet(src, q)):
                    it["headline"], it["headline_fra"] = snip, label
                    break


# ---------------------------------------------------------------- forside

async def front(session: AsyncSession, can_edit: bool, settings: dict) -> dict:
    async def produce():
        cfg = settings.get("front", {})
        secondary_n = int(cfg.get("secondary_count", 3))
        pool = await _cards(session, vis(can_edit), {}, limit=60)
        feat = next((c for c in pool if c["featured"]), None)
        hero = feat or next((c for c in pool if c["cover"] and c["cover"]["kind"] == "billede"), None) or (pool[0] if pool else None)
        rest = [c for c in pool if hero is None or c["id"] != hero["id"]]
        with_img = [c for c in rest if c["cover"]]
        secondary = (with_img + [c for c in rest if not c["cover"]])[:secondary_n]
        shown = {c["id"] for c in secondary} | ({hero["id"]} if hero else set())
        latest = [c for c in pool if c["id"] not in shown][:8]
        out = {"hero": hero, "secondary": secondary, "latest": latest,
               "total": (await session.execute(text(f"SELECT count(*) FROM art_articles a WHERE {vis(can_edit)}"))).scalar() or 0}
        out["on_this_day"] = []
        if cfg.get("on_this_day", True):
            t = dk_today()
            out["on_this_day"] = await _cards(
                session, vis(can_edit) + " AND extract(month FROM a.dato AT TIME ZONE 'Europe/Copenhagen') = :m AND extract(day FROM a.dato AT TIME ZONE 'Europe/Copenhagen') = :d "
                "AND extract(year FROM a.dato AT TIME ZONE 'Europe/Copenhagen') < :y", {"m": t.month, "d": t.day, "y": t.year}, "a.dato DESC", 4)
        return out

    return await cached(f"articles:front:{await version('articles')}:{int(can_edit)}", 300, produce)


# ---------------------------------------------------------------- lister

async def tags_overview(session: AsyncSession, can_edit: bool) -> list[dict]:
    rows = (await session.execute(text(f"""
        SELECT t.id, t.navn, t.kind, t.artnr, t.hidden, count(a.id) AS n FROM art_tags t JOIN art_article_tags x ON x.tag_id = t.id
        JOIN art_articles a ON a.id = x.article_id WHERE {vis(can_edit)} GROUP BY t.id ORDER BY lower(t.navn)"""))).mappings().all()
    return [dict(r) for r in rows]


async def species_overview(session: AsyncSession, can_edit: bool) -> list[dict]:
    rows = (await session.execute(text(f"""
        SELECT sp.artnr, sp.navn, sp.latin, count(DISTINCT a.id) AS n FROM art_species s JOIN art_articles a ON a.id = s.article_id
        JOIN species sp ON sp.artnr = s.artnr WHERE {vis(can_edit)} AND s.source <> 'fravalgt'
        GROUP BY sp.artnr, sp.navn, sp.latin, sp.sortering ORDER BY sp.sortering NULLS LAST, sp.navn"""))).mappings().all()
    return [dict(r) for r in rows]


async def authors_overview(session: AsyncSession, can_edit: bool) -> list[dict]:
    rows = (await session.execute(text(f"""
        SELECT x->>'navn' AS navn, count(*) AS n FROM art_articles a, jsonb_array_elements(a.authors) x
        WHERE {vis(can_edit)} AND x->>'navn' IS NOT NULL GROUP BY 1 ORDER BY lower(x->>'navn')"""))).mappings().all()
    return [dict(r) for r in rows]


async def species_box(session: AsyncSession, artnr: int, can_edit: bool, limit: int = 5) -> dict:
    """Til artssiden: de seneste artikler om arten (§6.5)."""
    res = await search(session, arter=[artnr], sort="nyeste", can_edit=can_edit, per=limit)
    return {"total": res["total"], "rows": res["rows"]}


async def by_netfugl(session: AsyncSession, no: int) -> int | None:
    return (await session.execute(text("SELECT id FROM art_articles WHERE netfugl_id = :n"), {"n": no})).scalar()


# ---------------------------------------------------------------- artikelsiden

async def detail(session: AsyncSession, aid: int, *, can_edit: bool, user_id: int) -> dict | None:
    r = (await session.execute(text(f"""
        SELECT a.id, a.netfugl_id, a.kind, a.title, a.lead, a.body_html, a.dato, a.published_at, a.authors, a.status, a.publish_at, a.featured_until,
               a.comments_open, a.source, a.source_url, a.views_imported + a.views AS views, a.cover_media_id, a.cover_caption, a.version,
               a.locally_edited, a.import_errors, a.body_md, a.uploaded_by, a.updated_at, left(a.body_text, 400) AS body_start,
               (SELECT count(*) FROM art_comments k WHERE k.article_id = a.id AND k.status = 'synlig') AS kommentarer
        FROM art_articles a WHERE a.id = :i AND {vis(can_edit)}"""), {"i": aid})).mappings().first()
    if r is None:
        return None
    media = (await session.execute(text("""
        SELECT id, kind, storage_key, thumb_key, medium_key, width, height, alt, caption, credit, filename, size, pages, text_md, text_source
        FROM content_media WHERE owner_kind = 'artikel' AND owner_id = :i ORDER BY sort, id"""), {"i": aid})).mappings().all()
    inline = set(content_render.media_ids(r["body_md"]))
    images, pdfs, files = [], [], []
    for m in media:
        base = {"id": m["id"], "url": _url(m["storage_key"]), "filename": m["filename"], "size": m["size"], "caption": m["caption"] or "", "alt": m["alt"] or ""}
        if m["kind"] == "billede":
            images.append({**base, "thumb": _url(m["thumb_key"] or m["storage_key"]), "medium": _url(m["medium_key"] or m["storage_key"]),
                           "w": m["width"], "h": m["height"], "credit": m["credit"] or "", "inline": m["id"] in inline})
        elif m["kind"] == "pdf":
            pdfs.append({**base, "thumb": _url(m["thumb_key"]), "pages": m["pages"], "text_md": m["text_md"], "text_source": m["text_source"]})
        else:
            files.append(base)
    all_tags = [dict(t) for t in (await session.execute(text("""
        SELECT t.id, t.navn, t.kind, t.hidden FROM art_article_tags x JOIN art_tags t ON t.id = x.tag_id WHERE x.article_id = :i ORDER BY t.kind, t.navn"""), {"i": aid})).mappings()]
    tags = [t for t in all_tags if not t["hidden"]]
    species = [dict(s) for s in (await session.execute(text("""
        SELECT s.artnr, sp.navn, sp.latin, s.source, s.hits FROM art_species s JOIN species sp ON sp.artnr = s.artnr
        WHERE s.article_id = :i AND s.source <> 'fravalgt' ORDER BY (s.source = 'ocr'), sp.sortering NULLS LAST"""), {"i": aid})).mappings()]
    sub = (await session.execute(text("SELECT muted FROM art_subscriptions WHERE user_id = :u AND kind = 'artikel' AND ref = :i"),
                                 {"u": user_id, "i": aid})).first()
    cover = next((i for i in images if i["id"] == r["cover_media_id"]), None) or next((i for i in images if not i["inline"]), None) or (images[0] if images else None)
    pdf_cover = pdfs[0] if r["kind"] == "pdf" and pdfs else None
    out = {k: _iso(v) for k, v in dict(r).items() if k not in ("body_md", "body_start")}
    out.update(images=images, pdfs=pdfs, files=files, tags=tags, species=species, cover=cover, pdf=pdf_cover, following=bool(sub) and not sub[0],
               muted=bool(sub) and bool(sub[0]), can_edit=can_edit, teaser=content_render.teaser(r["body_start"] or "", 200))
    if can_edit:
        out["body_md"] = r["body_md"]
        out["tags_all"] = all_tags  # inkl. skjulte typer (Nyhed, Artikel), så de ikke går tabt, når en redaktør gemmer
    out["related"] = await related(session, aid, can_edit)
    return out


async def related(session: AsyncSession, aid: int, can_edit: bool, limit: int = 4) -> list[dict]:
    rows = (await session.execute(text(f"""
        SELECT {CARD}, coalesce(sp.n, 0) * 2 + coalesce(tg.n, 0) AS score
        FROM art_articles a {COVER}
        LEFT JOIN (SELECT s2.article_id, count(*) AS n FROM art_species s1 JOIN art_species s2 ON s2.artnr = s1.artnr AND s2.article_id <> s1.article_id
                   WHERE s1.article_id = :i AND s1.source <> 'fravalgt' AND s2.source <> 'fravalgt' GROUP BY 1) sp ON sp.article_id = a.id
        LEFT JOIN (SELECT x2.article_id, count(*) AS n FROM art_article_tags x1 JOIN art_tags t ON t.id = x1.tag_id AND NOT t.hidden AND t.kind IN ('emne', 'sted', 'art')
                   JOIN art_article_tags x2 ON x2.tag_id = x1.tag_id AND x2.article_id <> x1.article_id WHERE x1.article_id = :i GROUP BY 1) tg ON tg.article_id = a.id
        WHERE {vis(can_edit)} AND a.id <> :i AND (sp.n IS NOT NULL OR tg.n IS NOT NULL)
        ORDER BY score DESC, {ORDER_NEWEST} LIMIT :lim"""), {"i": aid, "lim": limit})).mappings().all()
    return await _decorate(session, rows)


async def count_view(article_id: int, user_id: int) -> None:
    """Én læsning pr. bruger pr. døgn; lægges i Valkey og skrives til databasen af articles_publish (hvert 10. minut)."""
    r = kv()
    if await r.set(f"artview:{article_id}:{user_id}", "1", nx=True, ex=86400):
        await r.hincrby("artviews", str(article_id), 1)


async def flush_views(session: AsyncSession) -> int:
    r = kv()
    pending = await r.hgetall("artviews")
    n = 0
    for aid, cnt in pending.items():
        key = aid.decode() if isinstance(aid, bytes) else aid
        c = int(cnt)
        await session.execute(text("UPDATE art_articles SET views = views + :c WHERE id = :i"), {"c": c, "i": int(key)})
        await r.hincrby("artviews", key, -c)
        n += 1
    await session.commit()
    return n


def media_public(rows) -> list[dict]:
    return [content_media.public(m) for m in rows]
