"""Søg i observationerne (#/soeg): samme søgning som DOFbasen-modulets, med sortering på alle kolonner, kort over alle
fund og download som CSV, ODS eller XLSX. Slås til/fra under Admin → Funktioner (search)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import dofbasen as svc
from portal.services import features, sheets
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/search", tags=["search"])


async def need_search(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "search", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


def _timeout(exc: Exception) -> None:
    if "statement timeout" in str(exc) or "canceling statement" in str(exc):
        raise HTTPException(422, "Søgningen tog for lang tid. Indsnævr periode, område eller art.") from exc


async def _rows(db: AsyncSession, sql: str) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql))).mappings()]


@router.get("/valg")
async def valg(user: User = Depends(need_search), db: AsyncSession = Depends(get_session)):
    """Valgmuligheder til søgeformularen."""
    return {
        "species": svc.SPECIES_GROUPS, "adf": {k: v[0] for k, v in svc.ADF.items()}, "alder": svc.ALDER, "dragt": svc.DRAGT,
        "metode": svc.METODER,
        "kommuner": await _rows(db, "SELECT kode, navn FROM kommuner ORDER BY navn"),
        "afdelinger": await _rows(db, "SELECT id, navn FROM afdelinger ORDER BY navn"),
        "omraader": await _rows(db, "SELECT id, kind, navn FROM dofb_areas ORDER BY kind, navn"),
    }


@router.post("")
async def search(body: svc.SearchIn, user: User = Depends(need_search), db: AsyncSession = Depends(get_session)):
    try:
        return await svc.search(db, body, user.obserkode)
    except Exception as exc:  # noqa: BLE001 – typisk statement_timeout
        _timeout(exc)
        raise


@router.post("/points")
async def points(body: svc.SearchIn, user: User = Depends(need_search), db: AsyncSession = Depends(get_session)):
    try:
        return await svc.search_points(db, body, user.obserkode)
    except Exception as exc:  # noqa: BLE001
        _timeout(exc)
        raise


@router.post("/export/{fmt}")
async def export(fmt: str, body: svc.SearchIn, user: User = Depends(need_search), db: AsyncSession = Depends(get_session)):
    """Hele resultatet (højst 50.000 rækker) i den valgte sortering, med DOFbasens kolonnenavne."""
    if fmt not in sheets.FORMATS:
        raise HTTPException(404, "Ukendt format")
    try:
        res = await svc.search(db, body, user.obserkode, csv=True)
    except Exception as exc:  # noqa: BLE001
        _timeout(exc)
        raise
    data = sheets.write(fmt, svc.CSV_COLUMNS, (svc.csv_line(r) for r in res["rows"]), "Observationer")
    return Response(data, media_type=sheets.FORMATS[fmt],
                    headers={"Content-Disposition": f'attachment; filename="observationer-{dk_today().isoformat()}.{fmt}"'})
