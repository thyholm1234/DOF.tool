"""Admin → Lokaliteter: kortet med grænserne og deres redigering (services/location_edit.py) og import af lokalitets-zip'en
fra tools/dofbasen_lokaliteter (services/location_import.py). Kræver admin-modulet lokaliteter og funktionen lokalitetsadmin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm
from portal.cache import worker_command
from portal.db import get_session
from portal.models import LocationImport, User
from portal.dbops import sync_reference
from portal.services import features, location_edit, location_import

router = APIRouter(prefix="/api/admin/locations", tags=["admin"])
_perm = require_perm("lokaliteter")


async def perm_lokaliteter(user: User = Depends(_perm), db: AsyncSession = Depends(get_session)) -> User:
    if not features.visible(await features.load(db), "lokalitetsadmin", superadmin=user.role == "superadmin", insider=False):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


async def _import(db: AsyncSession, iid: int) -> LocationImport:
    imp = await db.get(LocationImport, iid)
    if imp is None:
        raise HTTPException(404, "Importen findes ikke")
    return imp


def _err(exc: location_import.ImportError_) -> HTTPException:
    msg = str(exc)
    return HTTPException(409 if msg.startswith("Serveren har") else 400, msg)


class ImportIn(BaseModel):
    filename: str = Field(min_length=5, max_length=300)
    size: int = Field(gt=0)


@router.get("/imports")
async def imports(user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    return [location_import.out(i) for i in await location_import.list_imports(db)]


@router.post("/imports")
async def create_import(body: ImportIn, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        imp = await location_import.create(db, user.id, body.filename, body.size)
    except location_import.ImportError_ as exc:
        raise _err(exc) from exc
    return location_import.out(imp)


@router.post("/imports/{iid}/upload")
async def upload_chunk(iid: int, request: Request, offset: int = Query(ge=0), user: User = Depends(perm_lokaliteter),
                       db: AsyncSession = Depends(get_session)):
    """Én bid af zip'en. Bidderne sendes i rækkefølge; ved et afbrud svarer serveren 409 med, hvor langt den er."""
    imp = await _import(db, iid)
    try:
        return {"size": await location_import.write_chunk(imp, offset, request.stream())}
    except location_import.ImportError_ as exc:
        raise _err(exc) from exc


@router.post("/imports/{iid}/finish")
async def finish(iid: int, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    """Hele zip'en er sendt: hurtig kontrol, og workeren validerer og sammenligner."""
    imp = await _import(db, iid)
    try:
        await location_import.finish(db, imp)
    except location_import.ImportError_ as exc:
        imp.status, imp.error = "fejl", str(exc)
        await db.commit()
        raise _err(exc) from exc
    await worker_command("location_import", import_id=imp.id)
    return location_import.out(imp)


@router.get("/imports/{iid}")
async def get_import(iid: int, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    return location_import.out(await _import(db, iid))


class ApplyIn(BaseModel):
    groups: list[str] = Field(min_length=1, max_length=len(location_import.GROUPS))
    conflicts: str = Field("behold", pattern="^(behold|dofbasen)$")


@router.post("/imports/{iid}/apply")
async def apply(iid: int, body: ApplyIn, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    if imp.status != "klar":
        raise HTTPException(400, "Importen er ikke klar til at blive anvendt")
    unknown = set(body.groups) - set(location_import.GROUPS)
    if unknown:
        raise HTTPException(400, f"Ukendte grupper: {', '.join(sorted(unknown))}")
    imp.status, imp.applied_by = "anvender", user.id
    await db.commit()
    await worker_command("location_apply", import_id=imp.id, groups=body.groups, conflicts=body.conflicts)
    return location_import.out(imp)


@router.delete("/imports/{iid}")
async def delete_import(iid: int, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    if imp.status in ("validerer", "anvender"):
        raise HTTPException(400, "Workeren arbejder på importen – vent til den er færdig")
    if imp.status == "anvendt":
        raise HTTPException(400, "En anvendt import kan ikke slettes (den er en del af grænsernes historik)")
    await location_import.discard(db, imp)
    return {"ok": True}


# ---------------------------------------------------------------- kort og redigering (trin 3)


def _edit_err(exc: location_edit.EditError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


async def _sync_locations() -> None:
    """locations er referencedata: kopiér til brugergruppen med det samme (ellers venter det til næste time)."""
    await sync_reference(("locations",))


@router.get("/map")
async def map_features(bbox: str = Query(pattern=r"^-?[\d.]+,-?[\d.]+,-?[\d.]+,-?[\d.]+$"), zoom: int = Query(ge=0, le=22),
                       user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    w, s, e, n = (float(x) for x in bbox.split(","))
    if not (w < e and s < n and e - w <= 20 and n - s <= 20):
        raise HTTPException(400, "Ugyldigt kortudsnit")
    return await location_edit.map_features(db, (w, s, e, n), zoom)


@router.get("/{loknr:int}")
async def location(loknr: int, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    d = await location_edit.detail(db, loknr)
    if d is None:
        raise HTTPException(404, "Lokaliteten findes ikke")
    return {**d, "advarsler": await location_edit.warnings(db, loknr) if d["graense"] else []}


@router.get("/{loknr:int}/shapes/{sid:int}")
async def shape(loknr: int, sid: int, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    g = await location_edit.shape_geojson(db, loknr, sid)
    if g is None:
        raise HTTPException(404, "Versionen findes ikke")
    return g


class ShapeIn(BaseModel):
    geojson: dict[str, Any]
    base_id: int | None = None
    note: str | None = Field(None, max_length=500)


@router.put("/{loknr:int}/shape")
async def save_shape(loknr: int, body: ShapeIn, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        return await location_edit.save_shape(db, loknr, body.geojson, body.base_id, user.id, body.note)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc


class CreateIn(BaseModel):
    navn: str = Field(min_length=2, max_length=250)
    geojson: dict[str, Any]
    parent_loknr: int | None = None
    typer_tekst: str | None = Field(None, max_length=300)
    beskrivelse: str | None = Field(None, max_length=20_000)


@router.post("")
async def create(body: CreateIn, tasks: BackgroundTasks, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        out = await location_edit.create(db, body.navn, body.geojson, user.id, body.parent_loknr, body.typer_tekst, body.beskrivelse)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc
    tasks.add_task(_sync_locations)
    return out


class SplitIn(BaseModel):
    line: dict[str, Any]
    names: list[str] = Field(default_factory=list, max_length=20)
    base_id: int | None = None


@router.post("/{loknr:int}/split")
async def split(loknr: int, body: SplitIn, tasks: BackgroundTasks, user: User = Depends(perm_lokaliteter),
                db: AsyncSession = Depends(get_session)):
    try:
        out = await location_edit.split(db, loknr, body.line, body.names, body.base_id, user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc
    tasks.add_task(_sync_locations)
    return out


class MergeIn(BaseModel):
    other: int
    base_id: int | None = None


@router.post("/{loknr:int}/merge")
async def merge(loknr: int, body: MergeIn, tasks: BackgroundTasks, user: User = Depends(perm_lokaliteter),
                db: AsyncSession = Depends(get_session)):
    try:
        out = await location_edit.merge(db, loknr, body.other, body.base_id, user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc
    tasks.add_task(_sync_locations)
    return out


class InfoIn(BaseModel):
    navn: str | None = Field(None, max_length=250)
    typer_tekst: str | None = Field(None, max_length=300)
    beskrivelse: str | None = Field(None, max_length=20_000)
    parent_loknr: int | None = None


@router.patch("/{loknr:int}")
async def update_info(loknr: int, body: InfoIn, tasks: BackgroundTasks, user: User = Depends(perm_lokaliteter),
                      db: AsyncSession = Depends(get_session)):
    try:
        out = await location_edit.update_info(db, loknr, body.model_dump(exclude_unset=True), user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc
    tasks.add_task(_sync_locations)
    return out


class RevertIn(BaseModel):
    shape_id: int
    base_id: int | None = None


@router.post("/{loknr:int}/revert")
async def revert(loknr: int, body: RevertIn, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        return await location_edit.revert(db, loknr, body.shape_id, body.base_id, user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc


class TrimIn(BaseModel):
    loknrs: list[int] = Field(min_length=1, max_length=50)


@router.post("/{loknr:int}/trim-neighbours")
async def trim_neighbours(loknr: int, body: TrimIn, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        return await location_edit.trim_neighbours(db, loknr, body.loknrs, user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc


@router.delete("/{loknr:int}")
async def delete(loknr: int, tasks: BackgroundTasks, user: User = Depends(perm_lokaliteter), db: AsyncSession = Depends(get_session)):
    try:
        out = await location_edit.delete(db, loknr, user.id)
    except location_edit.EditError as exc:
        raise _edit_err(exc) from exc
    tasks.add_task(_sync_locations)
    return out
