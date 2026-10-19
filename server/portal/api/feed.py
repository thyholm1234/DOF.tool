from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import kv
from portal.db import get_session
from portal.dof import gateway
from portal.dof.jobs import media_for, media_many
from portal.models import AlertRule, Area, ThreadComment, ThreadFollow, User
from portal.api.social import like_summary, notify_comment
from portal.services import activity, feed, finds, moderation, reports
from portal.services.auth import role_at_least
from portal.services.areas import Scope, parse_scope, scope_for_area
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
    if scope == "mine":
        if user is None:
            raise HTTPException(401, "Log ind for at bruge dine filtre")
        threads = await _my_filter_threads(db, day, user)
        await _attach_media(db, day, threads)
        await _attach_comments(db, day, threads)
        likes = await like_summary(db, "thread", [t["id"] for t in threads], user)
        for t in threads:
            t["likes"] = likes.get(t["id"], {}).get("count", 0)
        return {"date": day.isoformat(), "scope": "mine", "level": "mine", "threads": threads,
                "n_rules": await _n_rules(db, user)}
    sc = await resolve_scope(db, scope, user)
    if level == "all" and sc.kind == "dk":
        raise HTTPException(400, "'Alle' kræver et afgrænset område")
    threads = [dict(t) for t in await feed.threads(db, day, scope=sc, level=level, local_max=local_max)]
    threads = await reports.merge_feed(db, threads, day, sc, level, local_max, user)
    await _attach_media(db, day, threads)
    await _attach_comments(db, day, threads)
    likes = await like_summary(db, "thread", [t["id"] for t in threads], user)
    for t in threads:
        t["likes"] = likes.get(t["id"], {}).get("count", 0)
    return {"date": day.isoformat(), "scope": sc.key, "level": level, "threads": threads}


async def _attach_comments(db: AsyncSession, day: date, threads: list[dict]) -> None:
    counts = {f"{day.isoformat()}/{a}/{l}": n for a, l, n in (await db.execute(text("""
        SELECT artnr, loknr, count(*) FROM thread_comments WHERE dato = :d AND NOT hidden GROUP BY artnr, loknr
    """), {"d": day})).all()}
    for t in threads:
        t["n_comments"] = counts.get(t["id"], 0)


async def _attach_media(db: AsyncSession, day: date, threads: list[dict]) -> None:
    """Billeder, lyd og DKU-status fra medie-scannerens cache på trådene (ingen kald til DOFbasen her)."""
    if not threads:
        return
    rows = (await db.execute(text("""
        SELECT artnr, loknr, array_agg(obsid) FROM observations
        WHERE dato = :d AND hemmelig = 0 AND artnr = ANY(:a) AND loknr = ANY(:l) GROUP BY artnr, loknr
    """), {"d": day, "a": list({t["artnr"] for t in threads}), "l": list({t["loknr"] for t in threads if t["loknr"]})})).all()
    ids = {(a, l): list(o) for a, l, o in rows}
    found = await media_many([o for v in ids.values() for o in v])
    for t in threads:
        ms = [found[o] for o in ids.get((t["artnr"], t["loknr"]), []) if o in found]
        if ms:
            t["media"] = {"images": sum(len(m["images"]) for m in ms), "sounds": sum(len(m["sounds"]) for m in ms),
                          "dku": sorted({m["dku"] for m in ms if m.get("dku")}),
                          "image": next((m["images"][0] for m in ms if m["images"]), None)}


async def _n_rules(db: AsyncSession, user: User) -> int:
    return (await db.execute(select(func.count()).select_from(AlertRule)
                             .where(AlertRule.user_id == user.id, AlertRule.enabled))).scalar() or 0


async def _my_filter_threads(db: AsyncSession, day: date, user: User) -> list[dict]:
    """Dagens tråde der matcher mindst én af brugerens aktive alarmregler (samme regler som notifikationerne)."""
    rules = (await db.execute(select(AlertRule).where(AlertRule.user_id == user.id, AlertRule.enabled))).scalars().all()
    fetched: dict[tuple, list[dict]] = {}

    async def fetch(sc: Scope, level: str, local_max: int) -> list[dict]:
        key = (sc.key, level, local_max if level == "local" else 0)
        if key not in fetched:
            ts = [dict(t) for t in await feed.threads(db, day, scope=sc, level=level, local_max=local_max)]
            fetched[key] = await reports.merge_feed(db, ts, day, sc, level, local_max, user)
        return fetched[key]

    out: dict[str, dict] = {}
    for r in rules:
        if r.kommuner or r.afdelinger:
            scopes = [Scope("kommune", k) for k in r.kommuner] + [Scope("afdeling", str(a)) for a in r.afdelinger]
        elif r.area_id:
            scopes = [scope_for_area(await db.get(Area, r.area_id))]
        else:
            scopes = [Scope("dk")]
        levels = set(r.level.split(","))
        kats = levels & {"SU", "SUB", "bemaerk"}
        wanted: list[tuple[str, set[str]]] = []  # (feed-niveau, kategorier der tæller)
        if "all" in levels:
            wanted.append(("all", {"SU", "SUB", "bemaerk", "alm"}))
        else:
            if kats:
                wanted.append(("bemaerk" if "bemaerk" in kats else "SUB" if "SUB" in kats else "SU", kats))
            if "local" in levels:
                wanted.append(("local", {"alm", "bemaerk"}))
        for sc in scopes:
            for level, ok in wanted:
                for t in await fetch(sc, level, r.local_max):
                    if t["kategori"] not in ok or ((r.min_antal or 1) > 1 and t["max_antal"] < r.min_antal):
                        continue
                    if (r.species_include and t["artnr"] not in r.species_include) or t["artnr"] in (r.species_exclude or ()):
                        continue
                    hit = out.setdefault(t["id"], {**t, "rules": []})
                    if r.name not in hit["rules"]:
                        hit["rules"].append(r.name)
                    if level == "local" and t["kategori"] not in levels:
                        hit["local_hit"] = True
    return sorted(out.values(), key=lambda t: t["last_seen"], reverse=True)


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
    found = await media_many([o["obsid"] for o in data.get("observations", [])])
    for o in data.get("observations", []):
        o["media"] = found.get(o["obsid"])
    data["find"] = await finds.thread_find(db, day, artnr, loknr)
    data["comments"] = await _comments(db, day, artnr, loknr, user)
    key = f"{day.isoformat()}/{artnr}/{loknr}"
    data["likes"] = (await like_summary(db, "thread", [key], user)).get(key) or {"count": 0, "liked": False, "names": []}
    data["following"] = False
    data["comment_ban"] = None
    if user:
        f = await db.get(ThreadFollow, (user.id, day, artnr, loknr))
        data["following"] = bool(f and not f.muted)
        data["comment_ban"] = await moderation.active_ban(db, user.id)
    return data


async def _comments(db: AsyncSession, day: date, artnr: int, loknr: int, user: User | None) -> list[dict]:
    rows = (await db.execute(text(f"""
        SELECT c.id, c.body, c.created_at, u.navn, u.obserkode, c.user_id FROM thread_comments c JOIN users u ON u.id = c.user_id
        WHERE c.dato = :d AND c.artnr = :a AND c.loknr = :l AND NOT c.hidden AND {moderation.VISIBLE} ORDER BY c.created_at
    """), {"d": day, "a": artnr, "l": loknr, "viewer": user.id if user else -1})).mappings().all()
    out = [dict(c) for c in rows]
    likes = await like_summary(db, "comment", [str(c["id"]) for c in out], user)
    for c in out:
        c["likes"] = likes.get(str(c["id"])) or {"count": 0, "liked": False, "names": []}
    return out


@router.get("/thread/{day}/{artnr}/{loknr}/comments")
async def thread_comments(day: date, artnr: int, loknr: int, user: User = Depends(require_user),
                          db: AsyncSession = Depends(get_session)):
    """Kun kommentarsporet – hentes igen, når tråden får besked om ændringer."""
    return {"comments": await _comments(db, day, artnr, loknr, user), "comment_ban": await moderation.active_ban(db, user.id)}


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
    ban = await moderation.active_ban(db, user.id)
    if ban:
        until = f" til {date.fromisoformat(ban['until'][:10]).strftime('%d.%m.%Y')}" if ban["until"] else ""
        raise HTTPException(403, f"Du er udelukket fra at kommentere{until}. Årsag: {ban['reason']}")
    if not await kv().set(f"comment:rl:{user.id}", "1", nx=True, ex=5):
        raise HTTPException(429, "Vent et øjeblik før næste kommentar")
    c = ThreadComment(dato=day, artnr=artnr, loknr=loknr, user_id=user.id, body=body.body.strip())
    db.add(c)
    await db.commit()
    await activity.track(user.id, "comment", None, {"thread": f"{day}/{artnr}/{loknr}"})
    tasks.add_task(notify_comment, c.id)
    await moderation.comments_changed(f"{day}/{artnr}/{loknr}", "new", c.id)
    return {"id": c.id, "body": c.body, "navn": user.navn, "obserkode": user.obserkode, "user_id": user.id,
            "created_at": c.created_at.isoformat(), "likes": {"count": 0, "liked": False, "names": []}}


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    c = await db.get(ThreadComment, comment_id)
    if c is None:
        raise HTTPException(404)
    if c.user_id != user.id and not role_at_least(user, "moderator"):
        raise HTTPException(403)
    c.hidden = True
    await db.commit()
    if c.user_id != user.id:
        await activity.track(user.id, "moderation", None, {"comment": c.id, "deleted": True})
    await moderation.comments_changed(f"{c.dato.isoformat()}/{c.artnr}/{c.loknr}", "removed", c.id)
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
