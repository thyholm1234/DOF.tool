"""Administration: gateway/kø, arkiv, beregninger, indstillinger, brugere, arter, nyheder, moderation."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.api.deps import require_perm, require_role, require_user
from portal.cache import bump, kv, worker_command
from portal.config import get_settings
from portal.db import get_session
from portal.dof import gateway
from portal.dof.archive import FILE_RE, PROGRESS, archive_files, pending_files
from portal.models import CommentBan, NewsPost, Setting, Species, SupportMessage, SupportThread, User
from portal.services import activity, datasync, moderation, weather_archive
from portal.services.auth import MODULES, ROLE_MODULES, ROLES, can, modules

router = APIRouter(prefix="/api", tags=["admin"])
superadmin = require_role("superadmin")
perm_arter = require_perm("arter")
perm_brugere = require_perm("brugere")
perm_data = require_perm("data")
perm_drift = require_perm("drift")
perm_indhold = require_perm("indhold")
perm_moderation = require_perm("moderation")
perm_vejr = require_perm("vejr")

SETTINGS = {"live_enabled": True, "matrikel_tag": "#BB", "welcome": ""}


@router.get("/admin/overview")
async def overview(user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
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
async def put_settings(body: dict[str, Any], user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
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
async def enqueue(body: EnqueueIn, user: User = Depends(perm_drift)):
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
async def cancel_job(job_id: str, user: User = Depends(perm_drift)):
    await gateway.cancel(job_id)
    return {"ok": True}


@router.delete("/admin/gateway/queue")
async def clear_queue(user: User = Depends(perm_drift)):
    return {"removed": await gateway.clear()}


@router.post("/admin/worker/{cmd}")
async def worker(cmd: str, body: dict | None = None, user: User = Depends(perm_drift)):
    if cmd not in ("archive_import", "compute", "climate", "aggregates", "events", "regions"):
        raise HTTPException(400)
    await worker_command(cmd, **(body or {}))
    return {"queued": cmd}


# ---------------------------------------------------------------- upload af månedsfiler (dofbasen_ÅÅÅÅ_MM.csv)


@router.get("/admin/archive/files")
async def archive_list(user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    upload_root = Path(get_settings().archive_upload_dir)
    imported = {r["name"]: r for r in (await db.execute(text("SELECT name, size, mtime, rows, imported_at FROM archive_files"))).mappings()}
    pending = {p.name for p in await pending_files()}
    out = []
    for p in archive_files():
        st = p.stat()
        rec = imported.get(p.name)
        out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime, "uploaded": p.parent == upload_root,
                    "rows": rec["rows"] if rec else None, "imported_at": rec["imported_at"] if rec else None,
                    "pending": p.name in pending})
    return out


@router.post("/admin/archive/upload")
async def archive_upload(file: UploadFile = File(...), mtime: float | None = Form(None), user: User = Depends(perm_drift)):
    """Gem én månedsfil i upload-arkivet. Importen startes separat (worker: archive_import), når alle filer er sendt,
    og filen slettes, når den er i databasen."""
    name = Path(file.filename or "").name
    if not FILE_RE.match(name):
        raise HTTPException(400, f"{name or 'Filen'}: navnet skal være dofbasen_ÅÅÅÅ_MM.csv")
    head = (await file.read(4096)).decode("latin-1", errors="replace").split("\n", 1)[0].lstrip("\ufeff")
    cols = head.strip().split(";")
    if not cols or cols[0] != "Dato" or not {"Artnr", "Obsid", "Turid", "Loknr"} <= set(cols):
        raise HTTPException(400, f"{name}: ligner ikke et DOFbasen-udtræk (mangler kolonner som Dato, Artnr, Obsid)")
    root = Path(get_settings().archive_upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{name}.part"
    size = 0
    try:
        await file.seek(0)
        with tmp.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                fh.write(chunk)
                size += len(chunk)
        # Behold filens egen ændringstid: en fil der allerede er importeret fra arkivet, importeres så ikke igen
        if mtime:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, root / name)
    finally:
        tmp.unlink(missing_ok=True)
    pending = any(p.name == name for p in await pending_files())
    if not pending:
        (root / name).unlink(missing_ok=True)  # allerede importeret uændret – intet at gemme
    return {"name": name, "size": size, "pending": pending}


@router.delete("/admin/archive/upload/{name}")
async def archive_upload_delete(name: str, user: User = Depends(perm_drift)):
    """Fjern en uploadet fil, der endnu ikke er importeret."""
    if not FILE_RE.match(name):
        raise HTTPException(400)
    path = Path(get_settings().archive_upload_dir) / name
    if not path.is_file():
        raise HTTPException(404)
    path.unlink()
    return {"ok": True}


@router.get("/admin/jobs")
async def jobs(user: User = Depends(perm_drift)):
    return await jobstatus.snapshot()


@router.get("/admin/users")
async def users(q: str = "", user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT u.id, u.obserkode, u.navn, u.role, u.blocked, u.permissions, u.created_at, u.last_seen, u.history_synced_at,
               (SELECT count(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS devices,
               (SELECT count(*) FROM alert_rules r WHERE r.user_id = u.id) AS rules
        FROM users u WHERE (:q = '' OR u.obserkode ILIKE :like OR u.navn ILIKE :like)
        ORDER BY u.last_seen DESC NULLS LAST LIMIT 300
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    out = []
    for r in rows:
        probe = User(role=r["role"], blocked=False, permissions=r["permissions"])
        out.append({**dict(r), "modules": sorted(modules(probe))})
    return out


@router.get("/admin/modules")
async def module_list(user: User = Depends(perm_brugere)):
    """Modulerne til afkrydsningslisten, rollernes standard og hvad den indloggede selv må tildele."""
    return {"modules": MODULES, "roles": {r: sorted(m) for r, m in ROLE_MODULES.items()},
            "grantable": sorted(modules(user))}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: int, body: dict[str, Any], user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
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
        before = modules(target)
        target.role = role
        if user.role != "superadmin" and (before ^ modules(target)) - modules(user):
            raise HTTPException(403, "Rollen giver eller fjerner moduler, du ikke selv har adgang til")
    if "permissions" in body:
        _set_permissions(user, target, body["permissions"])
    if "blocked" in body:
        if target.role == "superadmin":
            raise HTTPException(400)
        if target.role == "admin" and user.role != "superadmin":
            raise HTTPException(403, "Kun superadmin kan spærre administratorer")
        target.blocked = bool(body["blocked"])
        if target.blocked:
            async for key in kv().scan_iter("sess:*"):
                if await kv().get(key) == str(target.id):
                    await kv().delete(key)
    await db.commit()
    await bump("lists")
    return {"ok": True}


def _set_permissions(user: User, target: User, value: Any) -> None:
    """None = rollens standard; ellers en liste af moduler. Man kan kun give og fjerne moduler, man selv har."""
    if target.id == user.id:
        raise HTTPException(400, "Du kan ikke ændre dine egne rettigheder")
    if target.role in ("admin", "superadmin") and user.role != "superadmin":
        raise HTTPException(403, "Kun superadmin kan ændre administratorers rettigheder")
    if target.role == "superadmin":
        raise HTTPException(400, "Superadmin har altid alle moduler")
    before = modules(target)
    if value is None:
        after = set(ROLE_MODULES.get(target.role, ()))
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        unknown = set(value) - set(MODULES)
        if unknown:
            raise HTTPException(400, f"Ukendte moduler: {', '.join(sorted(unknown))}")
        after = set(value)
    else:
        raise HTTPException(400, "Rettigheder skal være en liste af moduler eller null")
    if user.role != "superadmin" and (before ^ after) - modules(user):
        raise HTTPException(403, "Du kan kun give og fjerne moduler, du selv har adgang til")
    target.permissions = None if value is None else sorted(after)


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
async def species(q: str = "", user: User = Depends(perm_arter), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT s.artnr, s.navn, s.klass, s.art_type, s.list_artnr, s.excluded, ls.navn AS list_navn FROM species s
        LEFT JOIN species ls ON ls.artnr = s.list_artnr
        WHERE :q = '' OR s.navn ILIKE :like ORDER BY s.artnr LIMIT 400
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/admin/species/{artnr}")
async def update_species(artnr: int, body: dict[str, Any], user: User = Depends(perm_arter), db: AsyncSession = Depends(get_session)):
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
async def comments(status: str = "alle", q: str = "", user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    """Kommentarer til moderation (moderator og op). status: alle / skjulte / synlige."""
    rows = (await db.execute(text("""
        SELECT c.id, c.dato, c.artnr, c.loknr, c.body, c.hidden, c.created_at, c.user_id, u.obserkode, u.navn, u.role,
               s.navn AS art, l.navn AS lok,
               (SELECT count(*) FROM likes k WHERE k.kind = 'comment' AND k.key = c.id::text) AS likes,
               EXISTS (SELECT 1 FROM comment_bans b WHERE b.user_id = c.user_id AND {ACTIVE}) AS banned
        FROM thread_comments c JOIN users u ON u.id = c.user_id LEFT JOIN species s ON s.artnr = c.artnr
        LEFT JOIN locations l ON l.loknr = c.loknr
        WHERE (:st = 'alle' OR (:st = 'skjulte' AND c.hidden) OR (:st = 'synlige' AND NOT c.hidden))
          AND (:q = '' OR c.body ILIKE :like OR u.navn ILIKE :like OR u.obserkode ILIKE :like OR s.navn ILIKE :like)
        ORDER BY c.created_at DESC LIMIT 300
    """.replace("{ACTIVE}", moderation.ACTIVE)), {"st": status, "q": q, "like": f"%{q}%"})).mappings().all()
    return [{**dict(r), "staff": r["role"] in ROLES[1:]} for r in rows]


@router.patch("/admin/comments/{cid}")
async def moderate(cid: int, body: dict, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    hidden = bool(body.get("hidden"))
    row = (await db.execute(text("UPDATE thread_comments SET hidden = :h WHERE id = :id RETURNING dato, artnr, loknr"),
                            {"h": hidden, "id": cid})).first()
    if row is None:
        raise HTTPException(404)
    await db.commit()
    await activity.track(user.id, "moderation", None, {"comment": cid, "hidden": hidden})
    await moderation.comments_changed(f"{row[0].isoformat()}/{row[1]}/{row[2]}", "hidden" if hidden else "shown", cid)
    return {"ok": True}


# ---------------------------------------------------------------- udelukkelse fra kommentarer (som DOF.not's blacklist)


@router.get("/admin/bans")
async def bans(user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text(f"""
        SELECT b.user_id, b.reason, b.evidence, b.hide_comments, b.until, b.created_at, u.navn, u.obserkode,
               coalesce(m.navn, m.obserkode) AS by_navn, {moderation.ACTIVE} AS active,
               (SELECT count(*) FROM thread_comments c WHERE c.user_id = b.user_id) AS n_comments
        FROM comment_bans b JOIN users u ON u.id = b.user_id LEFT JOIN users m ON m.id = b.created_by
        ORDER BY {moderation.ACTIVE} DESC, b.created_at DESC
    """))).mappings().all()
    return [dict(r) for r in rows]


class BanIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    days: int | None = Field(None, ge=1, le=3650)  # None = indtil videre
    hide_comments: bool = True
    comment_id: int | None = None
    notify: bool = True


@router.put("/admin/bans/{user_id}")
async def ban(user_id: int, body: BanIn, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Brugeren findes ikke")
    if target.id == user.id:
        raise HTTPException(400, "Du kan ikke udelukke dig selv")
    if modules(target) and not can(user, "brugere"):  # personale udelukkes kun af dem, der styrer brugere
        raise HTTPException(403, "Kun dem med adgang til Brugere kan udelukke andre med admin-adgang")
    evidence = None
    if body.comment_id:
        evidence = (await db.execute(text("SELECT body FROM thread_comments WHERE id = :c AND user_id = :u"),
                                     {"c": body.comment_id, "u": target.id})).scalar()
    b = await db.get(CommentBan, target.id) or CommentBan(user_id=target.id)
    b.reason, b.hide_comments, b.created_by = body.reason.strip(), body.hide_comments, user.id
    b.until = datetime.now(timezone.utc) + timedelta(days=body.days) if body.days else None
    b.evidence = evidence or b.evidence
    b.created_at = datetime.now(timezone.utc)
    db.add(b)
    await db.commit()
    await activity.track(user.id, "ban", None, {"user": target.id, "days": body.days, "hide": body.hide_comments})
    await moderation.comments_changed("*", "ban")
    if body.notify:
        from portal.api.social import _notify
        until = f" til og med {b.until.date().strftime('%d.%m.%Y')}" if b.until else ""
        await _notify([target.id], "Du er udelukket fra at kommentere", f"{body.reason.strip()}{until}. Skriv til os under Feedback og support, hvis du har spørgsmål.",
                      "/#/support", f"ban:{target.id}:{int(b.created_at.timestamp())}")
    return {"ok": True}


@router.delete("/admin/bans/{user_id}")
async def unban(user_id: int, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    b = await db.get(CommentBan, user_id)
    if b is None:
        raise HTTPException(404)
    await db.delete(b)
    await db.commit()
    await activity.track(user.id, "unban", None, {"user": user_id})
    await moderation.comments_changed("*", "unban")
    return {"ok": True}


# ---------------------------------------------------------------- beskeder fra moderatorer (samme modul som support)


class ModMessageIn(BaseModel):
    subject: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=2, max_length=5000)
    comment_id: int | None = None


@router.post("/admin/users/{user_id}/message")
async def message_user(user_id: int, body: ModMessageIn, user: User = Depends(perm_moderation), db: AsyncSession = Depends(get_session)):
    """Start en samtale med en bruger. Den ligger i brugerens 'Feedback og support', og svar ses af alle moderatorer."""
    from portal.api.social import _notify
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "Brugeren findes ikke")
    ctx = None
    if body.comment_id:
        c = (await db.execute(text("SELECT body, dato, artnr, loknr FROM thread_comments WHERE id = :c"), {"c": body.comment_id})).first()
        if c:
            ctx = {"comment": c[0][:500], "path": f"#/traad/{c[1].isoformat()}/{c[2]}/{c[3]}"}
    t = SupportThread(user_id=target.id, subject=body.subject.strip(), kind="moderation", unread_user=True, unread_admin=False)
    db.add(t)
    await db.flush()
    db.add(SupportMessage(thread_id=t.id, user_id=user.id, from_admin=True, body=body.body.strip(), context=ctx))
    await db.commit()
    await activity.track(user.id, "mod_message", None, {"user": target.id, "thread": t.id})
    await _notify([target.id], f"Besked fra moderator: {t.subject}", body.body, f"/#/support?t={t.id}", f"support:{t.id}:0")
    return {"thread": t.id}


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
async def create_news(body: NewsIn, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
    n = NewsPost(author_id=user.id, **body.model_dump())
    db.add(n)
    await db.commit()
    return {"id": n.id}


@router.delete("/admin/news/{nid}")
async def delete_news(nid: int, user: User = Depends(perm_indhold), db: AsyncSession = Depends(get_session)):
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


def _source_perm(source: str, user: User) -> None:
    """Datapanelerne deles mellem DOFbasen (modulet data) og vejrarkivet (modulet vejr)."""
    if source not in ("dof", "weather"):
        raise HTTPException(404)
    module = "data" if source == "dof" else "vejr"
    if not can(user, module):
        raise HTTPException(403, f"Kræver adgang til {MODULES[module].split(':')[0]}")


@router.get("/admin/data/{source}")
async def data_panel(source: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    return await _panel(db, source)


@router.put("/admin/data/{source}/plan")
async def save_plan(source: str, body: PlanIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    try:
        await datasync.save_plan(db, source, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await _panel(db, source)


@router.post("/admin/data/{source}/run/{tier}")
async def run_tier(source: str, tier: str, user: User = Depends(require_user)):
    _source_perm(source, user)
    if tier not in dict(datasync.TIERS) or (source == "dof" and tier == "today"):
        raise HTTPException(400, "Ukendt vindue")
    await worker_command("run_plan", source=source, tier=tier)
    return {"queued": True}


class MonthIn(BaseModel):
    year: int
    month: int = Field(ge=1, le=12)


@router.post("/admin/data/dof/month")
async def dof_month(body: MonthIn, user: User = Depends(perm_data)):
    from portal.dof.jobs import enqueue_month

    return {"job": await enqueue_month(body.year, body.month, gateway.NORMAL)}


@router.delete("/admin/data/{source}/month/{year}/{month}")
async def delete_month(source: str, year: int, month: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    _source_perm(source, user)
    if not 1 <= month <= 12:
        raise HTTPException(404)
    try:
        n = await (datasync if source == "dof" else weather_archive).delete_month(db, year, month)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await activity.track(user.id, "data_delete", None, {"source": source, "month": f"{year}-{month:02d}", "rows": n})
    return {"deleted": n}


class WeatherMonthsIn(BaseModel):
    months: list[tuple[int, int]] = Field(default_factory=list, max_length=600)
    only_missing: bool = False


@router.post("/admin/data/weather/months")
async def weather_months(body: WeatherMonthsIn, user: User = Depends(perm_vejr)):
    return {"queued": await weather_archive.enqueue(body.months, only_missing=body.only_missing)}


@router.delete("/admin/data/weather/queue")
async def weather_clear(user: User = Depends(perm_vejr)):
    return {"removed": await weather_archive.clear_queue()}
