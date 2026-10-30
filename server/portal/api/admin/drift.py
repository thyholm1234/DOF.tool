"""Admin → Drift: lager, overblik, gateway og kø, worker-kommandoer, planlagte opgaver, lokale hits, arkivupload, talemodel og jobstatus."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.api.admin._deps import (
    perm_drift,
)
from portal.api.admin.indstillinger import get_settings_dict
from portal.cache import kv, worker_command
from portal.config import get_settings
from portal.db import get_session
from portal.dof import gateway
from portal.dof.archive import (
    FILE_RE,
    PROGRESS,
    archive_files,
    is_month_file,
    pending_files,
)
from portal.models import (
    User,
)
from portal.services import (
    diskusage,
    locfinds,
    schedule,
    speechmodel,
)

router = APIRouter(prefix="/api", tags=["admin"])


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
