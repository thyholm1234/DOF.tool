"""Netfugl-modulet (showcase af en ny netfugl.dk): forside, artikler, krydslister, artslister, fotogalleri, RSS, brugere og admin.

Alt kræver, at funktionen "netfugl" er synlig for brugeren (Admin → Funktioner). Redigering kræver en Netfugl-rolle med den
relevante rettighed (services.netfugl.PERMS). Krydslisterne beregnes ud fra DOFbasen-data; fotos kan knyttes til en
observation i DOFbasen. Det offentlige token-API ligger i netfugl_v1.py.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import bump, cached, worker_command
from portal.db import get_session
from portal.models import (NfApiGrant, NfApiToken, NfArticle, NfComment, NfFeed, NfKryds, NfLike, NfLink, NfPage, NfPhoto,
                           NfRole, NfSpecies, Setting, User)
from portal.services import dofbasen as dofb
from portal.services import features
from portal.services import netfugl as svc
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/netfugl", tags=["netfugl"])
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def need(feature: str = "netfugl", perm: str | None = None):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> svc.Ctx:
        ctx = await svc.context(db, user)
        if not ctx.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if perm and not ctx.can(perm):
            label = svc.PERMS[perm].split(":")[0]
            raise HTTPException(403, "Kræver Netfugl-rettigheden " + label[0].lower() + label[1:])
        return ctx

    return dep


async def _rows(db: AsyncSession, sql: str, **args) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql), args)).mappings().all()]


def _iso(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (date, datetime)):
                r[k] = v.isoformat()
    return rows


# ---------------------------------------------------------------- fælles


@router.get("/ctx")
async def ctx_info(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    ctx = await svc.context(db, user)
    out = {"enabled": ctx.on("netfugl"), **ctx.out(), "superadmin": ctx.superadmin}
    if out["enabled"]:
        out["me"] = {"id": user.id, "obserkode": user.obserkode, "navn": user.navn} if user else None
        out["afdelinger"] = await _rows(db, "SELECT id, navn FROM afdelinger ORDER BY id")
        out["api_access"] = bool(user and await db.get(NfApiGrant, user.id))
    return out


PHOTO_COLS = """p.id, p.url, p.width, p.height, p.artnr, p.art_navn, p.latin, p.dato, p.lokalitet, p.loknr, p.land, p.alder,
    p.obsid, p.status, p.views, p.published_at, p.created_at, p.user_id, coalesce(u.navn, u.obserkode) AS fotograf, u.obserkode,
    (SELECT count(*) FROM nf_likes l WHERE l.target_kind = 'foto' AND l.target_id = p.id) AS likes,
    (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id AND NOT c.hidden) AS kommentarer"""


async def photo_list(db: AsyncSession, where: str = "TRUE", order: str = "p.published_at DESC", limit: int = 12, offset: int = 0, **args) -> list[dict]:
    return _iso(await _rows(db, f"""SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id
        WHERE p.status = 'publiceret' AND {where} ORDER BY {order} LIMIT {int(limit)} OFFSET {int(offset)}""", **args))


# ---------------------------------------------------------------- forside


@router.get("/forside")
async def forside(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    out: dict = {}
    if ctx.on("netfugl_artikler"):
        out["artikler"] = _iso(await _rows(db, """SELECT id, title, dato, writers, image_url, created_at FROM nf_articles
            WHERE published AND kind = 'artikel' ORDER BY dato DESC, id DESC LIMIT 3"""))
        out["kort"] = _iso(await _rows(db, """SELECT a.id, a.title, a.dato, a.source_url,
            (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'artikel' AND c.target_id = a.id AND NOT c.hidden) AS kommentarer
            FROM nf_articles a WHERE published AND kind = 'kort' ORDER BY dato DESC, id DESC LIMIT 5"""))
        out["kommentarer"] = _iso(await _rows(db, """
            SELECT DISTINCT ON (a.id) a.id, a.title, c.created_at, coalesce(u.navn, u.obserkode) AS navn,
                   (SELECT count(*) FROM nf_comments x WHERE x.target_kind = 'artikel' AND x.target_id = a.id AND NOT x.hidden) AS antal
            FROM nf_comments c JOIN nf_articles a ON a.id = c.target_id JOIN users u ON u.id = c.user_id
            WHERE c.target_kind = 'artikel' AND NOT c.hidden AND a.published ORDER BY a.id, c.created_at DESC"""))
        out["kommentarer"] = sorted(out["kommentarer"], key=lambda r: r["created_at"], reverse=True)[:6]
    if ctx.on("netfugl_fotos"):
        out["fotos"] = await photo_list(db)
        out["fotos_likes"] = await photo_list(db, "p.published_at > now() - interval '30 days'", "likes DESC, p.published_at DESC")
        out["fotos_kommenteret"] = await photo_list(db, """EXISTS (SELECT 1 FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id)""",
                                                    "(SELECT max(created_at) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id) DESC")
        out["fotos_su"] = await photo_list(db, "p.artnr IN (SELECT artnr FROM species WHERE klass = 'SU')")
    if ctx.on("netfugl_rss"):
        for key, cond in (("rss_dk", "f.land = 'Danmark'"), ("rss_ud", "f.land <> 'Danmark'")):
            out[key] = _iso(await _rows(db, f"""SELECT i.title, i.url, i.published_at, f.navn AS kilde FROM nf_feed_items i
                JOIN nf_feeds f ON f.id = i.feed_id WHERE {cond} ORDER BY i.published_at DESC LIMIT 15"""))
    if ctx.on("netfugl_kryds"):
        out["kryds"] = await kryds_oversigt_data(db, 10)
    out["links"] = [{"gruppe": l.gruppe, "titel": l.titel, "url": l.url} for l in (await db.execute(select(NfLink).order_by(NfLink.sort))).scalars()]
    return out


@router.get("/sjaeldne")
async def sjaeldne(kind: str = Query("rare", pattern="^(rare|uncommon)$"), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Seneste sjældne (SU) og fåtallige (SUB) fugle i DOFbasen – 50 seneste (dato, art, lokalitet)."""
    kat = "SU" if kind == "rare" else "SUB"

    async def produce():
        return _iso(await _rows(db, """
            SELECT DISTINCT ON (o.dato, o.artnr, o.loknr) o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lokalitet, o.obsid
            FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
            WHERE o.dato > :d AND o.kategori = :k AND o.hemmelig = 0 ORDER BY o.dato DESC, o.artnr, o.loknr, o.obsid LIMIT 50
        """, d=dk_today() - timedelta(days=60), k=kat))
    rows = await cached(f"nf:sjf:{kind}:{dk_today()}:{datetime.now().hour}:{datetime.now().minute // 5}", 300, produce)
    return {"rows": rows, "dofbasen": ctx.out()["features"]["netfugl_dofbasen"]}


# ---------------------------------------------------------------- tekstsider og links


class PageIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(max_length=100_000)


@router.get("/sider/{slug}")
async def side(slug: str, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    p = await db.get(NfPage, slug)
    if p is None:
        raise HTTPException(404, "Siden findes ikke")
    return {"slug": p.slug, "title": p.title, "body": p.body, "updated_at": p.updated_at, "can_edit": ctx.can_edit("sider")}


@router.get("/sider")
async def sider(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return [{"slug": p.slug, "title": p.title, "updated_at": p.updated_at} for p in (await db.execute(select(NfPage).order_by(NfPage.title))).scalars()]


@router.put("/sider/{slug}")
async def gem_side(slug: str, body: PageIn, ctx: svc.Ctx = Depends(need(perm="sider")), db: AsyncSession = Depends(get_session)):
    if not re.fullmatch(r"[a-z0-9_]{2,40}", slug):
        raise HTTPException(400, "Ugyldig adresse")
    p = await db.get(NfPage, slug)
    if p is None:
        p = NfPage(slug=slug)
        db.add(p)
    p.title, p.body, p.updated_by, p.updated_at = body.title, body.body, ctx.user.id, datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "side", slug)
    await db.commit()
    return {"ok": True}


@router.delete("/sider/{slug}")
async def slet_side(slug: str, ctx: svc.Ctx = Depends(need(perm="sider")), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(NfPage).where(NfPage.slug == slug))
    await svc.audit(db, ctx.user, "slet side", slug)
    await db.commit()
    return {"ok": True}


class LinkIn(BaseModel):
    gruppe: str = Field(min_length=1, max_length=60)
    titel: str = Field(min_length=1, max_length=160)
    url: str = Field(pattern=r"^https?://", max_length=400)


@router.get("/links")
async def links(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return [{"id": l.id, "gruppe": l.gruppe, "titel": l.titel, "url": l.url} for l in (await db.execute(select(NfLink).order_by(NfLink.sort))).scalars()]


@router.put("/links")
async def gem_links(body: list[LinkIn], ctx: svc.Ctx = Depends(need(perm="sider")), db: AsyncSession = Depends(get_session)):
    """Erstat alle links (rækkefølgen bevares)."""
    await db.execute(delete(NfLink))
    for i, l in enumerate(body[:500]):
        db.add(NfLink(gruppe=l.gruppe, titel=l.titel, url=l.url, sort=i))
    await svc.audit(db, ctx.user, "links", f"{len(body)} links")
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- artikler og kommentarer


def _article_out(a: NfArticle) -> dict:
    return {"id": a.id, "kind": a.kind, "title": a.title, "body": a.body, "writers": a.writers, "tags": a.tags or [], "dato": a.dato.isoformat(),
            "image_url": a.image_url, "source_url": a.source_url, "published": a.published, "views": a.views,
            "created_at": a.created_at.isoformat() if a.created_at else None}


@router.get("/artikler")
async def artikler(q: str = Query("", max_length=80), tag: str = Query("", max_length=60), aar: int | None = None,
                   kind: str = Query("", pattern="^(|artikel|kort)$"), page: int = Query(1, ge=1), alle: bool = False,
                   ctx: svc.Ctx = Depends(need("netfugl_artikler")), db: AsyncSession = Depends(get_session)):
    where, args = ["TRUE" if alle and ctx.can("artikler") else "published"], {}
    if q.strip():
        where.append("(title ILIKE :q OR body ILIKE :q OR writers ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if tag:
        where.append(":tag = ANY(tags)")
        args["tag"] = tag
    if aar:
        where.append("extract(year FROM dato) = :aar")
        args["aar"] = aar
    if kind:
        where.append("kind = :kind")
        args["kind"] = kind
    w = " AND ".join(where)
    total = (await db.execute(text(f"SELECT count(*) FROM nf_articles WHERE {w}"), args)).scalar()
    rows = _iso(await _rows(db, f"""SELECT a.id, a.kind, a.title, a.dato, a.writers, a.tags, a.image_url, a.published, a.views,
        left(regexp_replace(a.body, '\\[([^\\]]*)\\]\\([^)]*\\)', '\\1', 'g'), 240) AS teaser,
        (SELECT count(*) FROM nf_comments c WHERE c.target_kind = 'artikel' AND c.target_id = a.id AND NOT c.hidden) AS kommentarer
        FROM nf_articles a WHERE {w} ORDER BY dato DESC, id DESC LIMIT 20 OFFSET :off""", **args, off=(page - 1) * 20))
    return {"total": total, "rows": rows, "can_edit": ctx.can_edit("artikler")}


@router.get("/artikler/emneord")
async def emneord(ctx: svc.Ctx = Depends(need("netfugl_artikler")), db: AsyncSession = Depends(get_session)):
    tags = await _rows(db, "SELECT t AS tag, count(*) AS n FROM nf_articles, unnest(tags) t WHERE published GROUP BY t ORDER BY t")
    years = [r[0] for r in (await db.execute(text("SELECT DISTINCT extract(year FROM dato)::int FROM nf_articles WHERE published ORDER BY 1 DESC"))).all()]
    return {"tags": tags, "years": years}


async def comments(db: AsyncSession, kind: str, target: int, ctx: svc.Ctx) -> list[dict]:
    rows = _iso(await _rows(db, """
        SELECT c.id, c.body, c.created_at, c.hidden, c.user_id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode,
               (SELECT count(*) FROM nf_likes l WHERE l.target_kind = 'komm' AND l.target_id = c.id) AS likes
        FROM nf_comments c JOIN users u ON u.id = c.user_id WHERE c.target_kind = :k AND c.target_id = :t ORDER BY c.created_at
    """, k=kind, t=target))
    mod = ctx.can("kommentarer")
    for r in rows:
        r["mine"] = r["user_id"] == ctx.user.id
        if r["hidden"] and not mod and not r["mine"]:
            r["body"] = "(Kommentaren er skjult af en moderator)"
    return rows


@router.get("/artikler/{aid}")
async def artikel(aid: int, ctx: svc.Ctx = Depends(need("netfugl_artikler")), db: AsyncSession = Depends(get_session)):
    a = await db.get(NfArticle, aid)
    if a is None or (not a.published and not ctx.can("artikler")):
        raise HTTPException(404, "Artiklen findes ikke")
    a.views += 1
    await db.commit()
    return {**_article_out(a), "comments": await comments(db, "artikel", aid, ctx), "can_edit": ctx.can_edit("artikler"),
            "can_moderate": ctx.can("kommentarer")}


class ArticleIn(BaseModel):
    kind: str = Field("artikel", pattern="^(artikel|kort)$")
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(max_length=100_000)
    writers: str = Field("", max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=30)
    dato: date
    image_url: str = Field("", max_length=400)
    source_url: str = Field("", max_length=400)
    published: bool = True


@router.post("/artikler")
async def ny_artikel(body: ArticleIn, ctx: svc.Ctx = Depends(need("netfugl_artikler", "artikler")), db: AsyncSession = Depends(get_session)):
    a = NfArticle(**{**body.model_dump(), "tags": [t.strip()[:60] for t in body.tags if t.strip()]}, author_id=ctx.user.id)
    a.image_url, a.source_url = body.image_url or None, body.source_url or None
    db.add(a)
    await svc.audit(db, ctx.user, "ny artikel", body.title)
    await db.commit()
    return _article_out(a)


@router.put("/artikler/{aid}")
async def ret_artikel(aid: int, body: ArticleIn, ctx: svc.Ctx = Depends(need("netfugl_artikler", "artikler")), db: AsyncSession = Depends(get_session)):
    a = await db.get(NfArticle, aid)
    if a is None:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    a.tags = [t.strip()[:60] for t in body.tags if t.strip()]
    a.image_url, a.source_url, a.updated_at = body.image_url or None, body.source_url or None, datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, "artikel", body.title)
    await db.commit()
    return _article_out(a)


@router.delete("/artikler/{aid}")
async def slet_artikel(aid: int, ctx: svc.Ctx = Depends(need("netfugl_artikler", "artikler")), db: AsyncSession = Depends(get_session)):
    a = await db.get(NfArticle, aid)
    if a:
        await db.execute(delete(NfComment).where(NfComment.target_kind == "artikel", NfComment.target_id == aid))
        await svc.audit(db, ctx.user, "slet artikel", a.title)
        await db.delete(a)
        await db.commit()
    return {"ok": True}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


async def _target_exists(db: AsyncSession, kind: str, target: int) -> bool:
    if kind == "artikel":
        a = await db.get(NfArticle, target)
        return bool(a and a.published)
    p = await db.get(NfPhoto, target)
    return bool(p and p.status == "publiceret")


@router.post("/{kind}/{target}/kommentarer")
async def ny_kommentar(kind: str, target: int, body: CommentIn, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    kind = {"artikler": "artikel", "fotos": "foto"}.get(kind, "")
    if not kind or not ctx.on("netfugl_artikler" if kind == "artikel" else "netfugl_fotos"):
        raise HTTPException(404)
    if not await _target_exists(db, kind, target):
        raise HTTPException(404, "Findes ikke")
    from portal.services import moderation

    if await moderation.active_ban(db, ctx.user.id):
        raise HTTPException(403, "Du er udelukket fra at kommentere")
    db.add(NfComment(target_kind=kind, target_id=target, user_id=ctx.user.id, body=body.body.strip()))
    await db.commit()
    return {"ok": True}


@router.delete("/kommentarer/{cid}")
async def slet_kommentar(cid: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Egen kommentar slettes; en moderator skjuler (eller viser igen) andres."""
    c = await db.get(NfComment, cid)
    if c is None:
        raise HTTPException(404)
    if c.user_id == ctx.user.id:
        await db.delete(c)
    elif ctx.can("kommentarer"):
        c.hidden = not c.hidden
        await svc.audit(db, ctx.user, "skjul kommentar" if c.hidden else "vis kommentar", str(cid))
    else:
        raise HTTPException(403)
    await db.commit()
    return {"ok": True}


@router.post("/likes/{kind}/{target}")
async def like(kind: str, target: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    if kind not in ("foto", "komm"):
        raise HTTPException(400)
    row = await db.get(NfLike, (ctx.user.id, kind, target))
    if row:
        await db.delete(row)
    else:
        db.add(NfLike(user_id=ctx.user.id, target_kind=kind, target_id=target))
    await db.commit()
    n = (await db.execute(text("SELECT count(*) FROM nf_likes WHERE target_kind = :k AND target_id = :t"), {"k": kind, "t": target})).scalar()
    return {"liked": row is None, "likes": n}


# ---------------------------------------------------------------- fotogalleri


@router.get("/fotos")
async def fotos(q: str = Query("", max_length=80), art: int | None = None, fotograf: int | None = None, su: bool = False,
                sort: str = Query("nyeste", pattern="^(nyeste|likes|kommenteret|visninger)$"), page: int = Query(1, ge=1),
                per: int = Query(24, ge=6, le=96), ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    where, args = ["TRUE"], {}
    if q.strip():
        where.append("(p.art_navn ILIKE :q OR p.latin ILIKE :q OR p.lokalitet ILIKE :q OR u.navn ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if art:
        where.append("(p.artnr = :art OR p.artnr IN (SELECT artnr FROM species WHERE list_artnr = :art))")
        args["art"] = art
    if fotograf:
        where.append("p.user_id = :fg")
        args["fg"] = fotograf
    if su:
        where.append("p.artnr IN (SELECT artnr FROM species WHERE klass = 'SU')")
    order = {"nyeste": "p.published_at DESC", "likes": "likes DESC, p.published_at DESC", "visninger": "p.views DESC",
             "kommenteret": "(SELECT max(created_at) FROM nf_comments c WHERE c.target_kind = 'foto' AND c.target_id = p.id) DESC NULLS LAST"}[sort]
    w = " AND ".join(where)
    total = (await db.execute(text(f"SELECT count(*) FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.status = 'publiceret' AND {w}"), args)).scalar()
    return {"total": total, "rows": await photo_list(db, w, order, per, (page - 1) * per, **args)}


@router.get("/fotos/{pid}")
async def foto(pid: int, ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    p = await db.get(NfPhoto, pid)
    if p is None or (p.status != "publiceret" and p.user_id != ctx.user.id and not ctx.can("fotos")):
        raise HTTPException(404, "Fotoet findes ikke")
    if p.status == "publiceret":
        p.views += 1
        await db.commit()
    row = (await _rows(db, f"SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.id = :i", i=pid))[0]
    row = _iso([row])[0]
    row["beskrivelse"] = p.beskrivelse
    row["liked"] = bool(await db.get(NfLike, (ctx.user.id, "foto", pid)))
    row["sjaeldenhed"] = (await db.execute(text("SELECT klass FROM species WHERE artnr = :a"), {"a": p.artnr})).scalar() if p.artnr else None
    row["mine"] = p.user_id == ctx.user.id
    row["can_moderate"] = ctx.can("fotos")
    row["dofbasen"] = ctx.out()["features"]["netfugl_dofbasen"]
    row["comments"] = await comments(db, "foto", pid, ctx)
    return row


@router.get("/mine-observationer")
async def mine_obs(artnr: int, dato: date | None = None, ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    """Brugerens egne observationer af arten (på datoen eller de seneste) – til at knytte et foto til DOFbasen."""
    where = "o.obserkode = :k AND (o.artnr = :a OR s.list_artnr = :a)"
    args = {"k": ctx.user.obserkode, "a": artnr}
    if dato:
        where += " AND o.dato BETWEEN :d1 AND :d2"
        args.update(d1=dato - timedelta(days=1), d2=dato + timedelta(days=1))
    return _iso(await _rows(db, f"""SELECT o.obsid, o.dato, o.antal, o.loknr, l.navn AS lokalitet, s.navn AS art
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE {where} ORDER BY o.dato DESC LIMIT 20""", **args))


@router.post("/fotos")
async def upload(file: UploadFile = File(...), art_navn: str = Form(..., max_length=200), artnr: int | None = Form(None),
                 dato: str = Form(""), lokalitet: str = Form("", max_length=250), loknr: int | None = Form(None),
                 land: str = Form("Danmark", max_length=80), alder: str = Form("", max_length=40), beskrivelse: str = Form("", max_length=4000),
                 obsid: int | None = Form(None), width: int | None = Form(None), height: int | None = Form(None),
                 ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    ext = IMAGE_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Kun billeder (jpg, png, webp)")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Billedet er større end 10 MB")
    if obsid:
        own = (await db.execute(text("SELECT 1 FROM observations WHERE obsid = :o AND obserkode = :k"), {"o": obsid, "k": ctx.user.obserkode})).scalar()
        if not own:
            raise HTTPException(400, "Observationen er ikke din")
    try:
        foto_dato = date.fromisoformat(dato) if dato else None
    except ValueError as exc:
        raise HTTPException(400, "Ugyldig dato") from exc
    latin = (await db.execute(text("SELECT latin FROM species WHERE artnr = :a"), {"a": artnr})).scalar() if artnr else None
    name = f"{uuid.uuid4().hex}{ext}"
    (svc.media_root() / name).write_bytes(data)
    moderated = (await svc.settings(db))["photo_moderation"] and not ctx.can("fotos")
    p = NfPhoto(user_id=ctx.user.id, url=f"/media/netfugl/{name}", width=width, height=height, artnr=artnr, art_navn=art_navn.strip(),
                latin=latin, dato=foto_dato, lokalitet=lokalitet or None, loknr=loknr, land=land or "Danmark",
                alder=alder or None, beskrivelse=beskrivelse or None, obsid=obsid,
                status="afventer" if moderated else "publiceret", published_at=None if moderated else datetime.now(timezone.utc))
    db.add(p)
    await db.commit()
    return {"id": p.id, "status": p.status}


class PhotoIn(BaseModel):
    art_navn: str = Field(min_length=1, max_length=200)
    artnr: int | None = None
    dato: date | None = None
    lokalitet: str = Field("", max_length=250)
    land: str = Field("Danmark", max_length=80)
    alder: str = Field("", max_length=40)
    beskrivelse: str = Field("", max_length=4000)
    obsid: int | None = None


@router.put("/fotos/{pid}")
async def ret_foto(pid: int, body: PhotoIn, ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    p = await db.get(NfPhoto, pid)
    if p is None or (p.user_id != ctx.user.id and not ctx.can("fotos")):
        raise HTTPException(404)
    if body.obsid and body.obsid != p.obsid:
        owner = (await db.execute(select(User.obserkode).where(User.id == p.user_id))).scalar()
        own = (await db.execute(text("SELECT 1 FROM observations WHERE obsid = :o AND obserkode = :k"), {"o": body.obsid, "k": owner})).scalar()
        if not own:
            raise HTTPException(400, "Observationen tilhører ikke fotografen")
    for k, v in body.model_dump().items():
        setattr(p, k, v if v != "" else None)
    p.land = body.land or "Danmark"
    await db.commit()
    return {"ok": True}


@router.delete("/fotos/{pid}")
async def slet_foto(pid: int, ctx: svc.Ctx = Depends(need("netfugl_fotos")), db: AsyncSession = Depends(get_session)):
    p = await db.get(NfPhoto, pid)
    if p is None or (p.user_id != ctx.user.id and not ctx.can("fotos")):
        raise HTTPException(404)
    path = svc.media_root() / p.url.rsplit("/", 1)[-1]
    await db.execute(delete(NfComment).where(NfComment.target_kind == "foto", NfComment.target_id == pid))
    await db.execute(delete(NfLike).where(NfLike.target_kind == "foto", NfLike.target_id == pid))
    if p.user_id != ctx.user.id:
        await svc.audit(db, ctx.user, "slet foto", f"{pid} {p.art_navn}")
    await db.delete(p)
    await db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}


class StatusIn(BaseModel):
    status: str = Field(pattern="^(afventer|publiceret|afvist)$")


@router.put("/fotos/{pid}/status")
async def foto_status(pid: int, body: StatusIn, ctx: svc.Ctx = Depends(need("netfugl_fotos", "fotos")), db: AsyncSession = Depends(get_session)):
    p = await db.get(NfPhoto, pid)
    if p is None:
        raise HTTPException(404)
    p.status = body.status
    if body.status == "publiceret" and not p.published_at:
        p.published_at = datetime.now(timezone.utc)
    await svc.audit(db, ctx.user, f"foto {body.status}", f"{pid} {p.art_navn}")
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- RSS


@router.get("/rss")
async def rss(q: str = Query("", max_length=80), kilde: int | None = None, land: str = Query("", max_length=60), page: int = Query(1, ge=1),
              ctx: svc.Ctx = Depends(need("netfugl_rss")), db: AsyncSession = Depends(get_session)):
    where, args = ["TRUE"], {}
    if q.strip():
        where.append("(i.title ILIKE :q OR i.summary ILIKE :q)")
        args["q"] = f"%{q.strip()}%"
    if kilde:
        where.append("f.id = :f")
        args["f"] = kilde
    if land:
        where.append("f.land = :land")
        args["land"] = land
    w = " AND ".join(where)
    total = (await db.execute(text(f"SELECT count(*) FROM nf_feed_items i JOIN nf_feeds f ON f.id = i.feed_id WHERE {w}"), args)).scalar()
    rows = _iso(await _rows(db, f"""SELECT i.id, i.title, i.url, i.summary, i.published_at, f.navn AS kilde, f.land FROM nf_feed_items i
        JOIN nf_feeds f ON f.id = i.feed_id WHERE {w} ORDER BY i.published_at DESC LIMIT 30 OFFSET :off""", **args, off=(page - 1) * 30))
    feeds = await _rows(db, "SELECT id, navn, land FROM nf_feeds ORDER BY navn")
    return {"total": total, "rows": rows, "kilder": feeds, "lande": sorted({f["land"] for f in feeds})}


# ---------------------------------------------------------------- krydslister


@router.get("/kryds/lister")
async def kryds_lister(ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    """Alle krydslister (som /ranking/alle)."""
    y = dk_today().year
    st = await svc.settings(db)
    years = list(range(y, y - int(st["year_lists"]), -1))
    aedel_years = [r[0] for r in (await db.execute(text(
        "SELECT DISTINCT extract(year FROM dato)::int FROM nf_kryds WHERE liste = 'aedel' AND dato IS NOT NULL ORDER BY 1 DESC"))).all()]
    return {
        "lister": [{"key": k, "name": v["name"], "auto": v["auto"], "manual": bool(v.get("manual")), "param": v.get("param"), "ref": v["ref"]}
                   for k, v in svc.LISTS.items()],
        "years": years, "aedel_years": aedel_years or [y], "months": svc.MONTHS,
        "afdelinger": await _rows(db, "SELECT id, navn FROM afdelinger ORDER BY navn"),
        "kommuner": await _rows(db, "SELECT kode, navn FROM kommuner ORDER BY navn"),
        "auto_at": await _auto_at(), "participants": st["participants"],
    }


async def _auto_at() -> str | None:
    from portal.cache import kv

    return await kv().get("nf:auto:at")


async def _data(db: AsyncSession, key: str) -> dict:
    try:
        return await svc.load_kryds(db, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/kryds/{key}/{view}")
async def kryds_view(key: str, view: str, aar: int | None = None, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    """Visninger: ranking, seneste, hvem, blockers, blockerstat, tophits (aar)."""
    data = await _data(db, key)
    base, _ = svc.parse_list(key)
    out = {"key": key, "name": data["name"], "deltagere": len(data["people"]), "auto_at": await _auto_at()}
    if view == "ranking":
        out["rows"] = svc.ranking(data)
    elif view == "seneste":
        out["rows"] = svc.latest(data, 100)
    elif view == "hvem":
        out.update(svc.who_has_what(data, await svc.reference(db, svc.LISTS[base]["ref"])))
    elif view in ("blockers", "blockerstat"):
        out.update(svc.blockers(data))
    elif view == "tophits":
        out["aar"] = aar or dk_today().year
        out["rows"] = svc.tophits(data, out["aar"])
        out["total"] = len({r["kode"] for r in data["rows"]})
    else:
        raise HTTPException(404)
    return out


@router.get("/kryds/{key}/art/{skey}")
async def kryds_art(key: str, skey: str, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    """Hvem har krydset arten på listen (med dato, lokalitet og evt. observationen i DOFbasen)."""
    data = await _data(db, key)
    rows = [{**r, "person": data["people"].get(r["kode"], r["kode"])} for r in data["rows"] if r["key"] == skey]
    rows.sort(key=lambda r: r["dato"] or "9999")
    return {"name": data["name"], "art": rows[0]["navn"] if rows else None, "rows": rows,
            "dofbasen": ctx.out()["features"]["netfugl_dofbasen"]}


async def kryds_oversigt_data(db: AsyncSession, n: int) -> dict:
    dk = await svc.load_kryds(db, "dk")
    vp = await svc.load_kryds(db, "vp")
    return {"dk": svc.latest(dk, n), "vp": svc.latest(vp, n), "dk350": svc.latest(dk, n, 350), "vp700": svc.latest(vp, n, 700)}


@router.get("/kryds/oversigt")
async def kryds_oversigt(ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    return {**await kryds_oversigt_data(db, 10), "auto_at": await _auto_at()}


@router.get("/mine-kryds/{liste}")
async def mine_kryds(liste: str, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    """Mine kryds på en liste (automatiske og manuelle) + skjulte automatiske kryds."""
    if liste not in ("dk", "vp", "falskvp", "ring", "aedel"):
        raise HTTPException(400)
    data = await svc.load_kryds(db, liste)
    rows = [r for r in data["rows"] if r["kode"] == ctx.user.obserkode]
    rows.sort(key=lambda r: r["dato"] or "")
    hidden = _iso(await _rows(db, "SELECT id, artnr, navn, latin FROM nf_kryds WHERE obserkode = :k AND liste = :l AND hidden",
                              k=ctx.user.obserkode, l=liste))
    participant = ctx.user.obserkode in data["people"]
    return {"rows": rows, "hidden": hidden, "participant": participant, "name": data["name"]}


class KrydsIn(BaseModel):
    liste: str = Field(pattern="^(dk|vp|falskvp|ring|aedel)$")
    species_id: int
    dato: date | None = None
    lokalitet: str = Field("", max_length=250)
    land: str = Field("", max_length=80)
    note: str = Field("", max_length=500)


@router.post("/mine-kryds")
async def nyt_kryds(body: KrydsIn, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    sp = await db.get(NfSpecies, body.species_id)
    ref = svc.LISTS[body.liste]["ref"]
    if sp is None or sp.liste != ref:
        raise HTTPException(400, "Arten findes ikke på listen")
    if body.dato and body.dato > dk_today():
        raise HTTPException(400, "Datoen ligger i fremtiden")
    if ref == "vp" and sp.su and not (body.dato and body.lokalitet and body.land):
        raise HTTPException(400, "VP-blockers (*) kræver land, lokalitet og dato")
    if ref == "dk" and sp.su and not (body.dato and body.lokalitet):
        raise HTTPException(400, "SU-arter kræver dato og lokalitet")
    await db.execute(delete(NfKryds).where(NfKryds.obserkode == ctx.user.obserkode, NfKryds.liste == body.liste,
                                           NfKryds.latin == sp.latin))
    db.add(NfKryds(obserkode=ctx.user.obserkode, liste=body.liste, artnr=sp.artnr, latin=sp.latin, navn=sp.navn, dato=body.dato,
                   lokalitet=body.lokalitet or None, land=body.land or ("Danmark" if ref == "dk" else None), note=body.note or None))
    await db.commit()
    await bump(svc.KRYDS_VERSION)
    return {"ok": True}


class HideIn(BaseModel):
    artnr: int
    hidden: bool = True


@router.post("/mine-kryds/skjul")
async def skjul_kryds(body: HideIn, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    """Skjul et automatisk DK-kryds (fx en fejlbestemmelse i DOFbasen) – eller vis det igen."""
    sp = (await db.execute(text("SELECT navn, latin FROM species WHERE artnr = :a"), {"a": body.artnr})).first()
    if sp is None:
        raise HTTPException(404)
    await db.execute(delete(NfKryds).where(NfKryds.obserkode == ctx.user.obserkode, NfKryds.liste == "dk", NfKryds.artnr == body.artnr,
                                           NfKryds.hidden))
    if body.hidden:
        db.add(NfKryds(obserkode=ctx.user.obserkode, liste="dk", artnr=body.artnr, latin=sp[1] or sp[0], navn=sp[0], hidden=True))
    await db.commit()
    await bump(svc.KRYDS_VERSION)
    return {"ok": True}


@router.delete("/mine-kryds/{kid}")
async def slet_kryds(kid: int, ctx: svc.Ctx = Depends(need("netfugl_kryds")), db: AsyncSession = Depends(get_session)):
    k = await db.get(NfKryds, kid)
    if k is None or (k.obserkode != ctx.user.obserkode and not ctx.can("kryds")):
        raise HTTPException(404)
    if k.obserkode != ctx.user.obserkode:
        await svc.audit(db, ctx.user, "slet kryds", f"{k.obserkode}: {k.navn} ({k.liste})")
    await db.delete(k)
    await db.commit()
    await bump(svc.KRYDS_VERSION)
    return {"ok": True}


# ---------------------------------------------------------------- arter


@router.get("/arter/familier")
async def familier(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _rows(db, "SELECT id, orden, familie, engelsk, antal FROM nf_families ORDER BY id")


@router.get("/arter/{liste}")
async def artsliste(liste: str, q: str = Query("", max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    if liste not in ("dk", "vp"):
        raise HTTPException(404)
    where, args = "liste = :l", {"l": liste}
    if q.strip():
        where += " AND (navn ILIKE :q OR latin ILIKE :q OR engelsk ILIKE :q)"
        args["q"] = f"%{q.strip()}%"
    rows = await _rows(db, f"SELECT id, sort, su, navn, latin, engelsk, kategori, artnr FROM nf_species WHERE {where} ORDER BY sort", **args)
    return {"rows": rows, "can_edit": ctx.can_edit("kryds")}


@router.get("/art/{sid}")
async def art(sid: int, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artsside: DK-fund fra DOFbasen (pr. måned og lokalafdeling; alle fund for sjældne arter), fotos og kryds."""
    sp = await db.get(NfSpecies, sid)
    if sp is None:
        raise HTTPException(404, "Arten findes ikke")
    out = {"id": sp.id, "liste": sp.liste, "su": sp.su, "navn": sp.navn, "latin": sp.latin, "engelsk": sp.engelsk, "kategori": sp.kategori,
           "artnr": sp.artnr, "dofbasen": ctx.out()["features"]["netfugl_dofbasen"]}
    if sp.artnr:
        async def produce():
            ids = [r[0] for r in (await db.execute(text("SELECT artnr FROM species WHERE artnr = :a OR list_artnr = :a"), {"a": sp.artnr})).all()]
            stat = (await db.execute(text("""SELECT count(*), count(DISTINCT (dato, loknr)), min(dato), max(dato)
                FROM observations WHERE artnr = ANY(:ids) AND hemmelig = 0"""), {"ids": ids})).first()
            months = await _rows(db, """SELECT extract(month FROM dato)::int AS m, count(DISTINCT (dato, loknr)) AS n FROM observations
                WHERE artnr = ANY(:ids) AND hemmelig = 0 GROUP BY 1 ORDER BY 1""", ids=ids)
            afd = await _rows(db, """SELECT a.navn, count(DISTINCT (o.dato, o.loknr)) AS n FROM observations o JOIN locations l ON l.loknr = o.loknr
                JOIN afdelinger a ON a.id = l.afdeling_id WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 GROUP BY a.navn ORDER BY n DESC""", ids=ids)
            fund = []
            if stat[1] and stat[1] <= 400:
                fund = _iso(await _rows(db, """SELECT DISTINCT ON (o.dato, o.loknr) o.dato, o.loknr, l.navn AS lokalitet, o.antal, o.obsid,
                    trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')) AS observator
                    FROM observations o LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN observers ob ON ob.obserkode = o.obserkode
                    WHERE o.artnr = ANY(:ids) AND o.hemmelig = 0 ORDER BY o.dato DESC, o.loknr, o.antal DESC""", ids=ids))
            return {"obs": stat[0], "fund": stat[1], "first": stat[2].isoformat() if stat[2] else None,
                    "last": stat[3].isoformat() if stat[3] else None, "months": months, "afdelinger": afd, "fundliste": fund}
        out["dofb"] = await cached(f"nf:art:{sp.artnr}:{dk_today()}", 6 * 3600, produce)
        if ctx.on("netfugl_fotos"):
            out["fotos"] = await photo_list(db, "(p.artnr = :a OR p.artnr IN (SELECT artnr FROM species WHERE list_artnr = :a))", a=sp.artnr)
    if ctx.on("netfugl_kryds"):
        key = "dk" if sp.liste == "dk" else "vp"
        data = await svc.load_kryds(db, key)
        skey = f"a{sp.artnr}" if key == "dk" and sp.artnr else f"l{sp.latin.lower()}"
        out["kryds"] = {"liste": key, "key": skey, "antal": sum(1 for r in data["rows"] if r["key"] == skey),
                        "deltagere": len({r["kode"] for r in data["rows"]})}
    return out


class SpeciesIn(BaseModel):
    liste: str = Field(pattern="^(dk|vp)$")
    navn: str = Field(min_length=1, max_length=200)
    latin: str = Field(min_length=1, max_length=200)
    engelsk: str = Field("", max_length=200)
    kategori: str = Field("", max_length=4)
    su: bool = False
    sort: int = 0


@router.put("/art/{sid}")
async def ret_art(sid: int, body: SpeciesIn, ctx: svc.Ctx = Depends(need(perm="kryds")), db: AsyncSession = Depends(get_session)):
    sp = await db.get(NfSpecies, sid) if sid else None
    if sp is None:
        sp = NfSpecies(liste=body.liste)
        db.add(sp)
    sp.navn, sp.latin, sp.engelsk, sp.kategori, sp.su, sp.sort = body.navn, body.latin, body.engelsk or None, body.kategori or None, body.su, body.sort
    sp.artnr = (await db.execute(text("SELECT artnr FROM species WHERE lower(latin) = lower(:l) ORDER BY art_type = 'art' DESC LIMIT 1"),
                                 {"l": body.latin})).scalar()
    await svc.audit(db, ctx.user, "art", f"{body.liste}: {body.navn}")
    await db.commit()
    await bump(svc.KRYDS_VERSION)
    return {"id": sp.id}


@router.delete("/art/{sid}")
async def slet_art(sid: int, ctx: svc.Ctx = Depends(need(perm="kryds")), db: AsyncSession = Depends(get_session)):
    sp = await db.get(NfSpecies, sid)
    if sp:
        await svc.audit(db, ctx.user, "slet art", f"{sp.liste}: {sp.navn}")
        await db.delete(sp)
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- brugere


@router.get("/brugere")
async def brugere(q: str = Query("", max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    people = await svc.participants(db)
    counts = {}
    if ctx.on("netfugl_kryds"):
        for key in ("dk", "vp"):
            for r in svc.ranking(await svc.load_kryds(db, key)):
                counts.setdefault(r["kode"], {})[key] = r["antal"]
    photos = {r["obserkode"]: r["n"] for r in await _rows(db, """SELECT u.obserkode, count(*) AS n FROM nf_photos p JOIN users u ON u.id = p.user_id
        WHERE p.status = 'publiceret' GROUP BY u.obserkode""")}
    rows = [{"kode": k, "navn": n, "dk": counts.get(k, {}).get("dk", 0), "vp": counts.get(k, {}).get("vp", 0), "fotos": photos.get(k, 0)}
            for k, n in people.items() if not q.strip() or q.lower() in f"{n} {k}".lower()]
    rows.sort(key=lambda r: r["navn"].lower())
    return rows


@router.get("/brugere/{kode}")
async def bruger(kode: str, ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    kode = kode.upper()[:16]
    people = await svc.participants(db)
    u = (await db.execute(select(User).where(User.obserkode == kode))).scalar_one_or_none()
    if kode not in people and u is None:
        raise HTTPException(404, "Brugeren findes ikke")
    out = {"kode": kode, "navn": people.get(kode) or (u.navn if u else kode), "deltager": kode in people,
           "dofbasen": ctx.out()["features"]["netfugl_dofbasen"], "lister": []}
    if ctx.on("netfugl_kryds") and kode in people:
        for key in ("dk", "vp", "ring", "aedel", "falskvp", f"aar:{dk_today().year}"):
            data = await svc.load_kryds(db, key)
            mine = [r for r in data["rows"] if r["kode"] == kode]
            if not mine:
                continue
            bl = svc.blockers(data)
            bkeys = {b["key"]: b["antal"] for b in bl["rows"]}
            out["lister"].append({"key": key, "name": data["name"], "antal": len(mine),
                                  "blockers": sorted([{"navn": r["navn"], "antal": bkeys[r["key"]]} for r in mine if r["key"] in bkeys],
                                                     key=lambda b: b["antal"])})
    if u:
        st = (await db.execute(text("""SELECT count(*), max(published_at), coalesce(sum(views), 0), coalesce(max(views), 0)
            FROM nf_photos WHERE user_id = :u AND status = 'publiceret'"""), {"u": u.id})).first()
        out["fotos"] = {"antal": st[0], "seneste": st[1].isoformat() if st[1] else None, "visninger": st[2], "hoejeste": st[3], "user_id": u.id}
        out["seneste_fotos"] = await photo_list(db, "p.user_id = :u", u=u.id, limit=8) if ctx.on("netfugl_fotos") else []
    return out


# ---------------------------------------------------------------- søg på Netfugl


@router.get("/soeg")
async def soeg(q: str = Query(min_length=2, max_length=80), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    like = f"%{q.strip()}%"
    out = {"arter": await _rows(db, """SELECT id, liste, navn, latin, su FROM nf_species WHERE navn ILIKE :q OR latin ILIKE :q OR engelsk ILIKE :q
        ORDER BY liste, sort LIMIT 30""", q=like)}
    if ctx.on("netfugl_artikler"):
        out["artikler"] = _iso(await _rows(db, """SELECT id, title, dato, kind FROM nf_articles WHERE published AND (title ILIKE :q OR body ILIKE :q)
            ORDER BY dato DESC LIMIT 20""", q=like))
    if ctx.on("netfugl_fotos"):
        out["fotos"] = await photo_list(db, "(p.art_navn ILIKE :q OR p.lokalitet ILIKE :q OR u.navn ILIKE :q)", limit=12, q=like)
    if ctx.on("netfugl_rss"):
        out["rss"] = _iso(await _rows(db, """SELECT i.title, i.url, i.published_at, f.navn AS kilde FROM nf_feed_items i JOIN nf_feeds f ON f.id = i.feed_id
            WHERE i.title ILIKE :q OR i.summary ILIKE :q ORDER BY i.published_at DESC LIMIT 15""", q=like))
    out["brugere"] = [{"kode": k, "navn": n} for k, n in (await svc.participants(db)).items() if q.lower() in f"{n} {k}".lower()][:20]
    return out


# ---------------------------------------------------------------- admin


@router.get("/admin/oversigt")
async def admin_oversigt(ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    if not ctx.perms:
        raise HTTPException(403, "Kræver en Netfugl-rolle")
    counts = (await _rows(db, """SELECT (SELECT count(*) FROM nf_articles) AS artikler, (SELECT count(*) FROM nf_comments) AS kommentarer,
        (SELECT count(*) FROM nf_photos WHERE status = 'publiceret') AS fotos, (SELECT count(*) FROM nf_photos WHERE status = 'afventer') AS afventer,
        (SELECT count(*) FROM nf_feeds WHERE active) AS kilder, (SELECT count(*) FROM nf_feed_items) AS rss,
        (SELECT count(DISTINCT obserkode) FROM nf_auto) AS deltagere, (SELECT count(*) FROM nf_auto) AS auto_kryds,
        (SELECT count(*) FROM nf_kryds) AS manuelle_kryds, (SELECT count(*) FROM nf_species WHERE liste = 'dk') AS dk_arter,
        (SELECT count(*) FROM nf_species WHERE liste = 'vp') AS vp_arter, (SELECT count(*) FROM nf_user_roles) AS roller,
        (SELECT count(*) FROM nf_api_grants) AS api_brugere, (SELECT coalesce(sum(uses), 0) FROM nf_api_tokens) AS api_kald"""))[0]
    log = _iso(await _rows(db, """SELECT a.at, a.action, a.target, u.navn, u.obserkode FROM nf_audit a LEFT JOIN users u ON u.id = a.user_id
        ORDER BY a.at DESC LIMIT 40"""))
    flags = await features.load(db)
    return {"counts": counts, "log": log, "auto_at": await _auto_at(), "settings": await svc.settings(db),
            "features": [{"key": k, "name": v[0], "description": v[1], "state": flags[k]} for k, v in features.FEATURES.items() if k.startswith("netfugl")]}


class SettingsIn(BaseModel):
    participants: str = Field(pattern="^(brugere|aktive)$")
    aktive_max: int = Field(300, ge=10, le=2000)
    photo_moderation: bool = True
    year_lists: int = Field(5, ge=1, le=30)


@router.put("/admin/indstillinger")
async def gem_indstillinger(body: SettingsIn, ctx: svc.Ctx = Depends(need(perm="kryds")), db: AsyncSession = Depends(get_session)):
    row = await db.get(Setting, svc.SETTINGS_KEY)
    if row is None:
        db.add(Setting(key=svc.SETTINGS_KEY, value=body.model_dump()))
    else:
        row.value = body.model_dump()
    await svc.audit(db, ctx.user, "indstillinger", str(body.model_dump()))
    await db.commit()
    await worker_command("netfugl_kryds")
    return {"ok": True}


@router.post("/admin/beregn")
async def beregn(ctx: svc.Ctx = Depends(need(perm="kryds"))):
    await worker_command("netfugl_kryds")
    return {"queued": True}


@router.get("/admin/fotos")
async def admin_fotos(status: str = Query("afventer", pattern="^(afventer|afvist|publiceret)$"), ctx: svc.Ctx = Depends(need("netfugl_fotos", "fotos")),
                      db: AsyncSession = Depends(get_session)):
    return _iso(await _rows(db, f"""SELECT {PHOTO_COLS} FROM nf_photos p JOIN users u ON u.id = p.user_id WHERE p.status = :s
        ORDER BY p.created_at DESC LIMIT 200""", s=status))


@router.get("/admin/kommentarer")
async def admin_kommentarer(ctx: svc.Ctx = Depends(need(perm="kommentarer")), db: AsyncSession = Depends(get_session)):
    return _iso(await _rows(db, """SELECT c.id, c.target_kind, c.target_id, c.body, c.hidden, c.created_at, coalesce(u.navn, u.obserkode) AS navn,
        CASE WHEN c.target_kind = 'artikel' THEN (SELECT title FROM nf_articles WHERE id = c.target_id)
             ELSE (SELECT art_navn FROM nf_photos WHERE id = c.target_id) END AS emne
        FROM nf_comments c JOIN users u ON u.id = c.user_id ORDER BY c.created_at DESC LIMIT 200"""))


class FeedIn(BaseModel):
    navn: str = Field(min_length=1, max_length=160)
    site_url: str = Field("", max_length=400)
    feed_url: str = Field("", max_length=400)
    land: str = Field("Danmark", max_length=60)
    active: bool = True


@router.get("/admin/rss")
async def admin_rss(ctx: svc.Ctx = Depends(need("netfugl_rss", "rss")), db: AsyncSession = Depends(get_session)):
    return _iso(await _rows(db, "SELECT id, navn, site_url, feed_url, land, active, last_fetch, last_error, items FROM nf_feeds ORDER BY navn"))


@router.put("/admin/rss/{fid}")
async def gem_feed(fid: int, body: FeedIn, ctx: svc.Ctx = Depends(need("netfugl_rss", "rss")), db: AsyncSession = Depends(get_session)):
    for u in (body.site_url, body.feed_url):
        if u and not u.startswith(("http://", "https://")):
            raise HTTPException(400, "Adresser skal starte med http:// eller https://")
    f = await db.get(NfFeed, fid) if fid else None
    if f is None:
        f = NfFeed()
        db.add(f)
    f.navn, f.site_url, f.feed_url, f.land, f.active = body.navn, body.site_url or None, body.feed_url or None, body.land or "Danmark", body.active
    await svc.audit(db, ctx.user, "rss-kilde", body.navn)
    await db.commit()
    return {"id": f.id}


@router.delete("/admin/rss/{fid}")
async def slet_feed(fid: int, ctx: svc.Ctx = Depends(need("netfugl_rss", "rss")), db: AsyncSession = Depends(get_session)):
    f = await db.get(NfFeed, fid)
    if f:
        await svc.audit(db, ctx.user, "slet rss-kilde", f.navn)
        await db.delete(f)
        await db.commit()
    return {"ok": True}


@router.post("/admin/rss/hent")
async def hent_rss(fid: int | None = None, ctx: svc.Ctx = Depends(need("netfugl_rss", "rss"))):
    await worker_command("netfugl_rss", feed=fid)
    return {"queued": True}


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    perms: list[str] = Field(default_factory=list)


@router.get("/admin/roller")
async def admin_roller(ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    roles = [{"key": r.key, "name": r.name, "perms": r.perms} for r in (await db.execute(select(NfRole).order_by(NfRole.sort, NfRole.name))).scalars()]
    people = _iso(await _rows(db, """SELECT u.id, u.navn, u.obserkode, array_agg(ur.role_key ORDER BY ur.role_key) AS roles, max(ur.granted_at) AS granted_at
        FROM nf_user_roles ur JOIN users u ON u.id = ur.user_id GROUP BY u.id ORDER BY u.navn"""))
    return {"roles": roles, "people": people, "perms": svc.PERMS}


@router.post("/admin/roller")
async def ny_rolle(body: RoleIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    key = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_")[:28] or "rolle"
    base, i = key, 2
    while await db.get(NfRole, key):
        key, i = f"{base}_{i}", i + 1
    db.add(NfRole(key=key, name=body.name, perms=[p for p in body.perms if p in svc.PERMS]))
    await svc.audit(db, ctx.user, "ny rolle", body.name)
    await db.commit()
    return {"key": key}


@router.patch("/admin/roller/{key}")
async def ret_rolle(key: str, body: RoleIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    r = await db.get(NfRole, key)
    if r is None:
        raise HTTPException(404)
    r.name, r.perms = body.name, [p for p in body.perms if p in svc.PERMS]
    await svc.audit(db, ctx.user, "rolle", f"{body.name}: {', '.join(r.perms) or 'ingen'}")
    await db.commit()
    return {"ok": True}


@router.delete("/admin/roller/{key}")
async def slet_rolle(key: str, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    if key == "admin":
        raise HTTPException(400, "Administratorrollen kan ikke slettes")
    await db.execute(delete(NfRole).where(NfRole.key == key))
    await svc.audit(db, ctx.user, "slet rolle", key)
    await db.commit()
    return {"ok": True}


@router.get("/admin/brugere")
async def admin_brugere(q: str = Query(min_length=2, max_length=60), ctx: svc.Ctx = Depends(need()), db: AsyncSession = Depends(get_session)):
    if not (ctx.can("roller") or ctx.can("api")):
        raise HTTPException(403)
    return await _rows(db, """SELECT u.id, u.navn, u.obserkode,
        coalesce((SELECT array_agg(role_key) FROM nf_user_roles WHERE user_id = u.id), '{}') AS roles,
        EXISTS (SELECT 1 FROM nf_api_grants g WHERE g.user_id = u.id) AS api
        FROM users u WHERE (u.navn ILIKE :q OR u.obserkode ILIKE :q) AND u.approved AND NOT u.blocked ORDER BY u.navn LIMIT 20""", q=f"%{q}%")


class UserRolesIn(BaseModel):
    roles: list[str] = Field(max_length=20)


@router.put("/admin/brugere/{user_id}/roller")
async def saet_roller(user_id: int, body: UserRolesIn, ctx: svc.Ctx = Depends(need(perm="roller")), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    await svc.set_user_roles(db, user_id, body.roles, ctx.user)
    await svc.audit(db, ctx.user, "roller", f"{target.obserkode}: {', '.join(body.roles) or 'ingen'}")
    await db.commit()
    return {"ok": True}


class GrantIn(BaseModel):
    rate_per_min: int = Field(60, ge=1, le=6000)
    max_tokens: int = Field(5, ge=1, le=50)
    note: str = Field("", max_length=300)


@router.get("/admin/api")
async def admin_api(ctx: svc.Ctx = Depends(need("netfugl_api", "api")), db: AsyncSession = Depends(get_session)):
    grants = _iso(await _rows(db, """SELECT g.user_id, u.navn, u.obserkode, g.rate_per_min, g.max_tokens, g.note, g.granted_at, gb.navn AS granted_by,
        (SELECT count(*) FROM nf_api_tokens t WHERE t.user_id = g.user_id AND t.revoked_at IS NULL) AS tokens,
        (SELECT coalesce(sum(uses), 0) FROM nf_api_tokens t WHERE t.user_id = g.user_id) AS uses,
        (SELECT max(last_used_at) FROM nf_api_tokens t WHERE t.user_id = g.user_id) AS last_used
        FROM nf_api_grants g JOIN users u ON u.id = g.user_id LEFT JOIN users gb ON gb.id = g.granted_by ORDER BY u.navn"""))
    tokens = _iso(await _rows(db, """SELECT t.id, t.user_id, u.obserkode, t.name, t.prefix, t.created_at, t.last_used_at, t.uses, t.revoked_at
        FROM nf_api_tokens t JOIN users u ON u.id = t.user_id ORDER BY t.created_at DESC LIMIT 300"""))
    return {"grants": grants, "tokens": tokens}


@router.put("/admin/api/{user_id}")
async def giv_api(user_id: int, body: GrantIn, ctx: svc.Ctx = Depends(need("netfugl_api", "api")), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    g = await db.get(NfApiGrant, user_id)
    if g is None:
        g = NfApiGrant(user_id=user_id, granted_by=ctx.user.id)
        db.add(g)
    g.rate_per_min, g.max_tokens, g.note = body.rate_per_min, body.max_tokens, body.note or None
    await svc.audit(db, ctx.user, "API-adgang", f"{target.obserkode} ({body.rate_per_min}/min)")
    await db.commit()
    return {"ok": True}


@router.delete("/admin/api/{user_id}")
async def fjern_api(user_id: int, ctx: svc.Ctx = Depends(need("netfugl_api", "api")), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(NfApiGrant).where(NfApiGrant.user_id == user_id))
    await db.execute(update(NfApiToken).where(NfApiToken.user_id == user_id, NfApiToken.revoked_at.is_(None)).values(revoked_at=func.now()))
    await svc.audit(db, ctx.user, "fjern API-adgang", str(user_id))
    await db.commit()
    return {"ok": True}


@router.delete("/admin/api/tokens/{token_id}")
async def spaer_token(token_id: int, ctx: svc.Ctx = Depends(need("netfugl_api", "api")), db: AsyncSession = Depends(get_session)):
    await db.execute(update(NfApiToken).where(NfApiToken.id == token_id, NfApiToken.revoked_at.is_(None)).values(revoked_at=func.now()))
    await svc.audit(db, ctx.user, "spær token", str(token_id))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- mine API-nøgler


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("/me/api")
async def mine_tokens(ctx: svc.Ctx = Depends(need("netfugl_api")), db: AsyncSession = Depends(get_session)):
    g = await db.get(NfApiGrant, ctx.user.id)
    tokens = [{"id": t.id, "name": t.name, "prefix": t.prefix, "created_at": t.created_at, "last_used_at": t.last_used_at, "uses": t.uses,
               "revoked_at": t.revoked_at}
              for t in (await db.execute(select(NfApiToken).where(NfApiToken.user_id == ctx.user.id).order_by(NfApiToken.created_at.desc()))).scalars()]
    return {"access": g is not None, "rate_per_min": g.rate_per_min if g else None, "max_tokens": g.max_tokens if g else None, "tokens": tokens}


@router.post("/me/api/tokens")
async def nyt_token(body: TokenIn, ctx: svc.Ctx = Depends(need("netfugl_api")), db: AsyncSession = Depends(get_session)):
    g = await db.get(NfApiGrant, ctx.user.id)
    if g is None:
        raise HTTPException(403, "Du har ikke adgang til API'et. En administrator skal give dig adgang.")
    active = (await db.execute(select(func.count()).select_from(NfApiToken)
                               .where(NfApiToken.user_id == ctx.user.id, NfApiToken.revoked_at.is_(None)))).scalar()
    if active >= g.max_tokens:
        raise HTTPException(400, f"Du kan højst have {g.max_tokens} aktive nøgler. Slet en gammel først.")
    token, prefix, digest = dofb.new_token("nf_")
    t = NfApiToken(user_id=ctx.user.id, name=body.name, prefix=prefix, token_hash=digest)
    db.add(t)
    await svc.audit(db, ctx.user, "nyt token", body.name)
    await db.commit()
    return {"id": t.id, "token": token, "prefix": prefix, "name": t.name}


@router.delete("/me/api/tokens/{token_id}")
async def slet_mit_token(token_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await db.execute(update(NfApiToken).where(NfApiToken.id == token_id, NfApiToken.user_id == user.id, NfApiToken.revoked_at.is_(None))
                     .values(revoked_at=func.now()))
    await db.commit()
    return {"ok": True}
