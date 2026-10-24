"""Feltmeldinger: fugle indtastet i portalen – én fugl med dato, koordinat og lokalitet (se services/reports.py)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.api.learn import MEDIA_TYPES
from portal.cache import publish
from portal.db import SessionLocal, get_session
from portal.models import FieldReport, ThreadFollow, User
from portal.services import activity, imagemeta, pulse, reports, storage
from portal.timeutil import dk_today

log = logging.getLogger("portal.reports")
router = APIRouter(prefix="/api/reports", tags=["reports"])

TIME = r"^([01]\d|2[0-3])[:.][0-5]\d$"


def _hhmm(v: str | None) -> str | None:
    return v.replace(".", ":") if v else None


class ReportIn(BaseModel):
    artnr: int
    dato: date | None = None  # standard: i dag
    loknr: int | None = None  # standard: nærmeste lokalitet til fuglens position
    antal: int = Field(1, ge=0, le=9_900_000)
    antalmetode: int | None = Field(None, ge=1, le=3)  # 1 uangivet, 2 præcis, 3 estimat
    maerker: list[str] = Field(default_factory=list, max_length=12)  # kalder, hoert, traek_ind, burfugl …
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
    retningslinjer: bool = False  # DOF: "Når du ser en sjælden fugl" – påkrævet for SU/SUB

    @field_validator("dato")
    @classmethod
    def not_future(cls, v: date | None) -> date | None:
        if v and v > dk_today():
            raise ValueError("Datoen ligger i fremtiden")
        return v

    @field_validator("maerker")
    @classmethod
    def known_marks(cls, v: list[str]) -> list[str]:
        bad = [m for m in v if m not in reports.MAERKER]
        if bad:
            raise ValueError(f"Ukendt markering: {', '.join(bad)}")
        return list(dict.fromkeys(v))

    @field_validator("grundlag")
    @classmethod
    def known_basis(cls, v: list[str]) -> list[str]:
        bad = [g for g in v if g not in reports.GRUNDLAG]
        if bad:
            raise ValueError(f"Ukendt grundlag: {', '.join(bad)}")
        return list(dict.fromkeys(v))


async def _in_background(fn, *args) -> None:
    async with SessionLocal() as s:
        try:
            await fn(s, *args)
        except Exception:  # noqa: BLE001 – en fejlet notifikation må ikke vælte indtastningen
            log.exception("Notifikation for feltmelding fejlede")


async def _own_report(db: AsyncSession, report_id: int, user: User) -> FieldReport:
    r = await db.get(FieldReport, report_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "Observationen findes ikke")
    return r


async def _remove_files(media: list | None) -> None:
    await storage.delete_urls(m.get("url") for m in media or [])


def _check_guidelines(r: FieldReport) -> None:
    if r.kategori in ("SU", "SUB") and not r.retningslinjer:
        raise HTTPException(400, "Bekræft at du følger DOF's retningslinjer for sjældne fugle")


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


async def _location_for(db: AsyncSession, body: ReportIn) -> int:
    """Den valgte lokalitet, ellers den nærmeste til fuglens position."""
    if body.loknr is not None:
        await _location_exists(db, body.loknr)
        return body.loknr
    near = await reports.nearest_locations(db, body.obs_lat, body.obs_lon, n=1)
    if not near:
        raise HTTPException(400, "Fandt ingen lokalitet nær positionen")
    return near[0]["loknr"]


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


# ---------------------------------------------------------------- observationer


@router.get("/mine")
async def mine(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Alle egne indtastede fugle som én liste (sorteres i browseren)."""
    return await reports.my_reports(db, user)


@router.post("")
async def create_report(body: ReportIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                        db: AsyncSession = Depends(get_session)):
    await _validate_codes(db, body)
    if body.obs_lat is None or body.obs_lon is None:
        raise HTTPException(400, "Sæt fuglens position på kortet")
    if not body.obstidfra:
        raise HTTPException(400, "Angiv tidspunkt for observationen")
    loknr = await _location_for(db, body)
    data = body.model_dump()
    status = data.pop("status")
    r = FieldReport(user_id=user.id, obserkode=user.obserkode,
                    **{**data, "dato": body.dato or dk_today(), "loknr": loknr,
                       "obstidfra": _hhmm(body.obstidfra), "obstidtil": _hhmm(body.obstidtil)})
    await reports.classify(db, r)
    _check_guidelines(r)
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
        await pulse.record_report(db, r)
        await publish("feed", {"type": "feed", "portal": True, "new": 1})
    return await _one(db, r, user)


@router.get("/{report_id:int}")
async def get_report(report_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await _one(db, await _own_report(db, report_id, user), user)


@router.patch("/{report_id}")
async def patch_report(report_id: int, body: dict, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    try:
        merged = ReportIn(**{**{k: getattr(r, k) for k in ReportIn.model_fields if k != "status"}, **body,
                             "status": "ukendt"})
    except ValidationError as err:
        raise HTTPException(400, "; ".join(e["msg"].removeprefix("Value error, ") for e in err.errors())) from None
    await _validate_codes(db, merged)
    if merged.loknr != r.loknr:
        await _location_exists(db, merged.loknr)
    before = (r.artnr, r.loknr, r.dato, r.hemmelig)
    for k, v in merged.model_dump().items():
        if k == "status" or (k in ("dato", "loknr") and v is None):
            continue
        setattr(r, k, _hhmm(v) if k in ("obstidfra", "obstidtil") else v)
    await reports.classify(db, r)
    _check_guidelines(r)
    r.updated_at = datetime.now(timezone.utc)
    await db.commit()
    # Puls: ændres art, sted, dag eller skjult-status, hører meldingen ikke længere hjemme på det gamle punkt
    if before != (r.artnr, r.loknr, r.dato, r.hemmelig):
        await pulse.record_deleted([-report_id])
        if not r.hemmelig and r.dato == dk_today():
            await pulse.record_report(db, r)
    await publish("feed", {"type": "feed", "portal": True})
    return await _one(db, r, user)


@router.delete("/{report_id}")
async def delete_report(report_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    r = await _own_report(db, report_id, user)
    media = list(r.media or [])
    await db.delete(r)
    await db.commit()
    await pulse.record_deleted([-report_id])
    await _remove_files(media)
    await publish("feed", {"type": "feed", "portal": True})
    return {"ok": True}


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
    if not ext.endswith((".mp3", ".ogg", ".wav", ".m4a", ".webm")):
        try:
            data = imagemeta.strip(data)  # GPS-position og andre metadata fjernes, før filen gemmes
        except ValueError as exc:
            raise HTTPException(400, "Billedet kunne ikke læses") from exc
    try:
        url = await storage.put(f"{uuid.uuid4().hex}{ext}", data, file.content_type or "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        log.warning("Upload til objektlageret fejlede: %s", exc)
        raise HTTPException(503, "Filen kunne ikke gemmes lige nu – prøv igen om lidt") from exc
    kind = "audio" if ext in (".mp3", ".ogg", ".wav", ".m4a", ".webm") else "image"
    r.media = [*(r.media or []), {"url": url, "kind": kind}]
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
    await _remove_files([{"url": url}])
    return {"media": r.media}
