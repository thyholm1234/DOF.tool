"""Administration: gateway/kø, arkiv, beregninger, indstillinger, brugere, arter, nyheder, moderation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.api.deps import require_perm, require_role, require_user
from portal.cache import bump, kv, worker_command
from portal.config import get_settings
from portal.db import get_session
from portal.dof import gateway
from portal.dof.archive import FILE_RE, PROGRESS, archive_files, is_month_file, pending_files
from portal.models import CommentBan, NewsPost, Role, Setting, SupportMessage, SupportThread, User
from portal.services import account, activity, backup, datasync, diskusage, fcm, features, locfinds, moderation, schedule, speechmodel, weather_archive
from portal.services import auth as auth_service
from portal.services.auth import FIXED_ROLES, MODULES, ROLE_MODULES, ROLE_NAMES, SUPERADMIN, can, modules
from portal.services.setting_store import put_setting

router = APIRouter(prefix="/api", tags=["admin"])
superadmin = require_role("superadmin")
perm_arter = require_perm("arter")
perm_brugere = require_perm("brugere")
perm_data = require_perm("data")
perm_drift = require_perm("drift")
perm_indhold = require_perm("indhold")
perm_moderation = require_perm("moderation")
perm_vejr = require_perm("vejr")

SETTINGS = {"live_enabled": True, "matrikel_tag": "#BB", "welcome": "", "require_approval": True}
# Indstillinger der styres fra et andet modul end Drift
SETTING_MODULE = {"require_approval": "brugere"}


@router.get("/admin/storage")
async def storage_usage(refresh: bool = False, user: User = Depends(perm_drift)):
    """Lagerforbrug: databaser, objektlager, filer på disken og Valkey (gemt 5 minutter; refresh=1 måler igen)."""
    return await diskusage.measure(refresh)


@router.get("/admin/overview")
async def overview(user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    counts = (await db.execute(text("""
        SELECT (SELECT count(*) FROM users) AS users,
               (SELECT count(*) FROM push_subscriptions) AS devices,
               (SELECT count(*) FROM alert_rules WHERE enabled) AS rules,
               (SELECT count(*) FROM areas) AS areas,
               (SELECT count(*) FROM notifications WHERE created_at > now() - interval '24 hours') AS notifications_24h,
               (SELECT count(*) FROM courses) AS courses
    """))).mappings().first()
    obs = (await db.execute(text("""
        SELECT (SELECT reltuples::bigint FROM pg_class WHERE relname = 'observations') AS observations_est,
               (SELECT min(dato) FROM observations) AS first_date, (SELECT max(dato) FROM observations) AS last_date
    """))).mappings().first()
    counts = {**counts, **obs}
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
async def put_settings(body: dict[str, Any], user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    for k, v in body.items():
        if k not in SETTINGS:
            raise HTTPException(400, f"Ukendt indstilling {k}")
        module = SETTING_MODULE.get(k, "drift")
        if not can(user, module):
            raise HTTPException(403, f"Kræver adgang til {MODULES[module].split(':')[0]}")
        await put_setting(db, k, v, commit=False)
    await db.commit()
    if "matrikel_tag" in body:
        await bump("lists")
    return await get_settings_dict(db)


@router.get("/admin/features")
async def get_features(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Funktioner, der kan slås til/fra (kun superadmin): off / preview (kun superadmin og DOFbasen-roller) / on."""
    flags = await features.load(db)
    return [{"key": k, "name": name, "description": desc, "parent": parent, "default": default, "state": flags[k]}
            for k, (name, desc, parent, default) in features.FEATURES.items()]


@router.put("/admin/features")
async def put_features(body: dict[str, str], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    try:
        await features.save(db, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "features", None, body)
    return await get_features(user, db)


@router.get("/admin/fcm")
async def get_fcm(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Firebase (push til Android-appen): er en servicekonto sat op, og hvorfra (.env eller her)?"""
    await fcm.ensure(db)
    acc = fcm._account()
    return {"source": fcm.source(), "project_id": acc and acc["project_id"], "client_email": acc and acc["client_email"],
            "devices": (await db.execute(text("SELECT count(*) FROM push_subscriptions WHERE endpoint LIKE 'fcm:%'"))).scalar(),
            "last_error": fcm.LAST["error"]}


@router.put("/admin/fcm")
async def put_fcm(body: dict[str, Any], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Gem servicekontoens JSON (Firebase → Projektindstillinger → Tjenestekonti → Generér ny privat nøgle)."""
    acc = body.get("account")
    if isinstance(acc, str):
        try:
            acc = json.loads(acc)
        except ValueError as exc:
            raise HTTPException(400, "Filen er ikke gyldig JSON") from exc
    if not isinstance(acc, dict) or acc.get("type") != "service_account" or not fcm.valid(acc):
        raise HTTPException(400, "Det er ikke en servicekonto-nøgle (mangler type, project_id, client_email eller private_key)")
    try:
        fcm._assertion(acc)  # kan nøglen signere?
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Den private nøgle kan ikke bruges: {exc}") from exc
    await put_setting(db, fcm.SETTING, acc)
    fcm.set_db_account(acc)
    await activity.track(user.id, "fcm", None, {"project_id": acc["project_id"]})
    return await get_fcm(user, db)


@router.delete("/admin/fcm")
async def delete_fcm(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(Setting).where(Setting.key == fcm.SETTING))
    await db.commit()
    fcm.set_db_account(None)
    return await get_fcm(user, db)


# ---------------------------------------------------------------- backup (pg_dump): SFTP efter plan og download


class BackupIn(BaseModel):
    host: str = Field("", max_length=253, pattern=r"^[A-Za-z0-9._:-]*$")
    port: int = Field(22, ge=1, le=65535)
    user: str = Field("", max_length=100)
    password: str | None = None  # None = behold; "" = fjern
    private_key: str | None = Field(None, max_length=20000)
    path: str = Field("backup", max_length=300)
    keep: int = Field(14, ge=1, le=1000)


async def _backup_panel(db: AsyncSession) -> dict:
    return {**backup.public(await backup.load(db)), "last": await backup.last(), "targets": [n for n, _ in backup.targets()]}


@router.get("/admin/backup")
async def get_backup(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    return await _backup_panel(db)


@router.put("/admin/backup")
async def put_backup(body: BackupIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    if (body.host, body.port) != (cfg["host"], cfg["port"]):
        cfg["host_key"] = ""  # ny server: værtsnøglen skal godkendes igen
    cfg.update(host=body.host, port=body.port, user=body.user, path=body.path.strip(), keep=body.keep)
    if body.password is not None:
        cfg["password"] = body.password
    if body.private_key is not None:
        if body.private_key.strip():
            try:
                backup.check_private_key(body.private_key.strip())
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"Den private nøgle kan ikke læses (kun nøgler uden adgangskode): {exc}") from exc
        cfg["private_key"] = body.private_key.strip()
    await backup.save(db, cfg)
    await activity.track(user.id, "backup", None, {"host": body.host, "user": body.user, "path": body.path})
    return await _backup_panel(db)


@router.get("/admin/backup/host-key")
async def backup_host_key(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Serverens værtsnøgle (fingeraftryk) – godkendes med POST, før workeren vil forbinde."""
    cfg = await backup.load(db)
    if not cfg["host"]:
        raise HTTPException(400, "Angiv først en server")
    try:
        key = await backup.fetch_host_key(cfg["host"], int(cfg["port"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Kunne ikke hente værtsnøglen: {exc or type(exc).__name__}") from exc
    return {"key": key, "fingerprint": backup.fingerprint(key)}


@router.post("/admin/backup/host-key")
async def trust_host_key(body: dict[str, str], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    try:
        backup.fingerprint(body.get("key", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Ugyldig værtsnøgle") from exc
    cfg["host_key"] = body["key"]
    await backup.save(db, cfg)
    await activity.track(user.id, "backup_host_key", None, {"host": cfg["host"], "fingerprint": backup.fingerprint(body["key"])})
    return await _backup_panel(db)


@router.post("/admin/backup/test")
async def test_backup(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    if not backup.configured(cfg):
        raise HTTPException(400, "Udfyld server, bruger, kodeord eller nøgle, og godkend værtsnøglen")
    try:
        files = await backup.check(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"SFTP: {exc or type(exc).__name__}") from exc
    return {"files": files}


@router.post("/admin/backup/run")
async def run_backup(user: User = Depends(superadmin)):
    await worker_command("backup")
    return {"queued": "backup"}


@router.get("/admin/backup/download/{name}")
async def download_backup(name: str, user: User = Depends(superadmin)):
    """pg_dump af én database direkte til browseren (pg_restore-format)."""
    try:
        node = backup.target(name)
    except KeyError as exc:
        raise HTTPException(404, "Ukendt database") from exc
    stream = backup.dump(node)
    try:
        first = await anext(stream)  # fejler forbindelsen, får admin en fejl i stedet for en tom fil
    except StopAsyncIteration:
        first = b""
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    await activity.track(user.id, "backup_download", None, {"database": name})

    async def body():
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(body(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{backup.filename(name)}"'})


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
async def worker(cmd: str, body: dict | None = None, user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    if cmd == "loc_finds":
        ids = (body or {}).get("afdelinger")
        return {"queued": cmd, "afdelinger": await locfinds.enqueue(db, [int(x) for x in ids] if ids else None)}
    if cmd == "speech_model":
        await speechmodel.request(force=bool((body or {}).get("force")))
        return {"queued": cmd}
    if cmd not in ("archive_import", "compute", "climate", "aggregates", "events", "forecast", "regions", "gaps", "location_years"):
        raise HTTPException(400)
    await worker_command(cmd, **(body or {}))
    return {"queued": cmd}


# ---------------------------------------------------------------- planlagte opgaver


@router.get("/admin/schedule")
async def get_schedule(user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    return {"tasks": await schedule.overview(db)}


@router.put("/admin/schedule")
async def put_schedule(body: dict[str, dict], user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    try:
        await schedule.save(db, body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"tasks": await schedule.overview(db)}


async def _locfinds_panel(db: AsyncSession) -> dict:
    cfg = (await schedule.load(db))["loc_finds"]
    batches = await locfinds.batches(db)
    plan = await locfinds.get_plan(db, [b["id"] for b in batches])
    st = await locfinds.status(db)
    return {"on": cfg["on"], "at": cfg["at"], "max_day": locfinds.LAST_DAY,
            "batches": [{**b, "day": plan["days"][str(b["id"])], **(st.get(str(b["id"])) or {})} for b in batches]}


@router.get("/admin/locfinds")
async def get_locfinds(user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    return await _locfinds_panel(db)


class LocfindsIn(BaseModel):
    on: bool = True
    at: str = Field("03:30", pattern=r"^\d{1,2}:\d{2}$")
    days: dict[str, int] = Field(default_factory=dict)


@router.put("/admin/locfinds")
async def put_locfinds(body: LocfindsIn, user: User = Depends(perm_drift), db: AsyncSession = Depends(get_session)):
    try:
        await schedule.save(db, {"loc_finds": {"on": body.on, "at": body.at}})
        await locfinds.save_plan(db, {"days": body.days})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return await _locfinds_panel(db)


# ---------------------------------------------------------------- upload af DOFbasen-udtræk (CSV)


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
    """Gem ét DOFbasen-udtræk i upload-arkivet. Importen startes separat (worker: archive_import), når alle filer er sendt,
    og filen slettes, når den er i databasen. Månedsfiler (dofbasen_ÅÅÅÅ_MM.csv) beholder navnet; andre udtræk (fx
    search_result(3).csv) gemmes under navn + indholdets hash, så filer med samme navn fra browseren ikke overskriver
    hinanden, og en fil der allerede er importeret uændret, genkendes."""
    name = Path(file.filename or "").name
    if not name.lower().endswith(".csv"):
        raise HTTPException(400, f"{name or 'Filen'}: skal være en CSV-fil fra DOFbasen")
    head = (await file.read(4096)).decode("latin-1", errors="replace").split("\n", 1)[0].lstrip("\ufeff")
    cols = head.strip().split(";")
    if not cols or cols[0] != "Dato" or not {"Artnr", "Obsid", "Turid", "Loknr"} <= set(cols):
        raise HTTPException(400, f"{name}: ligner ikke et DOFbasen-udtræk (mangler kolonner som Dato, Artnr, Obsid)")
    root = Path(get_settings().archive_upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{secrets.token_hex(8)}.part"
    size, digest = 0, hashlib.sha1()
    try:
        await file.seek(0)
        with tmp.open("wb") as fh:
            while chunk := await file.read(1 << 20):
                fh.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if not is_month_file(name):
            stem = re.sub(r"[^\w().-]+", "_", Path(name).stem)[:50]
            name = f"{stem}_{digest.hexdigest()[:10]}.csv"
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


@router.get("/admin/speech-model")
async def speech_model(user: User = Depends(perm_drift)):
    """Talemodellen til tale-indtastning: den version serveren leverer, og om byggeagenten kører."""
    return await speechmodel.status()


UPLOAD_ID = re.compile(r"^[a-z0-9]{8,32}$")


def _part(upload_id: str) -> Path:
    if not UPLOAD_ID.match(upload_id):
        raise HTTPException(400, "Ugyldigt upload-id")
    return Path(get_settings().models_dir) / f".upload-{upload_id}.part"


@router.post("/admin/speech-model/upload/{upload_id}")
async def speech_model_chunk(upload_id: str, request: Request, offset: int = Query(ge=0), user: User = Depends(perm_drift)):
    """Én bid af en uploadet talemodel (speech-model.tar fra tools/speech-model/pack.sh). Filen er ca. 355 MB og sendes i
    små bidder, så ingen proxy foran portalen skal tillade så store forespørgsler. Bidderne skal komme i rækkefølge;
    ved et afbrud svarer serveren 409 med, hvor langt den er nået, så uploaden kan fortsætte derfra."""
    part = _part(upload_id)
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if offset == 0 and have:
        part.unlink()  # forfra
        have = 0
    if offset != have:
        raise HTTPException(409, f"Serveren har {have} bytes")
    if offset == 0:
        for old in part.parent.glob(".upload-*.part"):  # rester af opgivne uploads
            if old != part and time.time() - old.stat().st_mtime > 3600:
                old.unlink(missing_ok=True)
    size = have
    with part.open("ab") as fh:
        async for chunk in request.stream():
            size += len(chunk)
            if size > speechmodel.MAX_UPLOAD:
                fh.close()
                part.unlink(missing_ok=True)
                raise HTTPException(413, "Filen er for stor til at være en talemodel")
            fh.write(chunk)
    return {"size": size}


@router.post("/admin/speech-model/upload/{upload_id}/finish")
async def speech_model_finish(upload_id: str, user: User = Depends(perm_drift)):
    """Alle bidder er sendt: kontrollér arkivet og tag modellen i brug."""
    part = _part(upload_id)
    if not part.exists():
        raise HTTPException(404, "Uploaden findes ikke (eller er ryddet væk) – prøv igen")
    try:
        m = await asyncio.to_thread(speechmodel.install, part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        part.unlink(missing_ok=True)
    return {"version": m["version"], "size": m["size"]}


@router.get("/admin/jobs")
async def jobs(user: User = Depends(perm_drift)):
    return await jobstatus.snapshot()


@router.get("/admin/users")
async def users(q: str = "", user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT u.id, u.obserkode, u.navn, u.role, u.blocked, u.approved, u.is_test, u.permissions, u.created_at, u.last_seen, u.history_synced_at,
               (SELECT count(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS devices,
               (SELECT count(*) FROM alert_rules r WHERE r.user_id = u.id) AS rules
        FROM users u WHERE (:q = '' OR u.obserkode ILIKE :like OR u.navn ILIKE :like)
        ORDER BY u.approved, u.last_seen DESC NULLS LAST, u.created_at DESC LIMIT 300
    """), {"q": q, "like": f"%{q}%"})).mappings().all()
    out = []
    for r in rows:
        probe = User(role=r["role"], blocked=False, permissions=r["permissions"])
        out.append({**dict(r), "modules": sorted(modules(probe))})
    return out


class TestUserIn(BaseModel):
    navn: str = Field(min_length=1, max_length=160)
    password: str | None = Field(None, min_length=6, max_length=100)


@router.post("/admin/test-users")
async def create_test_user(body: TestUserIn, user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    """Opret en testbruger med lokal adgangskode (til test af appen uden en DOFbasen-konto). Adgangskoden vises kun her."""
    target, password = await auth_service.create_test_user(db, body.navn, body.password)
    await activity.track(user.id, "test_user", None, {"user": target.id})
    return {"id": target.id, "obserkode": target.obserkode, "navn": target.navn, "password": password}


@router.post("/admin/test-users/{user_id}/password")
async def reset_test_password(user_id: int, body: dict[str, Any], user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None or not target.is_test:
        raise HTTPException(404, "Ikke en testbruger")
    password = str(body.get("password") or "") or secrets.token_urlsafe(9)
    if len(password) < 6:
        raise HTTPException(400, "Adgangskoden skal have mindst 6 tegn")
    target.test_pw_hash = auth_service.hash_test_password(password)
    await db.commit()
    async for key in kv().scan_iter("sess:*"):
        if await kv().get(key) == str(target.id):
            await kv().delete(key)
    return {"obserkode": target.obserkode, "password": password}


@router.get("/admin/modules")
async def module_list(user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    """Modulerne til afkrydsningslisten, rollernes standard og hvad den indloggede selv må tildele – samt kontakten for
    godkendelse af nye brugere."""
    return {"modules": MODULES, "roles": {r: sorted(m) for r, m in ROLE_MODULES.items()},
            "role_names": dict(ROLE_NAMES), "grantable": sorted(modules(user)), "require_approval": await auth_service.approval_required(db)}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: int, body: dict[str, Any], user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if target.role == SUPERADMIN:
        raise HTTPException(400, "Superadmin styres fra .env og kan ikke ændres her")
    if "role" in body:
        role = body["role"]
        if role == SUPERADMIN:
            raise HTTPException(400, "Superadmin kan ikke tildeles – den styres fra .env")
        if role not in ROLE_MODULES:
            raise HTTPException(400, "Ukendt rolle")
        if (role == "admin" or target.role == "admin") and user.role != SUPERADMIN:
            raise HTTPException(403, "Kun superadmin kan ændre administratorer")
        if target.id == user.id and role != user.role:
            raise HTTPException(400, "Du kan ikke ændre din egen rolle")
        before = modules(target)
        target.role = role
        if user.role != "superadmin" and (before ^ modules(target)) - modules(user):
            raise HTTPException(403, "Rollen giver eller fjerner moduler, du ikke selv har adgang til")
    if "approved" in body:
        if target.id == user.id:
            raise HTTPException(400, "Du kan ikke ændre din egen godkendelse")
        if target.role in ("admin", "superadmin") and user.role != "superadmin":
            raise HTTPException(403, "Kun superadmin kan ændre administratorer")
        was = target.approved
        target.approved = bool(body["approved"])
        if not target.approved:  # fjernet godkendelse: log ud overalt
            async for key in kv().scan_iter("sess:*"):
                if await kv().get(key) == str(target.id):
                    await kv().delete(key)
        if was != target.approved:
            await activity.track(user.id, "approval", None, {"user": target.id, "approved": target.approved})
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
async def delete_user(user_id: int, user: User = Depends(perm_brugere), db: AsyncSession = Depends(get_session)):
    target = await db.get(User, user_id)
    if target is None or target.role == "superadmin":
        raise HTTPException(400)
    # Sletning af eksisterende brugere kræver superadmin; en afventende (afvist) ansøgning kan fjernes af dem, der styrer brugere
    if target.approved and not target.is_test and user.role != "superadmin":
        raise HTTPException(403, "Kun superadmin kan slette godkendte brugere")
    await account.delete(db, target)
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
    return [{**dict(r), "staff": r["role"] != "user"} for r in rows]


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
    if tier not in dict(datasync.TIERS):
        raise HTTPException(400, "Ukendt vindue")
    if source == "dof" and tier == "today":
        # Dagen i dag: én live-hentning med det samme, uden at vente på minimumsintervallet
        if not await gateway.request_live_now():
            raise HTTPException(429, f"Dagen er lige bestilt – vent {gateway.LIVE_NOW_COOLDOWN} sekunder")
        return {"queued": True, "now": True}
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


# ---------------------------------------------------------------- roller (kun superadmin)
# Roller med deres admin-moduler. "user" og "admin" kan ikke slettes (koden bruger dem), og "user" har aldrig moduler.
# Superadmin er ikke en rolle her: den har altid alle moduler og er brugeren fra .env.


class RoleIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    modules: list[str] | None = Field(None, max_length=len(MODULES))


def _role_key(name: str, taken: set[str]) -> str:
    base = "".join(c if c.isalnum() else "_" for c in name.lower().translate(str.maketrans("æøå", "aoa"))).strip("_")[:24] or "rolle"
    key, i = base, 2
    while key in taken or key == SUPERADMIN:
        key, i = f"{base}_{i}", i + 1
    return key


@router.get("/admin/roles")
async def roles(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Rollerne med moduler og antal brugere – og alle med en rolle eller individuelle rettigheder."""
    rows = (await db.execute(select(Role).order_by(Role.sort, Role.name))).scalars().all()
    counts = dict((await db.execute(text("SELECT role, count(*) FROM users GROUP BY role"))).all())
    people = (await db.execute(text("""
        SELECT id, obserkode, navn, role, permissions, blocked, approved, last_seen FROM users
        WHERE role <> 'user' OR permissions IS NOT NULL ORDER BY role = 'superadmin' DESC, role, navn
    """))).mappings().all()
    return {
        "modules": MODULES,
        "roles": [{"key": r.key, "name": r.name, "modules": sorted(ROLE_MODULES.get(r.key, ())), "fixed": r.key in FIXED_ROLES,
                   "locked": r.key == "user", "users": counts.get(r.key, 0)} for r in rows],
        "superadmin": {"obserkode": get_settings().superadmin_obserkode.upper() or None},
        "people": [{**dict(p), "modules": sorted(modules(User(role=p["role"], blocked=False, permissions=p["permissions"])))}
                   for p in people],
    }


@router.post("/admin/roles")
async def create_role(body: RoleIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    if not body.name:
        raise HTTPException(400, "Rollen skal have et navn")
    taken = set((await db.execute(select(Role.key))).scalars())
    sort = (await db.execute(text("SELECT coalesce(max(sort), 0) + 10 FROM roles"))).scalar()
    mods = sorted(set(body.modules or []) & set(MODULES))
    role = Role(key=_role_key(body.name, taken), name=body.name.strip(), modules=mods, sort=sort)
    db.add(role)
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"created": role.key})
    return {"key": role.key}


@router.patch("/admin/roles/{key}")
async def update_role(key: str, body: RoleIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    role = await db.get(Role, key)
    if role is None:
        raise HTTPException(404)
    if body.name is not None:
        role.name = body.name.strip()
    if body.modules is not None:
        if key == "user":
            raise HTTPException(400, "Rollen Bruger har ingen admin-moduler")
        unknown = set(body.modules) - set(MODULES)
        if unknown:
            raise HTTPException(400, f"Ukendte moduler: {', '.join(sorted(unknown))}")
        role.modules = sorted(set(body.modules))
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"updated": key})
    return {"ok": True}


@router.delete("/admin/roles/{key}")
async def delete_role(key: str, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Brugere med rollen bliver almindelige brugere (individuelle rettigheder bevares)."""
    if key in FIXED_ROLES:
        raise HTTPException(400, "Rollen kan ikke slettes")
    role = await db.get(Role, key)
    if role is None:
        raise HTTPException(404)
    moved = (await db.execute(text("UPDATE users SET role = 'user' WHERE role = :k"), {"k": key})).rowcount
    await db.delete(role)
    await db.commit()
    await auth_service.roles_changed()
    await activity.track(user.id, "roles", None, {"deleted": key, "users": moved})
    return {"ok": True, "users": moved}
