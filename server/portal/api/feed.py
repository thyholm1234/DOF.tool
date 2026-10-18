from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import kv, publish
from portal.db import get_session
from portal.dof import gateway
from portal.dof.jobs import media_for
from portal.models import Area, ThreadComment, ThreadFollow, User
from portal.api.social import like_summary, notify_comment
from portal.services import activity, feed, reports
from portal.services.areas import Scope, parse_scope
from portal.timeutil import dk_today

router = APIRouter(prefix="/api", tags=["feed"])


async def resolve_scope(db: AsyncSession, scope: str | None, user: User | None) -> Scope:
    try:
        sc = parse_scope(scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if sc.kind == "area":
        area = await db.get(Area, int(sc.ref))
        if area is None or user is None or area.user_id != user.id:
            raise HTTPException(404, "Område findes ikke")
        if area.kind not in ("polygon", "radius"):
            return Scope(area.kind, area.ref)
    return sc


@router.get("/feed")
async def get_feed(
    day: date | None = Query(None, alias="date"),
    scope: str | None = None,
    level: str = Query("bemaerk", pattern="^(SU|SUB|bemaerk|local|all)$"),
    local_max: int = Query(10, ge=1, le=1000),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_session),
):
    day = day or dk_today()
    sc = await resolve_scope(db, scope, user)
    if level == "all" and sc.kind == "dk":
        raise HTTPException(400, "'Alle' kræver et afgrænset område")
    threads = [dict(t) for t in await feed.threads(db, day, scope=sc, level=level, local_max=local_max)]
    threads = await reports.merge_feed(db, threads, day, sc, level, local_max, user)
    likes = await like_summary(db, "thread", [t["id"] for t in threads], user)
    for t in threads:
        t["likes"] = likes.get(t["id"], {}).get("count", 0)
    return {"date": day.isoformat(), "scope": sc.key, "level": level, "threads": threads}


@router.get("/feed/summary")
async def summary(day: date | None = Query(None, alias="date"), db: AsyncSession = Depends(get_session)):
    day = day or dk_today()
    status = await gateway.status()
    return {"date": day.isoformat(), "summary": await feed.day_summary(db, day),
            "migration": await feed.migration_today(db, day),
            "last_update": status.get("last_at"), "last_kind": status.get("last_kind")}


@router.get("/thread/{day}/{artnr}/{loknr}")
async def thread(day: date, artnr: int, loknr: int, user: User | None = Depends(optional_user),
                 db: AsyncSession = Depends(get_session)):
    data = await feed.thread_detail(db, day, artnr, loknr)
    portal = await reports.thread_reports(db, day, artnr, loknr, user)
    if data is None and not portal:
        raise HTTPException(404, "Tråden findes ikke (længere)")
    if data is None:
        data = await feed.thread_meta(db, day, artnr, loknr)
    data["reports"] = portal
    comments = (await db.execute(text("""
        SELECT c.id, c.body, c.created_at, u.navn, u.obserkode, c.user_id FROM thread_comments c JOIN users u ON u.id = c.user_id
        WHERE c.dato = :d AND c.artnr = :a AND c.loknr = :l AND NOT c.hidden ORDER BY c.created_at
    """), {"d": day, "a": artnr, "l": loknr})).mappings().all()
    data["comments"] = [dict(c) for c in comments]
    key = f"{day.isoformat()}/{artnr}/{loknr}"
    data["likes"] = (await like_summary(db, "thread", [key], user)).get(key) or {"count": 0, "liked": False, "names": []}
    clikes = await like_summary(db, "comment", [str(c["id"]) for c in data["comments"]], user)
    for c in data["comments"]:
        c["likes"] = clikes.get(str(c["id"])) or {"count": 0, "liked": False, "names": []}
    data["following"] = False
    if user:
        f = await db.get(ThreadFollow, (user.id, day, artnr, loknr))
        data["following"] = bool(f and not f.muted)
    return data


@router.post("/thread/{day}/{artnr}/{loknr}/follow")
async def follow(day: date, artnr: int, loknr: int, on: bool = True, user: User = Depends(require_user),
                 db: AsyncSession = Depends(get_session)):
    existing = await db.get(ThreadFollow, (user.id, day, artnr, loknr))
    if on and existing is None:
        db.add(ThreadFollow(user_id=user.id, dato=day, artnr=artnr, loknr=loknr))
    elif existing is not None:
        existing.muted = not on
    await db.commit()
    return {"following": on}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


@router.post("/thread/{day}/{artnr}/{loknr}/comments")
async def comment(day: date, artnr: int, loknr: int, body: CommentIn, tasks: BackgroundTasks,
                  user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not await kv().set(f"comment:rl:{user.id}", "1", nx=True, ex=5):
        raise HTTPException(429, "Vent et øjeblik før næste kommentar")
    c = ThreadComment(dato=day, artnr=artnr, loknr=loknr, user_id=user.id, body=body.body.strip())
    db.add(c)
    await db.commit()
    await activity.track(user.id, "comment", None, {"thread": f"{day}/{artnr}/{loknr}"})
    tasks.add_task(notify_comment, c.id)
    payload = {"type": "comment", "thread": f"{day}/{artnr}/{loknr}", "id": c.id, "body": c.body,
               "navn": user.navn, "obserkode": user.obserkode, "created_at": c.created_at.isoformat()}
    await publish("feed", payload)
    return payload


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    c = await db.get(ThreadComment, comment_id)
    if c is None:
        raise HTTPException(404)
    if c.user_id != user.id and user.role not in ("admin", "superadmin"):
        raise HTTPException(403)
    c.hidden = True
    await db.commit()
    return {"ok": True}


@router.get("/obs/{obsid}/media")
async def media(obsid: int, request: bool = False, user: User | None = Depends(optional_user)):
    """Billeder/lyd/DKU-status hentes fra DOFbasen på forespørgsel (køes; svar cachet 7 dage)."""
    hit = await media_for(obsid)
    if hit:
        return {"status": "ready", **hit}
    if not request:
        return {"status": "none"}
    if user is None:
        raise HTTPException(401, "Log ind for at hente medier fra DOFbasen")
    job = await gateway.enqueue("media", {"obsid": obsid}, priority=gateway.INTERACTIVE, dedupe=f"media:{obsid}")
    return {"status": "queued", "job": job, "queue": await kv().zcard(gateway.Q)}


@router.get("/nearby")
async def nearby(lat: float, lon: float, radius_km: float = Query(10, gt=0, le=100), days: int = Query(1, ge=1, le=14),
                 level: str = Query("bemaerk", pattern="^(SU|SUB|bemaerk|all)$"), db: AsyncSession = Depends(get_session)):
    min_rank = feed.LEVELS[level]
    rows = (await db.execute(text(f"""
        SELECT o.dato, o.artnr, s.navn AS art, o.loknr, l.navn AS lok, max(o.antal) AS antal, max({feed.KAT_RANK}) AS rank,
               l.lat, l.lon, round((ST_Distance(l.geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000)::numeric, 1) AS km
        FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
        WHERE o.dato > current_date - CAST(:days AS integer) AND o.hemmelig = 0
          AND ST_DWithin(l.geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :r)
          AND {feed.KAT_RANK} >= :minrank
        GROUP BY o.dato, o.artnr, s.navn, o.loknr, l.navn, l.lat, l.lon, l.geom
        ORDER BY o.dato DESC, rank DESC, km LIMIT 300
    """), {"lat": lat, "lon": lon, "r": radius_km * 1000, "days": days, "minrank": min_rank})).mappings().all()
    kat = {3: "SU", 2: "SUB", 1: "bemaerk", 0: "alm"}
    return {"items": [{**dict(r), "kategori": kat[r["rank"]], "km": float(r["km"])} for r in rows]}


@router.get("/stream")
async def stream(request: Request, user: User | None = Depends(optional_user)):
    """Server-sent events: nye observationer, kommentarer og personlige notifikationer."""
    channels = ["feed"] + ([f"user:{user.id}"] if user else [])

    async def gen():
        pubsub = kv().pubsub()
        await pubsub.subscribe(*channels)
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if msg is None:
                    yield ": ping\n\n"
                    continue
                data = msg["data"]
                event = json.loads(data).get("type", "message")
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
