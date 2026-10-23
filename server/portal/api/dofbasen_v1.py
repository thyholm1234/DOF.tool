"""DOFbasen API v1 – til integration i egne apps.

Godkendelse: ``Authorization: Bearer dofb_…``. En administrator giver adgang (DOFbasen → Admin → API), og brugeren laver
selv sine tokens på sin profil. Kun læsning (GET). CORS er åben, så API'et kan bruges direkte fra browserapps.
Hemmelige observationer udleveres kun til den observatør, de tilhører. Grænse: grantens antal kald pr. minut pr. token.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.db import get_session
from portal.models import DofbApiGrant, DofbApiToken, DofbArea, DofbList, DofbNews, User
from portal.services import dofbasen as svc
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/dofbasen/v1", tags=["DOFbasen API v1"])


class Client:
    def __init__(self, user: User, token: DofbApiToken, grant: DofbApiGrant):
        self.user, self.token, self.grant = user, token, grant


async def client(request: Request, db: AsyncSession = Depends(get_session)) -> Client:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Mangler Authorization: Bearer <token>", headers={"WWW-Authenticate": "Bearer"})
    tok = (await db.execute(select(DofbApiToken).where(DofbApiToken.token_hash == svc.hash_token(auth[7:].strip()),
                                                       DofbApiToken.revoked_at.is_(None)))).scalar_one_or_none()
    if tok is None:
        raise HTTPException(401, "Ugyldigt eller spærret token", headers={"WWW-Authenticate": "Bearer"})
    user = await db.get(User, tok.user_id)
    grant = await db.get(DofbApiGrant, tok.user_id)
    if user is None or user.blocked or not user.approved or grant is None:
        raise HTTPException(403, "Brugeren har ikke (længere) adgang til API'et")
    ctx = await svc.context(db, user)
    if not ctx.on("dofbasen_api"):
        raise HTTPException(404, "API'et er ikke slået til")
    # Grænse pr. token pr. minut
    key = f"dofb:rate:{tok.id}:{int(datetime.now(timezone.utc).timestamp() // 60)}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 70)
    if n > grant.rate_per_min:
        raise HTTPException(429, f"For mange kald – højst {grant.rate_per_min} i minuttet", headers={"Retry-After": "60"})
    await db.execute(update(DofbApiToken).where(DofbApiToken.id == tok.id)
                     .values(uses=DofbApiToken.uses + 1, last_used_at=datetime.now(timezone.utc)))
    await db.commit()
    return Client(user, tok, grant)


async def _rows(db: AsyncSession, sql: str, **args) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql), args)).mappings().all()]


@router.get("/me", summary="Hvem er jeg")
async def me(c: Client = Depends(client)):
    return {"obserkode": c.user.obserkode, "navn": c.user.navn, "token": {"name": c.token.name, "prefix": c.token.prefix},
            "rate_per_min": c.grant.rate_per_min}


@router.get("/observations", summary="Søg observationer",
            description="Samme filtre som søgesiden (periode, område, art, antal, adfærd …). Højst 500 pr. side.")
async def observations(p: Annotated[svc.SearchIn, Query()], c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return await svc.search(db, p, c.user.obserkode)


@router.get("/observations/{obsid}", summary="Én observation")
async def observation(obsid: int, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    rows = await _rows(db, f"""SELECT {svc.ROW_COLS} FROM observations o JOIN species s ON s.artnr = o.artnr
        LEFT JOIN locations l ON l.loknr = o.loknr LEFT JOIN trips t ON t.turid = o.turid {svc.ROW_JOINS}
        WHERE o.obsid = :id AND (o.hemmelig = 0 OR o.obserkode = :me)""", id=obsid, me=c.user.obserkode, sub_as_species=False)
    if not rows:
        raise HTTPException(404, "Observationen findes ikke")
    return rows[0]


@router.get("/days/{dato}", summary="Observationer på en dag",
            description="visning: fugle (bemærkelsesværdige), allfugle, sufugle (SU og SUB). afdeling: 574–586.")
async def day(dato: date, visning: str = Query("fugle", pattern="^(fugle|allfugle|sufugle)$"), afdeling: int | None = None,
              c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    cond = {"fugle": "o.kategori <> 'alm'", "allfugle": "TRUE", "sufugle": "o.kategori IN ('SU', 'SUB')"}[visning]
    rows = await _rows(db, f"""
        SELECT o.obsid, o.turid, o.artnr, s.navn AS art, o.antal, o.adfkode, o.kategori, o.loknr, l.navn AS lokalitet,
               l.afdeling_id, o.obserkode, coalesce(o.obs_lat, l.lat) AS lat, coalesce(o.obs_lon, l.lon) AS lon
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE o.dato = :d AND {cond} AND (o.hemmelig = 0 OR o.obserkode = :me) AND (CAST(:a AS integer) IS NULL OR l.afdeling_id = :a)
        ORDER BY s.sortering, l.navn LIMIT 10000""", d=dato, me=c.user.obserkode, a=afdeling)
    return {"dato": dato.isoformat(), "count": len(rows), "rows": rows}


@router.get("/rare", summary="Seneste SU- og SUB-fund")
async def rare(days: int = Query(7, ge=1, le=60), c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return await _rows(db, """
        SELECT DISTINCT ON (o.dato, o.artnr, o.loknr) o.dato, o.artnr, s.navn AS art, o.kategori, o.loknr, l.navn AS lokalitet,
               max(o.antal) OVER (PARTITION BY o.dato, o.artnr, o.loknr) AS max_antal, o.obsid
        FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr
        WHERE o.dato > :d AND o.kategori IN ('SU', 'SUB') AND o.hemmelig = 0
        ORDER BY o.dato DESC, o.artnr, o.loknr, o.obsid""", d=dk_today() - timedelta(days=days))


@router.get("/stats/daily", summary="Antal observationer pr. dag")
async def daily(days: int = Query(20, ge=1, le=90), c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return await _rows(db, "SELECT dato, count(*) AS n FROM observations WHERE dato > :d GROUP BY dato ORDER BY dato",
                       d=dk_today() - timedelta(days=days))


@router.get("/species", summary="Artsliste")
async def species(c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return await _rows(db, "SELECT artnr, navn, latin, art_type, klass, list_artnr FROM species WHERE NOT excluded ORDER BY sortering")


@router.get("/species/{artnr}", summary="Én art")
async def species_one(artnr: int, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    rows = await _rows(db, "SELECT artnr, navn, latin, art_type, klass, list_artnr FROM species WHERE artnr = :a", a=artnr)
    if not rows:
        raise HTTPException(404, "Arten findes ikke")
    return rows[0]


@router.get("/locations", summary="Lokaliteter", description="Filtrér på navn (q), lokalafdeling eller kommunekode. Højst 1000.")
async def locations(q: str = Query("", max_length=60), afdeling: int | None = None, kommune: str | None = Query(None, max_length=4),
                    c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return await _rows(db, """
        SELECT loknr, navn, afdeling_id, kommune_kode, lat, lon FROM locations
        WHERE (:q = '' OR navn ILIKE :ql) AND (CAST(:a AS integer) IS NULL OR afdeling_id = :a) AND (CAST(:k AS text) IS NULL OR kommune_kode = :k)
        ORDER BY navn LIMIT 1000""", q=q, ql=f"%{q}%", a=afdeling, k=kommune)


@router.get("/locations/{loknr}", summary="Én lokalitet")
async def location(loknr: int, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    rows = await _rows(db, "SELECT loknr, navn, afdeling_id, kommune_kode, lat, lon FROM locations WHERE loknr = :l", l=loknr)
    if not rows:
        raise HTTPException(404, "Lokaliteten findes ikke")
    return rows[0]


@router.get("/areas", summary="Storlokaliteter og IBA-områder")
async def areas(c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return [{"id": a.id, "kind": a.kind, "navn": a.navn, "loknr": a.loknrs} for a in (await db.execute(select(DofbArea).order_by(DofbArea.kind, DofbArea.navn))).scalars()]


@router.get("/news", summary="Nyt på DOFbasen")
async def news(c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    q = select(DofbNews).where(DofbNews.published).order_by(DofbNews.dato.desc())
    return [{"id": n.id, "dato": n.dato.isoformat(), "title": n.title, "body": n.body} for n in (await db.execute(q)).scalars()]


@router.get("/lists", summary="Opslagslister")
async def lists(c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    return [{"key": x.key, "title": x.title} for x in (await db.execute(select(DofbList).order_by(DofbList.key))).scalars()]


@router.get("/lists/{key}", summary="Én opslagsliste (SU-arter, adfærdskoder …)")
async def list_one(key: str, c: Client = Depends(client), db: AsyncSession = Depends(get_session)):
    x = await db.get(DofbList, key)
    if x is None:
        raise HTTPException(404, "Listen findes ikke")
    return {"key": x.key, "title": x.title, "columns": x.columns, "rows": x.rows}
