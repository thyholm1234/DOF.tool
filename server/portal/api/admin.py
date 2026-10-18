"""Administration: gateway/kø, arkiv, beregninger, indstillinger, brugere, arter, nyheder, moderation."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_role
from portal.cache import bump, kv, worker_command
from portal.db import get_session
from portal.dof import gateway
from portal.dof.archive import PROGRESS, archive_files, pending_files
from portal.models import NewsPost, Setting, Species, User
from portal.services import datasync, weather_archive
from portal.services.auth import ROLES

router = APIRouter(prefix="/api", tags=["admin"])
admin = require_role("admin")
superadmin = require_role("superadmin")

SETTINGS = {"live_enabled": True, "matrikel_tag": "#BB", "welcome": ""}


@router.get("/admin/overview")
async def overview(user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    counts = (await db.execute(text("""
        SELECT (SELECT count(*) FROM users) AS users,
               (SELECT count(*) FROM push_subscriptions) AS devices,
               (SELECT count(*) FROM alert_rules WHERE enabled) AS rules,
               (SELECT count(*) FROM areas) AS areas,
               (SELECT reltuples::bigint FROM pg_class WHERE relname = 'observations') AS observations_est,
               (SELECT min(dato) FROM observations) AS first_date,
               (SELECT max(dato) FROM observations) AS last_date,
               (SELECT count(*) FROM notifications WHERE created_at > now() - interval '24 hours') AS notifications_24h,
               (SELECT count(*) FROM courses) AS courses
    """))).mappings().first()
    r = kv()
    log = [json.loads(x) for x in await r.lrange("dof:log", 0, 49)]
    progress = await r.hgetall(PROGRESS)
    return {
        "counts": dict(counts),
        "gateway": await gateway.status(),
        "queue": await gateway.queue_snapshot(100),
        "log": log,
        "archive": {"progress": progress, "files": len(archive_files()), "pending": [p.name for p in await pending_files()][:20]},
        "worker": await r.hgetall("worker:last_command"),
        "settings": await get_settings_dict(db),
    }


async def get_settings_dict(db: AsyncSession) -> dict:
    rows = {s.key: s.value for s in (await db.execute(select(Setting).where(Setting.key.in_(list(SETTINGS))))).scalars()}
    return {k: rows.get(k, v) for k, v in SETTINGS.items()}


@router.get("/settings/public")
async def public_settings(db: AsyncSession = Depends(get_session)):
    s = await get_settings_dict(db)
    return {"matrikel_tag": s["matrikel_tag"], "welcome": s["welcome"]}


@router.put("/admin/settings")
async def put_settings(body: dict[str, Any], user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    for k, v in body.items():
        if k not in SETTINGS:
            raise HTTPException(400, f"Ukendt indstilling {k}")
        row = await db.get(Setting, k)
        if row is None:
            db.add(Setting(key=k, value=v))
        else:
            row.value = v
    await db.commit()
    if "matrikel_tag" in body:
        await bump("lists")
    return await get_settings_dict(db)


class EnqueueIn(BaseModel):
    kind: str = Field(pattern="^(day|month|history|artdata|status|bemaerk|wsdata|reference)$")
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/admin/gateway/enqueue")
async def enqueue(body: EnqueueIn, user: User = Depends(admin)):
    from portal.dof.jobs import enqueue_day, enqueue_month, enqueue_reference_refresh

    if body.kind == "reference":
        return {"queued": await enqueue_reference_refresh()}
    p = body.params
    if body.kind == "day":
        return {"job": await enqueue_day(date.fromisoformat(p["date"]), gateway.HIGH)}
    if body.kind == "month":
        return {"job": await enqueue_month(int(p["year"]), int(p["month"]), gateway.NORMAL)}
    if body.kind == "history":
        p = {"obserkode": str(p["obserkode"]).upper(), "year_from": int(p.get("year_from", 1900)),
             "year_to": int(p.get("year_to", date.today().year))}
    job = await gateway.enqueue(body.kind, p, priority=gateway.HIGH if body.kind == "day" else gateway.NORMAL,
                                dedupe=f"{body.kind}:{json.dumps(p, sort_keys=True)}")
    return {"job": job}


@router.delete("/admin/gateway/jobs/{job_id}")
async def cancel_job(job_id: str, user: User = Depends(admin)):
    await gateway.cancel(job_id)
    return {"ok": True}


@router.post("/admin/worker/{cmd}")
async def worker(cmd: str, body: dict | None = None, user: User = Depends(admin)):
    if cmd not in ("archive_import", "compute", "climate", "aggregates", "events"):
        raise HTTPException(400)
    await worker_command(cmd, **(body or {}))
    return {"queued": cmd}


@router.get("/admin/users")
async def users(q: str = "", user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT u.id, u.obserkode, u.navn, u.role, u.blocked, u.created_at, u.last_seen, u.history_synced_at,
               (SELECT count(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS devices,
               (SELECT count(*) FROM alert_rules r WHERE r.user_id = u.id) AS rules
        FROM users u WHERE (:q = '' OR u.obserkode ILIKE :like OR u.navn ILIKE :like)
        ORDER BY u.last_seen DESC NULLS LAST LIMIT 300
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: int, body: dict[str, Any], user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if "role" in body:
        role = body["role"]
        if role not in ROLES:
            raise HTTPException(400)
        if (role in ("admin", "superadmin") or target.role in ("admin", "superadmin")) and user.role != "superadmin":
            raise HTTPException(403, "Kun superadmin kan ændre administratorer")
        if target.id == user.id and role != user.role:
            raise HTTPException(400, "Du kan ikke ændre din egen rolle")
        target.role = role
    if "blocked" in body:
        if target.role == "superadmin":
            raise HTTPException(400)
        target.blocked = bool(body["blocked"])
        if target.blocked:
            async for key in kv().scan_iter("sess:*"):
                if await kv().get(key) == str(target.id):
                    await kv().delete(key)
    await db.commit()
    await bump("lists")
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
async def delete_user(user_id: int, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None or target.role == "superadmin":
        raise HTTPException(400)
    await db.delete(target)
    await db.commit()
    await bump("lists")
    return {"ok": True}


@router.get("/admin/species")
async def species(q: str = "", user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT s.artnr, s.navn, s.klass, s.art_type, s.list_artnr, s.excluded, ls.navn AS list_navn FROM species s
        LEFT JOIN species ls ON ls.artnr = s.list_artnr
        WHERE :q = '' OR s.navn ILIKE :like ORDER BY s.artnr LIMIT 400
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/admin/species/{artnr}")
async def update_species(artnr: int, body: dict[str, Any], user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    sp = await db.get(Species, artnr)
    if sp is None:
        raise HTTPException(404)
    if "excluded" in body:
        sp.excluded = bool(body["excluded"])
    if "klass" in body and body["klass"] in ("SU", "SUB", "Alm"):
        sp.klass = body["klass"]
        await bump("reference")
    if "list_artnr" in body:
        sp.list_artnr = int(body["list_artnr"]) if body["list_artnr"] else None
    await db.commit()
    await bump("lists")
    return {"ok": True}


@router.get("/admin/comments")
async def comments(user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT c.id, c.dato, c.artnr, c.loknr, c.body, c.hidden, c.created_at, u.obserkode, u.navn, s.navn AS art
        FROM thread_comments c JOIN users u ON u.id = c.user_id LEFT JOIN species s ON s.artnr = c.artnr
        ORDER BY c.created_at DESC LIMIT 200
    """))).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/admin/comments/{cid}")
async def moderate(cid: int, body: dict, user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    await db.execute(text("UPDATE thread_comments SET hidden = :h WHERE id = :id"), {"h": bool(body.get("hidden")), "id": cid})
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- nyheder


class NewsIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    published: bool = True


@router.get("/news")
async def news(limit: int = Query(10, le=50), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT n.id, n.title, n.body, n.created_at, n.published, u.navn AS author FROM news n LEFT JOIN users u ON u.id = n.author_id
        WHERE n.published ORDER BY n.created_at DESC LIMIT :n
    """), {"n": limit})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/admin/news")
async def create_news(body: NewsIn, user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    n = NewsPost(author_id=user.id, **body.model_dump())
    db.add(n)
    await db.commit()
    return {"id": n.id}


@router.delete("/admin/news/{nid}")
async def delete_news(nid: int, user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(NewsPost).where(NewsPost.id == nid))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- datapaneler: DOFbasen og vejrarkiv



class PlanIn(BaseModel):
    tiers: dict[str, dict] = Field(default_factory=dict)
    start_year: int | None = None
    workers: int | None = None


async def _panel(db: AsyncSession, source: str) -> dict:
    plan = await datasync.get_plan(db, source)
    data = await datasync.dof_status(db) if source == "dof" else await weather_archive.status(db)
    extra = {}
    if source == "dof":
        g = await gateway.status()
        extra = {"queue_length": g.get("queue_length"), "calls_today": g.get(f"calls:{date.today().isoformat()}", 0),
                 "min_interval": g.get("min_interval")}
    return datasync.jsonable({**data, **extra, "plan": plan, "tiers": datasync.TIERS,
                              "last_runs": await datasync.last_runs(source),
                              "per_day": datasync.calls_per_day(source, plan["tiers"])})


@router.get("/admin/data/{source}")
async def data_panel(source: str, user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    if source not in ("dof", "weather"):
        raise HTTPException(404)
    return await _panel(db, source)


@router.put("/admin/data/{source}/plan")
async def save_plan(source: str, body: PlanIn, user: User = Depends(admin), db: AsyncSession = Depends(get_session)):
    if source not in ("dof", "weather"):
        raise HTTPException(404)
    try:
        await datasync.save_plan(db, source, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await _panel(db, source)


@router.post("/admin/data/{source}/run/{tier}")
async def run_tier(source: str, tier: str, user: User = Depends(admin)):
    if source not in ("dof", "weather") or tier not in dict(datasync.TIERS) or (source == "dof" and tier == "today"):
        raise HTTPException(400, "Ukendt vindue")
    await worker_command("run_plan", source=source, tier=tier)
    return {"queued": True}


class MonthIn(BaseModel):
    year: int
    month: int = Field(ge=1, le=12)


@router.post("/admin/data/dof/month")
async def dof_month(body: MonthIn, user: User = Depends(admin)):
    from portal.dof.jobs import enqueue_month

    return {"job": await enqueue_month(body.year, body.month, gateway.NORMAL)}


class WeatherMonthsIn(BaseModel):
    months: list[tuple[int, int]] = Field(default_factory=list, max_length=600)
    only_missing: bool = False


@router.post("/admin/data/weather/months")
async def weather_months(body: WeatherMonthsIn, user: User = Depends(admin)):
    return {"queued": await weather_archive.enqueue(body.months, only_missing=body.only_missing)}


@router.delete("/admin/data/weather/queue")
async def weather_clear(user: User = Depends(admin)):
    return {"removed": await weather_archive.clear_queue()}
