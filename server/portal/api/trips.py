"""Ture: indtastning som i app.dofbasen.dk – en tur med lokalitet, tider, metode, vejr og GPS-rute, og under den
observationer, ynglepar og forstyrrelser. Turen gemmes i portalens egen database og mærkes som indtastet i portalen
(kilde "portal"); den sendes ikke til DOFbasen. Hele turen sendes og gemmes i ét kald, så en offline-kø kan uploade
den, og et gentaget kald med samme client_id giver ikke dubletter."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.api.reports import ReportIn, TIME, _check_guidelines, _hhmm, _in_background, _location_exists, _remove_files, _validate_codes
from portal.cache import publish
from portal.db import get_session
from portal.models import FieldBreedingPair, FieldDisturbance, FieldReport, FieldTrip, ThreadFollow, User
from portal.services import activity, dku, pulse, reports
from portal.timeutil import dk_today

log = logging.getLogger("portal.trips")
router = APIRouter(prefix="/api/trips", tags=["trips"])

LOOKUPS = json.loads((Path(__file__).resolve().parent.parent / "seed" / "trip_lookups.json").read_text(encoding="utf-8"))
VIND = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]
NEDBOER = ["Regn", "Hagl", "Slud", "Sne"]
METODE_IDS = {m["id"] for m in LOOKUPS["metoder"]}
FORSTYRRELSE_IDS = {m["id"] for m in LOOKUPS["forstyrrelser"]}
CID = r"^[A-Za-z0-9_-]{8,40}$"


class ObsIn(ReportIn):
    """Én observation på turen. Dato og lokalitet er turens; position og tidspunkt kan udelades."""

    id: int | None = None
    client_id: str | None = Field(None, pattern=CID)
    dato: date | None = None
    loknr: int | None = None


class PairIn(BaseModel):
    id: int | None = None
    client_id: str | None = Field(None, pattern=CID)
    artnr: int
    antal: int = Field(1, ge=1, le=100_000)
    adfkode: str | None = Field(None, max_length=16)
    lat: float | None = Field(None, ge=54.4, le=57.9)
    lon: float | None = Field(None, ge=7.8, le=15.4)
    noter: str | None = Field(None, max_length=2000)
    hemmelig: bool = False


class DisturbanceIn(BaseModel):
    id: int | None = None
    client_id: str | None = Field(None, pattern=CID)
    type_id: int
    antal: int | None = Field(None, ge=0, le=100_000)
    noter: str | None = Field(None, max_length=1000)

    @field_validator("type_id")
    @classmethod
    def known_type(cls, v: int) -> int:
        if v not in FORSTYRRELSE_IDS:
            raise ValueError("Ukendt forstyrrelse")
        return v


class TripIn(BaseModel):
    client_id: str | None = Field(None, pattern=CID)
    dato: date
    loknr: int
    turtidfra: str | None = Field(None, pattern=TIME)
    turtidtil: str | None = Field(None, pattern=TIME)
    metode_id: int = 1
    komplet: bool | None = None
    spontan: bool = False
    medobser: str | None = Field(None, max_length=300)
    turnoter: str | None = Field(None, max_length=4000)
    on_behalf_of: str | None = Field(None, pattern=r"^[A-Za-z0-9]{2,16}$")
    lat: float | None = Field(None, ge=54.4, le=57.9)
    lon: float | None = Field(None, ge=7.8, le=15.4)
    temperatur: int | None = Field(None, ge=-60, le=60)
    skydaekke: int | None = Field(None, ge=0, le=8)
    vindretning: str | None = None
    vindstyrke: int | None = Field(None, ge=0, le=50)
    nedboer: int | None = Field(None, ge=0, le=1000)
    nedboertype: str | None = None
    sigtbarhed: int | None = Field(None, ge=0, le=100_000)
    gps_aktiv: bool = False
    gps_track: list[list[float]] | None = Field(None, max_length=20_000)
    observations: list[ObsIn] = Field(default_factory=list, max_length=500)
    breeding_pairs: list[PairIn] = Field(default_factory=list, max_length=200)
    disturbances: list[DisturbanceIn] = Field(default_factory=list, max_length=40)

    @field_validator("dato")
    @classmethod
    def not_future(cls, v: date) -> date:
        if v > dk_today():
            raise ValueError("Datoen ligger i fremtiden")
        return v

    @field_validator("metode_id")
    @classmethod
    def known_method(cls, v: int) -> int:
        if v not in METODE_IDS:
            raise ValueError("Ukendt metode")
        return v

    @field_validator("vindretning")
    @classmethod
    def known_wind(cls, v: str | None) -> str | None:
        if v and v not in VIND:
            raise ValueError("Ukendt vindretning")
        return v or None

    @field_validator("nedboertype")
    @classmethod
    def known_rain(cls, v: str | None) -> str | None:
        if v and v not in NEDBOER:
            raise ValueError("Ukendt nedbørstype")
        return v or None

    @field_validator("gps_track")
    @classmethod
    def track_points(cls, v: list[list[float]] | None) -> list[list[float]] | None:
        if v and any(len(p) < 2 or not (54 <= p[0] <= 58.5 and 7.5 <= p[1] <= 15.7) for p in v):
            raise ValueError("Ugyldigt punkt i GPS-ruten")
        return v

    @model_validator(mode="after")
    def times(self) -> "TripIn":
        if self.turtidfra and self.turtidtil and _hhmm(self.turtidtil) < _hhmm(self.turtidfra):
            raise ValueError("Sluttidspunktet ligger før starten")
        return self


# ---------------------------------------------------------------- opslag


@router.get("/lookups")
async def lookups():
    return {**LOOKUPS, "vindretninger": VIND, "nedboertyper": NEDBOER}


@router.get("/mine")
async def mine(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT t.id, t.dato, t.turtidfra, t.turtidtil, t.loknr, l.navn AS lok, k.navn AS kommune, t.obserkode,
               t.metode_id, t.komplet, t.spontan, t.gps_aktiv, t.created_at,
               (SELECT count(*) FROM field_reports r WHERE r.trip_id = t.id) AS n_obs,
               (SELECT count(DISTINCT r.artnr) FROM field_reports r WHERE r.trip_id = t.id) AS n_arter,
               (SELECT count(*) FROM field_breeding_pairs p WHERE p.trip_id = t.id) AS n_ynglepar,
               (SELECT count(*) FROM field_disturbances d WHERE d.trip_id = t.id) AS n_forstyrrelser
        FROM field_trips t JOIN locations l ON l.loknr = t.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE t.user_id = :u ORDER BY t.dato DESC, t.turtidfra DESC NULLS LAST, t.id DESC LIMIT 500
    """), {"u": user.id})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat(), "created_at": r["created_at"].isoformat(),
             "for_andre": r["obserkode"] != user.obserkode} for r in rows]


async def _own_trip(db: AsyncSession, trip_id: int, user: User) -> FieldTrip:
    t = await db.get(FieldTrip, trip_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "Turen findes ikke")
    return t


async def trip_out(db: AsyncSession, t: FieldTrip, user: User) -> dict:
    loc = (await db.execute(text("""
        SELECT l.navn, l.lat, l.lon, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.loknr = :l"""), {"l": t.loknr})).mappings().first()
    obs = await reports._report_rows(db, "r.trip_id = :t", {"t": t.id}, user)
    pairs = (await db.execute(select(FieldBreedingPair).where(FieldBreedingPair.trip_id == t.id)
                              .order_by(FieldBreedingPair.id))).scalars().all()
    names = {r[0]: r[1] for r in (await db.execute(text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"),
                                                      {"a": [p.artnr for p in pairs]})).all()} if pairs else {}
    dist = (await db.execute(select(FieldDisturbance).where(FieldDisturbance.trip_id == t.id)
                             .order_by(FieldDisturbance.id))).scalars().all()
    head = {k: getattr(t, k) for k in ("id", "obserkode", "client_id", "loknr", "turtidfra", "turtidtil", "metode_id", "komplet",
                                        "spontan", "medobser", "turnoter", "lat", "lon", "temperatur", "skydaekke",
                                        "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed", "gps_aktiv",
                                        "gps_track")}
    return {**head, "kilde": "portal", "dato": t.dato.isoformat(), "created_at": t.created_at.isoformat(),
            "lok": loc["navn"] if loc else None, "kommune": loc["kommune"] if loc else None,
            "lok_lat": loc["lat"] if loc else None, "lok_lon": loc["lon"] if loc else None,
            "for_andre": t.obserkode != user.obserkode,
            "observations": obs,
            "breeding_pairs": [{"id": p.id, "client_id": p.client_id, "artnr": p.artnr, "art": names.get(p.artnr), "antal": p.antal,
                                "adfkode": p.adfkode, "lat": p.lat, "lon": p.lon, "noter": p.noter, "hemmelig": p.hemmelig}
                               for p in pairs],
            "disturbances": [{"id": d.id, "client_id": d.client_id, "type_id": d.type_id, "antal": d.antal, "noter": d.noter}
                             for d in dist]}


@router.get("/{trip_id:int}")
async def get_trip(trip_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await trip_out(db, await _own_trip(db, trip_id, user), user)


# ---------------------------------------------------------------- gem


async def _observer_code(db: AsyncSession, user: User, code: str | None) -> str:
    """Observatøren: brugeren selv, eller en kendt obserkode, når der indtastes på vegne af en anden."""
    if not code or code.upper() == user.obserkode.upper():
        return user.obserkode
    code = code.upper()
    known = (await db.execute(text("SELECT 1 FROM users WHERE upper(obserkode) = :c LIMIT 1"), {"c": code})).scalar() or \
        (await db.execute(text("SELECT 1 FROM observations WHERE obserkode = :c LIMIT 1"), {"c": code})).scalar()
    if not known:
        raise HTTPException(400, f"Obserkoden {code} er ikke kendt")
    return code


async def _save(db: AsyncSession, user: User, trip: FieldTrip, body: TripIn) -> tuple[list[int], list[int], list[int]]:
    """Skriver turen og dens børn. Returnerer id'er på nye, ændrede og slettede observationer."""
    await _location_exists(db, body.loknr)
    trip.obserkode = await _observer_code(db, user, body.on_behalf_of)
    for k in ("dato", "loknr", "turtidfra", "turtidtil", "metode_id", "komplet", "spontan", "medobser", "turnoter", "lat", "lon",
              "temperatur", "skydaekke", "vindretning", "vindstyrke", "nedboer", "nedboertype", "sigtbarhed", "gps_aktiv"):
        setattr(trip, k, _hhmm(getattr(body, k)) if k in ("turtidfra", "turtidtil") else getattr(body, k))
    if body.gps_track is not None or not trip.id:
        trip.gps_track = body.gps_track
    trip.updated_at = datetime.now(timezone.utc)
    if body.client_id:
        trip.client_id = body.client_id
    if not trip.id:
        db.add(trip)
    await db.flush()

    # Observationer: match på id, ellers client_id; resten oprettes, og det der ikke er med længere slettes
    existing = {r.id: r for r in (await db.execute(select(FieldReport).where(FieldReport.trip_id == trip.id))).scalars()}
    by_cid = {r.client_id: r for r in existing.values() if r.client_id}
    created: list[int] = []
    changed: list[int] = []
    keep: set[int] = set()
    for o in body.observations:
        await _validate_codes(db, o)
        data = o.model_dump(exclude={"id", "status", "client_id", "dato", "loknr"})
        data.update(dato=trip.dato, loknr=trip.loknr, obstidfra=_hhmm(o.obstidfra) or trip.turtidfra,
                    obstidtil=_hhmm(o.obstidtil))
        r = existing.get(o.id) if o.id else by_cid.get(o.client_id) if o.client_id else None
        if o.id and r is None:
            raise HTTPException(400, "Ukendt observation i turen")
        if r is None:
            r = FieldReport(user_id=user.id, trip_id=trip.id, client_id=o.client_id, **data)
            r.obserkode = trip.obserkode
            await reports.classify(db, r)
            _check_guidelines(r)
            db.add(r)
            await db.flush()
            if o.status != "ukendt":
                await reports.add_status(db, r, user, o.status, None)
            if not await db.get(ThreadFollow, (user.id, r.dato, r.artnr, r.loknr)):
                db.add(ThreadFollow(user_id=user.id, dato=r.dato, artnr=r.artnr, loknr=r.loknr))
            created.append(r.id)
        else:
            before = (r.artnr, r.loknr, r.dato, r.hemmelig)
            for k, v in data.items():
                setattr(r, k, v)
            r.obserkode = trip.obserkode
            r.client_id = r.client_id or o.client_id
            await reports.classify(db, r)
            _check_guidelines(r)
            r.updated_at = datetime.now(timezone.utc)
            if before != (r.artnr, r.loknr, r.dato, r.hemmelig):
                changed.append(r.id)
        keep.add(r.id)
    gone = [i for i in existing if i not in keep]
    for i in gone:
        await _remove_files(existing[i].media)
        await db.delete(existing[i])

    for p in body.breeding_pairs:
        if not (await db.execute(text("SELECT 1 FROM species WHERE artnr = :a AND NOT excluded"), {"a": p.artnr})).scalar():
            raise HTTPException(400, "Ukendt art i ynglepar")
        if p.adfkode and not (await db.execute(text("SELECT 1 FROM code_texts WHERE kind = 'adf' AND code = :c"),
                                               {"c": p.adfkode})).scalar():
            raise HTTPException(400, "Ukendt adfærd i ynglepar")
    await _sync_children(db, trip, FieldBreedingPair, body.breeding_pairs,
                         lambda p: {"artnr": p.artnr, "antal": p.antal, "adfkode": p.adfkode, "lat": p.lat, "lon": p.lon,
                                    "noter": p.noter, "hemmelig": p.hemmelig})
    await _sync_children(db, trip, FieldDisturbance, body.disturbances,
                         lambda d: {"type_id": d.type_id, "antal": d.antal, "noter": d.noter})
    return created, changed, gone


async def _sync_children(db: AsyncSession, trip: FieldTrip, model, items, fields) -> None:
    rows = (await db.execute(select(model).where(model.trip_id == trip.id))).scalars().all()
    by_id = {r.id: r for r in rows}
    by_cid = {r.client_id: r for r in rows if r.client_id}
    keep: set[int] = set()
    for it in items:
        row = by_id.get(it.id) if it.id else by_cid.get(it.client_id) if it.client_id else None
        if it.id and row is None:
            raise HTTPException(400, "Ukendt post i turen")
        if row is None:
            row = model(trip_id=trip.id, client_id=it.client_id)
            db.add(row)
        for k, v in fields(it).items():
            setattr(row, k, v)
        await db.flush()
        keep.add(row.id)
    for r in rows:
        if r.id not in keep:
            await db.delete(r)


async def _after(db: AsyncSession, tasks: BackgroundTasks, user: User, trip: FieldTrip, created: list[int],
                 changed: list[int], gone: list[int]) -> None:
    """Notifikationer, puls og live-opdatering – først når turen er gemt."""
    if gone or changed:
        await pulse.record_deleted([-i for i in gone + changed])
    visible = [r for r in (await db.execute(select(FieldReport).where(FieldReport.id.in_(created + changed)))).scalars()
               if not r.hemmelig]
    if created:
        new = [r.id for r in visible if r.id in created]
        if new:
            tasks.add_task(_in_background, reports.notify_new, new)
    for r in visible:
        if r.dato == dk_today():
            await pulse.record_report(db, r)
    tasks.add_task(activity.track, user.id, "trip", None, {"loknr": trip.loknr, "n_obs": len(created)})
    await publish("feed", {"type": "feed", "portal": True, "new": len(created)})


@router.post("")
async def create_trip(body: TripIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_session)):
    if body.client_id:
        old = (await db.execute(select(FieldTrip).where(FieldTrip.user_id == user.id, FieldTrip.client_id == body.client_id))).scalar()
        if old:  # samme tur sendt igen (fx efter tabt svar): returnér den, der allerede er gemt
            return await trip_out(db, old, user)
    trip = FieldTrip(user_id=user.id)
    created, changed, gone = await _save(db, user, trip, body)
    await db.commit()
    await _after(db, tasks, user, trip, created, changed, gone)
    return await trip_out(db, trip, user)


@router.put("/{trip_id:int}")
async def update_trip(trip_id: int, body: TripIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_session)):
    trip = await _own_trip(db, trip_id, user)
    created, changed, gone = await _save(db, user, trip, body)
    await db.commit()
    await _after(db, tasks, user, trip, created, changed, gone)
    for rid in (await db.execute(select(FieldReport.id).where(FieldReport.trip_id == trip.id))).scalars():
        tasks.add_task(dku.report_changed, rid)  # åbne DKU-sager om turens meldinger får ændringerne
    return await trip_out(db, trip, user)


@router.delete("/{trip_id:int}")
async def delete_trip(trip_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    trip = await _own_trip(db, trip_id, user)
    rows = (await db.execute(select(FieldReport.id, FieldReport.media).where(FieldReport.trip_id == trip.id))).all()
    await db.execute(delete(FieldTrip).where(FieldTrip.id == trip.id))  # børnene følger med (ON DELETE CASCADE)
    await db.commit()
    await pulse.record_deleted([-i for i, _ in rows])
    for _, media in rows:
        await _remove_files(media)
    await publish("feed", {"type": "feed", "portal": True})
    return {"ok": True}
