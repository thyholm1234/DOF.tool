"""Netfugl API v1 – artikler, krydslister, fotos, RSS og artslister til egne apps.

Godkendelse: ``Authorization: Bearer nf_…``. En administrator giver adgang (Netfugl → Admin → API-adgang), og brugeren laver selv
sine nøgler. Kun læsning (GET), åben CORS, grænse pr. nøgle pr. minut.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.db import get_session
from portal.models import NfApiGrant, NfApiToken, NfArticle, User
from portal.services import dofbasen as dofb
from portal.services import netfugl as svc
from portal.services.netfugl_content import iso as _iso
from portal.services.netfugl_content import photo_list
from portal.services.netfugl_content import rows as _rows

router = APIRouter(prefix="/api/netfugl/v1", tags=["Netfugl API v1"])


class Client:
    def __init__(self, user: User, token: NfApiToken, grant: NfApiGrant, ctx: svc.Ctx):
        self.user, self.token, self.grant, self.ctx = user, token, grant, ctx


async def client(request: Request, db: AsyncSession = Depends(get_session)) -> Client:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Mangler Authorization: Bearer <token>", headers={"WWW-Authenticate": "Bearer"})
    tok = (await db.execute(select(NfApiToken).where(NfApiToken.token_hash == dofb.hash_token(auth[7:].strip()),
                                                     NfApiToken.revoked_at.is_(None)))).scalar_one_or_none()
    if tok is None:
        raise HTTPException(401, "Ugyldigt eller spærret token", headers={"WWW-Authenticate": "Bearer"})
    user = await db.get(User, tok.user_id)
    grant = await db.get(NfApiGrant, tok.user_id)
    if user is None or user.blocked or not user.approved or grant is None:
        raise HTTPException(403, "Brugeren har ikke (længere) adgang til API'et")
    ctx = await svc.context(db, user)
    if not ctx.on("netfugl_api"):
        raise HTTPException(404, "API'et er ikke slået til")
    key = f"nf:rate:{tok.id}:{int(datetime.now(timezone.utc).timestamp() // 60)}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 70)
    if n > grant.rate_per_min:
        raise HTTPException(429, f"For mange kald – højst {grant.rate_per_min} i minuttet", headers={"Retry-After": "60"})
    await db.execute(update(NfApiToken).where(NfApiToken.id == tok.id).values(uses=NfApiToken.uses + 1, last_used_at=datetime.now(timezone.utc)))
    await db.commit()
    return Client(user, tok, grant, ctx)


def _need(c: Client, feature: str) -> None:
    if not c.ctx.on(feature):
        raise HTTPException(404, "Funktionen er ikke slået til")


@router.get("/me", summary="Hvem er jeg")
async def me(c: Client = Depends(client)):
    return {"obserkode": c.user.obserkode, "navn": c.user.navn, "token": {"name": c.token.name, "prefix": c.token.prefix},
            "rate_per_min": c.grant.rate_per_min}


@router.get("/articles", summary="Nyheder og artikler", description="kind: artikel eller kort. q søger i titel og tekst.")
async def articles(q: str = Query("", max_length=80), kind: str = Query("", pattern="^(|artikel|kort)$"), page: int = Query(1, ge=1),
                   c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_artikler")
    return _iso(await _rows(db, """SELECT id, kind, title, dato, writers, tags, source_url FROM nf_articles
        WHERE published AND (:q = '' OR title ILIKE :ql OR body ILIKE :ql) AND (:k = '' OR kind = :k)
        ORDER BY dato DESC, id DESC LIMIT 50 OFFSET :off""", q=q, ql=f"%{q}%", k=kind, off=(page - 1) * 50))


@router.get("/articles/{aid}", summary="Én artikel med tekst")
async def article(aid: int, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_artikler")
    a = await db.get(NfArticle, aid)
    if a is None or not a.published:
        raise HTTPException(404, "Artiklen findes ikke")
    return {"id": a.id, "kind": a.kind, "title": a.title, "body": a.body, "writers": a.writers, "tags": a.tags, "dato": a.dato.isoformat(),
            "source_url": a.source_url}


@router.get("/photos", summary="Fotos (metadata og adresse)", description="art = artnr i DOFbasen. sort: nyeste eller likes.")
async def photos(art: int | None = None, sort: str = Query("nyeste", pattern="^(nyeste|likes)$"), page: int = Query(1, ge=1),
                 c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_fotos")
    where, args = ("p.artnr = :a", {"a": art}) if art else ("TRUE", {})
    rows = await photo_list(db, where, "likes DESC, p.published_at DESC" if sort == "likes" else "p.published_at DESC", 50, (page - 1) * 50, **args)
    return [{k: v for k, v in r.items() if k != "user_id"} for r in rows]


@router.get("/rss", summary="Netfugl live (RSS-indslag)")
async def rss(land: str = Query("", max_length=60), c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_rss")
    return _iso(await _rows(db, """SELECT i.title, i.url, i.published_at, f.navn AS kilde, f.land FROM nf_feed_items i JOIN nf_feeds f ON f.id = i.feed_id
        WHERE (:land = '' OR f.land = :land) ORDER BY i.published_at DESC LIMIT 100""", land=land))


@router.get("/lists", summary="Krydslister", description="Nøgler: dk, aar:<år>, md:<1-12>, afd:<574-586>, kom:<kode>, vp, falskvp, ring, aedel, aedelaar:<år>")
async def lists(c: Client = Depends(client)):
    _need(c, "netfugl_kryds")
    return [{"key": k, "name": v["name"], "auto": v["auto"], "param": v.get("param")} for k, v in svc.LISTS.items()]


async def _data(db: AsyncSession, key: str) -> dict:
    try:
        return await svc.load_kryds(db, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/lists/{key}/ranking", summary="Rangliste for en krydsliste")
async def ranking(key: str, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_kryds")
    data = await _data(db, key)
    return {"name": data["name"], "rows": svc.ranking(data)}


@router.get("/lists/{key}/latest", summary="Seneste kryds")
async def latest(key: str, n: int = Query(100, ge=1, le=500), c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_kryds")
    data = await _data(db, key)
    return {"name": data["name"], "rows": svc.latest(data, n)}


@router.get("/lists/{key}/blockers", summary="Blockers (arter set af højst 10 % af deltagerne)")
async def blockers(key: str, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_kryds")
    return svc.blockers(await _data(db, key))


@router.get("/species/{liste}", summary="DK-listen eller VP-listen")
async def species(liste: str, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    if liste not in ("dk", "vp"):
        raise HTTPException(404)
    return await _rows(db, "SELECT id, su, navn, latin, engelsk, kategori, artnr FROM nf_species WHERE liste = :l ORDER BY sort", l=liste)


@router.get("/users/{kode}", summary="En deltagers krydslister")
async def user(kode: str, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    _need(c, "netfugl_kryds")
    kode = kode.upper()
    people = await svc.participants(db)
    if kode not in people:
        raise HTTPException(404, "Ikke deltager i krydslisterne")
    out = {"obserkode": kode, "navn": people[kode], "lister": {}}
    for key in ("dk", "vp", "ring", "aedel"):
        data = await svc.load_kryds(db, key)
        out["lister"][key] = sorted(({"navn": r["navn"], "latin": r["latin"], "dato": r["dato"], "lokalitet": r["lok"]}
                                     for r in data["rows"] if r["kode"] == kode), key=lambda r: r["dato"] or "")
    return out
