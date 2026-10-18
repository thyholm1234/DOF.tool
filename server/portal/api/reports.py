"""Feltmeldinger: ture og observationer indtastet i portalen (se services/reports.py)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.api.learn import MEDIA_TYPES
from portal.cache import kv, publish
from portal.config import get_settings
from portal.db import SessionLocal, get_session
from portal.models import FieldReport, FieldTrip, ThreadFollow, User
from portal.services import activity, reports
from portal.timeutil import dk_today

log = logging.getLogger("portal.reports")
router = APIRouter(prefix="/api/reports", tags=["reports"])

TIME = r"^([01]\d|2[0-3])[:.][0-5]\d$"


def _hhmm(v: str | None) -> str | None:
    return v.replace(".", ":") if v else None


class TripIn(BaseModel):
    loknr: int
    dato: date
    turtidfra: str | None = Field(None, pattern=TIME)
    turtidtil: str | None = Field(None, pattern=TIME)
    medobser: str | None = Field(None, max_length=300)
    turnoter: str | None = Field(None, max_length=2000)

    @field_validator("dato")
    @classmethod
    def not_future(cls, v: date) -> date:
        if v > dk_today():
            raise ValueError("Datoen ligger i fremtiden")
        return v


class TripPatch(BaseModel):
    dato: date | None = None
    turtidfra: str | None = Field(None, pattern=TIME)
    turtidtil: str | None = Field(None, pattern=TIME)
    medobser: str | None = Field(None, max_length=300)
    turnoter: str | None = Field(None, max_length=2000)


class ReportIn(BaseModel):
    artnr: int
    antal: int = Field(1, ge=0, le=9_900_000)
    koen: str | None = Field(None, pattern="^(M|F)$")
    adfkode: str | None = Field(None, max_length=16)
    alderkode: str | None = Field(None, max_length=8)
    dragtkode: str | None = Field(None, max_length=8)
    metode: str | None = Field(None, max_length=80)
    obstidfra: str | None = Field(None, pattern=TIME)
    obstidtil: str | None = Field(None, pattern=TIME)
    obs_lat: float | None = Field(None, ge=54.4, le=57.9)
    obs_lon: float | None = Field(None, ge=7.8, le=15.4)
    radius_m: int | None = Field(None, ge=0, le=50_000)
    fuglnoter: str | None = Field(None, max_length=2000)
    hemmelig: bool = False
    skjul_position: bool = False
    sikkerhed: str = Field("sikker", pattern="^(sikker|sandsynlig|mulig)$")
    grundlag: list[str] = Field(default_factory=list, max_length=6)
    status: str = Field("ukendt", pattern="^(til_stede|fløjet|ikke_genfundet|ukendt)$")

    @field_validator("grundlag")
    @classmethod
    def known_basis(cls, v: list[str]) -> list[str]:
        bad = [g for g in v if g not in reports.GRUNDLAG]
        if bad:
            raise ValueError(f"Ukendt grundlag: {', '.join(bad)}")
        return list(dict.fromkeys(v))


class StatusIn(BaseModel):
    status: str = Field(pattern="^(til_stede|fløjet|ikke_genfundet)$")
    note: str | None = Field(None, max_length=300)


async def _in_background(fn, *args) -> None:
    async with SessionLocal() as s:
        try:
            await fn(s, *args)
        except Exception:  # noqa: BLE001 – en fejlet notifikation må ikke vælte indtastningen
            log.exception("Notifikation for feltmelding fejlede")


async def _own_trip(db: AsyncSession, trip_id: int, user: User) -> FieldTrip:
    trip = await db.get(FieldTrip, trip_id)
    if trip is None or trip.user_id != user.id:
        raise HTTPException(404, "Turen findes ikke")
    return trip


async def _own_report(db: AsyncSession, report_id: int, user: User) -> FieldReport:
    r = await db.get(FieldReport, report_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Observationen findes ikke")
    return r


def _remove_files(media: list | None) -> None:
    root = Path(get_settings().media_dir) / "obs"
    for m in media or []:
        path = root / Path(m.get("url", "")).name
        if path.is_file():
            path.unlink()


async def _one(db: AsyncSession, r: FieldReport, user: User) -> dict:
    rows = await reports.thread_reports(db, r.dato, r.artnr, r.loknr, user)
    return next(x for x in rows if x["id"] == r.id)


async def _validate_codes(db: AsyncSession, body: ReportIn | dict) -> None:
    data = body if isinstance(body, dict) else body.model_dump()
    if "artnr" in data and not (await db.execute(text("SELECT 1 FROM species WHERE artnr = :a AND NOT excluded"),
                                                {"a": data["artnr"]})).scalar():
        raise HTTPException(400, "Ukendt art")
    for field, kind in (("adfkode", "adf"), ("dragtkode", "dragt")):
        if data.get(field) and not (await db.execute(text("SELECT 1 FROM code_texts WHERE kind = :k AND code = :c"),
                                                     {"k": kind, "c": data[field]})).scalar():
            raise HTTPException(400, f"Ukendt kode i {field}")


async def _location_exists(db: AsyncSession, loknr: int) -> None:
    if not (await db.execute(text("SELECT 1 FROM locations WHERE loknr = :l"), {"l": loknr})).scalar():
        raise HTTPException(400, "Ukendt lokalitet")


# ---------------------------------------------------------------- opslag


@router.get("/codes")
async def codes(db: AsyncSession = Depends(get_session)):
    return await reports.codes(db)


@router.get("/species")
async def species(db: AsyncSession = Depends(get_session)):
    return await reports.species_list(db)


@router.get("/near")
async def near(lat: float = Query(ge=54, le=58.5), lon: float = Query(ge=7.5, le=15.7), db: AsyncSession = Depends(get_session)):
    return await reports.nearest_locations(db, lat, lon)


@router.get("/recent")
async def recent(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await reports.recent(db, user)


# ---------------------------------------------------------------- ture


@router.get("/trips")
async def trips(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await reports.my_trips(db, user)


@router.post("/trips")
async def create_trip(body: TripIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await _location_exists(db, body.loknr)
    trip = FieldTrip(user_id=user.id, obserkode=user.obserkode, dato=body.dato, loknr=body.loknr,
                     turtidfra=_hhmm(body.turtidfra), turtidtil=_hhmm(body.turtidtil),
                     medobser=body.medobser, turnoter=body.turnoter)
    db.add(trip)
    await db.commit()
    return await reports.trip_dict(db, trip, user)


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await reports.trip_dict(db, await _own_trip(db, trip_id, user), user)


@router.patch("/trips/{trip_id}")
async def patch_trip(trip_id: int, body: TripPatch, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    trip = await _own_trip(db, trip_id, user)
    data = body.model_dump(exclude_unset=True)
    if data.get("dato") and data["dato"] > dk_today():
        raise HTTPException(400, "Datoen ligger i fremtiden")
    for k, v in data.items():
        setattr(trip, k, _hhmm(v) if k.startswith("turtid") else v)
    if "dato" in data:
        await db.execute(text("UPDATE field_reports SET dato = :d WHERE trip_id = :t"), {"d": trip.dato, "t": trip.id})
    await db.commit()
    await publish("feed", {"type": "feed", "portal": True})
    return await reports.trip_dict(db, trip, user)


@router.delete("/trips/{trip_id}")
async def delete_trip(trip_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    trip = await _own_trip(db, trip_id, user)
    media = (await db.execute(text("SELECT media FROM field_reports WHERE trip_id = :t"), {"t": trip.id})).scalars().all()
    await db.delete(trip)
    await db.commit()
    for m in media:
        _remove_files(m)
    await publish("feed", {"type": "feed", "portal": True})
    return {"ok": True}


# ---------------------------------------------------------------- observationer


@router.post("/trips/{trip_id}/reports")
async def create_report(trip_id: int, body: ReportIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                        db: AsyncSession = Depends(get_session)):
    trip = await _own_trip(db, trip_id, user)
    await _validate_codes(db, body)
    data = body.model_dump()
    status = data.pop("status")
    r = FieldReport(trip_id=trip.id, user_id=user.id, obserkode=user.obserkode, dato=trip.dato, loknr=trip.loknr,
                    **{**data, "obstidfra": _hhmm(body.obstidfra), "obstidtil": _hhmm(body.obstidtil)})
    await reports.classify(db, r)
    db.add(r)
    await db.flush()
    if status != "ukendt":
        await reports.add_status(db, r, user, status, None)
    # Indtasteren følger automatisk tråden og får dermed statusmeldinger fra andre
    if not await db.get(ThreadFollow, (user.id, r.dato, r.artnr, r.loknr)):
        db.add(ThreadFollow(user_id=user.id, dato=r.dato, artnr=r.artnr, loknr=r.loknr))
    await db.commit()
    tasks.add_task(activity.track, user.id, "report", None, {"artnr": r.artnr, "loknr": r.loknr, "kategori": r.kategori})
    if not r.hemmelig:
        tasks.add_task(_in_background, reports.notify_new, [r.id])
        await publish("feed", {"type": "feed", "portal": True, "new": 1})
    return await _one(db, r, user)


@router.patch("/{report_id}")
async def patch_report(report_id: int, body: dict, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    merged = ReportIn(**{**{k: getattr(r, k) for k in ReportIn.model_fields if k != "status"}, **body,
                         "status": "ukendt"})
    await _validate_codes(db, merged)
    for k, v in merged.model_dump().items():
        if k == "status":
            continue
        setattr(r, k, _hhmm(v) if k in ("obstidfra", "obstidtil") else v)
    await reports.classify(db, r)
    r.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await publish("feed", {"type": "feed", "portal": True})
    return await _one(db, r, user)


@router.delete("/{report_id}")
async def delete_report(report_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    media = list(r.media or [])
    await db.delete(r)
    await db.commit()
    _remove_files(media)
    await publish("feed", {"type": "feed", "portal": True})
    return {"ok": True}


@router.post("/{report_id}/status")
async def set_status(report_id: int, body: StatusIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                     db: AsyncSession = Depends(get_session)):
    """Alle indloggede kan melde status på en synlig feltmelding fra de seneste to dage."""
    r = await db.get(FieldReport, report_id)
    if r is None or (r.hemmelig and r.user_id != user.id):
        raise HTTPException(404, "Observationen findes ikke")
    if r.dato < dk_today() - timedelta(days=1):
        raise HTTPException(400, "Status kan kun meldes samme dag eller dagen efter")
    if not await kv().set(f"report:status:rl:{user.id}:{r.id}", "1", nx=True, ex=20):
        raise HTTPException(429, "Du har lige meldt status – vent lidt")
    upd = await reports.add_status(db, r, user, body.status, body.note)
    await db.commit()
    tasks.add_task(_in_background, reports.notify_status, r.id, upd.id)
    await publish("feed", {"type": "feed", "portal": True})
    return await _one(db, r, user)


@router.post("/{report_id}/media")
async def upload_media(report_id: int, file: UploadFile = File(...), user: User = Depends(require_user),
                       db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    if len(r.media or []) >= 8:
        raise HTTPException(400, "Højst 8 filer pr. observation")
    ext = MEDIA_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Kun billeder (jpg/png/webp/gif) og lyd (mp3/ogg/wav/m4a/webm)")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Filen er større end 15 MB")
    root = Path(get_settings().media_dir) / "obs"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (root / name).write_bytes(data)
    kind = "audio" if ext in (".mp3", ".ogg", ".wav", ".m4a", ".webm") else "image"
    r.media = [*(r.media or []), {"url": f"/media/obs/{name}", "kind": kind}]
    if kind == "image" and "foto" not in (r.grundlag or []):
        r.grundlag = [*(r.grundlag or []), "foto"]
    if kind == "audio" and "lyd" not in (r.grundlag or []):
        r.grundlag = [*(r.grundlag or []), "lyd"]
    await db.commit()
    return {"media": r.media, "grundlag": r.grundlag}


@router.delete("/{report_id}/media")
async def delete_media(report_id: int, url: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    if not any(m["url"] == url for m in r.media or []):
        raise HTTPException(404)
    r.media = [m for m in r.media if m["url"] != url]
    await db.commit()
    _remove_files([{"url": url}])
    return {"media": r.media}


@router.get("/export.csv")
async def export(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    body = await reports.export_csv(db, user)
    return Response("﻿" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="feltmeldinger-{user.obserkode}.csv"'})
