"""Admin → Lokaliteter: upload af lokalitets-zip'en fra tools/dofbasen_lokaliteter, ændringsrapport og anvend
(services/location_import.py). Kræver admin-modulet lokaliteter og funktionen lokalitetsadmin."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm
from portal.cache import worker_command
from portal.db import get_session
from portal.models import LocationImport, User
from portal.services import features, location_import

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
