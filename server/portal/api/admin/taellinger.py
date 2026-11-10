"""Admin → Tællinger: spontan- og FiNN-tællinger – status og statistik, udtræk, FiNN-områder (ret og synkronisér med
DOFbasen) og gridceller. Kræver admin-modulet taellinger. SQL ligger i services/taellinger.py og services/finn_admin.py."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm
from portal.db import get_session
from portal.models import FinnSync, User
from portal.services import finn_admin, sheets, taellinger
from portal.services.finn_admin import FinnError

router = APIRouter(prefix="/api/admin/taellinger", tags=["admin"])
perm = require_perm("taellinger")
Kind = Literal["alle", "spontan", "finn"]
CELL = r"^(1|2|5|10)km_\d{1,5}_\d{1,4}$"


def _filter(kind: Kind = Query("alle"), fra: date | None = Query(None), til: date | None = Query(None),
            site: int | None = Query(None, ge=1), obserkode: str | None = Query(None, pattern=r"^[A-Za-z0-9]{2,16}$"),
            artnr: int | None = Query(None, ge=1), cell: str | None = Query(None, pattern=CELL)) -> taellinger.Filter:
    if fra and til and til < fra:
        raise HTTPException(400, "Slutdatoen ligger før startdatoen")
    return taellinger.Filter(kind=kind, fra=fra, til=til, site=site, obserkode=obserkode, artnr=artnr, cell=cell)


def _err(exc: FinnError) -> HTTPException:
    return HTTPException(400, str(exc))


# ---------------------------------------------------------------- status, statistik og tællinger


@router.get("/status")
async def status(user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    return await taellinger.status(db)


@router.get("/oversigt")
async def overview(f: taellinger.Filter = Depends(_filter), user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    return await taellinger.overview(db, f)


@router.get("/liste")
async def trips(f: taellinger.Filter = Depends(_filter), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    return await taellinger.trips_page(db, f, limit, offset)


@router.get("/liste/{tid}")
async def trip(tid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    d = await taellinger.trip_detail(db, tid)
    if d is None:
        raise HTTPException(404, "Tællingen findes ikke")
    return d


@router.get("/udtraek")
async def export(f: taellinger.Filter = Depends(_filter), niveau: Literal["taellinger", "observationer"] = Query("taellinger"),
                 fmt: Literal["csv", "xlsx", "ods"] = Query("csv"), user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    cols, rows = await taellinger.export_rows(db, f, niveau)
    name = f"{'taellinger' if niveau == 'taellinger' else 'taellinger-observationer'}-{f.kind}"
    if f.fra or f.til:
        name += f"-{f.fra or ''}_{f.til or ''}"
    return Response(sheets.write(fmt, cols, rows, "Tællinger" if niveau == "taellinger" else "Observationer"),
                    media_type=sheets.FORMATS[fmt], headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


# ---------------------------------------------------------------- gridceller


@router.get("/grid")
async def grid(f: taellinger.Filter = Depends(_filter), km: Literal[1, 2, 5, 10] = Query(10), user: User = Depends(perm),
               db: AsyncSession = Depends(get_session)):
    return await taellinger.grid(db, f, km)


@router.get("/grid/{code}")
async def grid_cell(code: str, f: taellinger.Filter = Depends(_filter), user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    if not re.fullmatch(CELL, code):
        raise HTTPException(400, "Ugyldig gridcelle")
    return await taellinger.grid_cell_detail(db, f, code)


# ---------------------------------------------------------------- FiNN-områder og -punkter


class SiteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    ring: list[list[float]] = Field(min_length=3, max_length=5000)
    loknr: int | None = None
    note: str | None = Field(None, max_length=2000)


class SitePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    ring: list[list[float]] | None = Field(None, min_length=3, max_length=5000)
    loknr: int | None = None
    aktiv: bool | None = None
    note: str | None = Field(None, max_length=2000)


class PointIn(BaseModel):
    lat: float
    lon: float
    type: Literal["reference", "management"] = "reference"
    source: Literal["researcher", "volunteer"] | None = "volunteer"
    note: str | None = Field(None, max_length=2000)


class PointPatch(BaseModel):
    lat: float | None = None
    lon: float | None = None
    type: Literal["reference", "management"] | None = None
    source: Literal["researcher", "volunteer"] | None = None
    aktiv: bool | None = None
    note: str | None = Field(None, max_length=2000)


class ApplyIn(BaseModel):
    groups: list[Literal["nye", "aendrede", "konflikter", "fjernede"]] = Field(min_length=1, max_length=4)
    conflicts: Literal["portal", "dofbasen"] = "portal"


@router.get("/finn")
async def finn_sites(user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    return await finn_admin.sites_out(db)


@router.post("/finn/sites")
async def create_site(body: SiteIn, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        return finn_admin.site_out(await finn_admin.create_site(db, user.id, body.title, body.ring, body.loknr, body.note))
    except FinnError as exc:
        raise _err(exc) from exc


@router.patch("/finn/sites/{sid}")
async def update_site(sid: int, body: SitePatch, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        s = await finn_admin.get_site(db, sid)
        return finn_admin.site_out(await finn_admin.update_site(db, s, user.id, body.model_dump(exclude_unset=True)))
    except FinnError as exc:
        raise _err(exc) from exc


@router.post("/finn/sites/{sid}/revert")
async def revert_site(sid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        return finn_admin.site_out(await finn_admin.revert_site(db, await finn_admin.get_site(db, sid), user.id))
    except FinnError as exc:
        raise _err(exc) from exc


@router.delete("/finn/sites/{sid}")
async def delete_site(sid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        await finn_admin.delete_site(db, await finn_admin.get_site(db, sid))
    except FinnError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.post("/finn/points")
async def create_point(body: PointIn, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        return finn_admin.point_out(await finn_admin.create_point(db, user.id, body.lat, body.lon, body.source, body.type, body.note))
    except FinnError as exc:
        raise _err(exc) from exc


@router.patch("/finn/points/{pid}")
async def update_point(pid: int, body: PointPatch, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        p = await finn_admin.get_point(db, pid)
        return finn_admin.point_out(await finn_admin.update_point(db, p, user.id, body.model_dump(exclude_unset=True)))
    except FinnError as exc:
        raise _err(exc) from exc


@router.post("/finn/points/{pid}/revert")
async def revert_point(pid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        return finn_admin.point_out(await finn_admin.revert_point(db, await finn_admin.get_point(db, pid), user.id))
    except FinnError as exc:
        raise _err(exc) from exc


@router.delete("/finn/points/{pid}")
async def delete_point(pid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        await finn_admin.delete_point(db, await finn_admin.get_point(db, pid))
    except FinnError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.get("/finn/syncs")
async def syncs(user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    return [finn_admin.sync_out(s) for s in await finn_admin.syncs(db)]


@router.post("/finn/syncs")
async def start_sync(user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    """Hent fra DOFbasen og lav ændringsrapporten (intet ændres endnu)."""
    try:
        return finn_admin.sync_out(await finn_admin.start_sync(db, user.id))
    except FinnError as exc:
        raise HTTPException(502, str(exc)) from exc


async def _sync(db: AsyncSession, sid: int) -> FinnSync:
    s = await db.get(FinnSync, sid)
    if s is None:
        raise HTTPException(404, "Hentningen findes ikke")
    return s


@router.post("/finn/syncs/{sid}/apply")
async def apply_sync(sid: int, body: ApplyIn, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    try:
        return {"anvendt": await finn_admin.apply_sync(db, await _sync(db, sid), body.groups, body.conflicts, user.id)}
    except FinnError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/finn/syncs/{sid}")
async def discard_sync(sid: int, user: User = Depends(perm), db: AsyncSession = Depends(get_session)):
    await finn_admin.discard_sync(db, await _sync(db, sid))
    return {"ok": True}
