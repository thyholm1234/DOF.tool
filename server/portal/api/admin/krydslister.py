"""Krydslister → Admin: listetyper, artslister (CSV), World Bird List (AviList-import med diff), godkendelse af kryds og indstillinger. SQL ligger i services (kl_admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import worker_command
from portal.db import get_session
from portal.models import KlImport, KlSpeciesList, User
from portal.services import activity, kl_access, kl_admin, krydslister
from portal.services.setting_store import get_setting_dict, put_setting

router = APIRouter(prefix="/api/admin/krydslister", tags=["admin"])


def need():
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> kl_access.Access:
        acc = await kl_access.context(db, user)
        if not acc.on("krydslister"):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if not acc.admin:
            raise HTTPException(403, "Du har ikke adgang til dette")
        if await krydslister.seed(db):
            await db.commit()
        return acc

    return dep


def _err(exc: krydslister.KlError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# ---------------------------------------------------------------- listetyper

class DefIn(BaseModel):
    key: str = Field(max_length=24)
    navn: str = Field(max_length=160)
    ref_list_id: int
    auto: bool = False
    manuel: bool = False
    param: str | None = None
    regler: dict = Field(default_factory=dict)
    sort: int = 0
    aktiv: bool = True
    beskrivelse_md: str | None = None


@router.get("/listetyper")
async def listetyper(acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await kl_admin.list_defs(db)


@router.post("/listetyper")
async def opret_listetype(body: DefIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        d = await kl_admin.save_def(db, body.model_dump())
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_listetype", "/krydslister/admin", {"key": d.key})
    return kl_admin._def_out(d)


@router.put("/listetyper/{did}")
async def ret_listetype(did: int, body: DefIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        d = await kl_admin.save_def(db, body.model_dump(), did)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_listetype", "/krydslister/admin", {"key": d.key})
    return kl_admin._def_out(d)


@router.delete("/listetyper/{did}")
async def slet_listetype(did: int, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await kl_admin.delete_def(db, did)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_listetype_slet", "/krydslister/admin", {"id": did})
    return {"ok": True}


@router.post("/beregn")
async def beregn(acc: kl_access.Access = Depends(need())):
    await worker_command("kryds_recompute")
    return {"ok": True}


# ---------------------------------------------------------------- artslister

class ListIn(BaseModel):
    key: str = Field(max_length=24)
    navn: str = Field(max_length=120)
    beskrivelse: str | None = None
    sort: int = 0


class EntryIn(BaseModel):
    kategorier: list[str] = Field(default_factory=list, max_length=6)
    sjaeldenhed: str = "almindelig"
    sort: int | None = None


@router.post("/artslister")
async def opret_artsliste(body: ListIn, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        l = await kl_admin.save_species_list(db, body.model_dump())
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"id": l.id}


@router.put("/artslister/{lid}")
async def ret_artsliste(lid: int, body: ListIn, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await kl_admin.save_species_list(db, body.model_dump(), lid)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.put("/artslister/{lid}/arter/{tid}")
async def gem_art(lid: int, tid: int, body: EntryIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await kl_admin.save_entry(db, lid, tid, body.model_dump())
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_art", "/krydslister/admin", {"liste": lid, "taxon": tid})
    return {"ok": True}


@router.delete("/artslister/{lid}/arter/{tid}")
async def fjern_art(lid: int, tid: int, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    await kl_admin.remove_entry(db, lid, tid)
    await activity.track(user.id, "kl_art_fjern", "/krydslister/admin", {"liste": lid, "taxon": tid})
    return {"ok": True}


@router.get("/artslister/{lid}/csv")
async def eksport(lid: int, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    l = await db.get(KlSpeciesList, lid)
    if l is None:
        raise HTTPException(404)
    return Response(await kl_admin.export_csv(db, lid), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{l.key}.csv"'})


@router.post("/artslister/{lid}/csv")
async def import_csv(lid: int, file: UploadFile = File(...), anvend: bool = Form(False), user: User = Depends(require_user), acc: kl_access.Access = Depends(need()),
                     db: AsyncSession = Depends(get_session)):
    """Importér en rettet CSV: uden «anvend» vises kun forskellene (diff); med «anvend» skrives nye og ændrede rækker (der slettes aldrig noget)."""
    if await db.get(KlSpeciesList, lid) is None:
        raise HTTPException(404)
    try:
        out = await kl_admin.import_diff(db, lid, await file.read(5 * 1024 * 1024), apply=anvend)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    if anvend:
        await activity.track(user.id, "kl_csv_import", "/krydslister/admin", {"liste": lid, "nye": len(out["nye"]), "aendrede": len(out["aendrede"])})
    return out


# ---------------------------------------------------------------- World Bird List

class TaxonIn(BaseModel):
    dansk: str | None = Field(None, max_length=200)
    artnr: int | None = None


@router.post("/wbl/import")
async def wbl_import(file: UploadFile = File(...), version: str = Form("", max_length=40), user: User = Depends(require_user), acc: kl_access.Access = Depends(need()),
                     db: AsyncSession = Depends(get_session)):
    """Læs AviList-CSV'en og vis diffen (nye, omdøbte, ændrede, forsvundne). Intet skrives, før den anvendes."""
    try:
        imp = await kl_admin.avilist_diff(db, user, await file.read(80 * 1024 * 1024), file.filename or "avilist.csv", version.strip())
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    return {"id": imp.id, "version": imp.version, "diff": imp.diff}


@router.get("/wbl/import/{iid}")
async def wbl_import_diff(iid: int, acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    imp = await db.get(KlImport, iid)
    if imp is None:
        raise HTTPException(404)
    return {"id": imp.id, "version": imp.version, "status": imp.status, "diff": imp.diff, "filename": imp.filename}


@router.post("/wbl/import/{iid}/anvend")
async def wbl_anvend(iid: int, deaktiver_forsvundne: bool = Query(False), user: User = Depends(require_user), acc: kl_access.Access = Depends(need()),
                     db: AsyncSession = Depends(get_session)):
    try:
        out = await kl_admin.avilist_apply(db, iid, deactivate_gone=deaktiver_forsvundne)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_wbl_import", "/krydslister/admin", {"import": iid, **{k: v for k, v in out.items() if k != "uden_artnr"}})
    return out


@router.get("/wbl/uden-artnr")
async def uden_artnr(acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await kl_admin.taxa_without_artnr(db)


@router.put("/wbl/{tid}")
async def ret_taxon(tid: int, body: TaxonIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await kl_admin.edit_taxon(db, tid, body.model_dump(exclude_unset=True))
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_taxon", "/krydslister/admin", {"taxon": tid})
    return {"ok": True}


@router.post("/wbl/netfugl-ids")
async def netfugl_ids(acc: kl_access.Access = Depends(need())):
    await worker_command("photos_link_taxa")
    return {"ok": True}


# ---------------------------------------------------------------- godkendelse af kryds

class DecideIn(BaseModel):
    reason: str = Field("", max_length=500)


@router.get("/godkendelse")
async def godkendelse(status: str = Query("afventer", pattern="^(afventer|afvist)$"), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await kl_admin.approval_queue(db, status)


@router.post("/godkendelse/{kid}/{action}")
async def afgoer(kid: int, action: str, body: DecideIn, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    if action not in ("godkend", "afvis"):
        raise HTTPException(404)
    try:
        await kl_admin.decide(db, user, kid, action == "godkend", body.reason)
    except krydslister.KlError as exc:
        raise _err(exc) from exc
    await activity.track(user.id, "kl_kryds_" + action, "/krydslister/admin", {"kryds": kid})
    return {"ok": True}


# ---------------------------------------------------------------- indstillinger og tekster

@router.get("/indstillinger")
async def indstillinger(acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return {**await krydslister.settings(db), "tekster": await get_setting_dict(db, krydslister.TEXTS_KEY, {"om": "", "regler": ""}), "auto_at": await krydslister.auto_at()}


@router.put("/indstillinger")
async def gem_indstillinger(body: dict, user: User = Depends(require_user), acc: kl_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    cur = await krydslister.settings(db)
    if body.get("participants") in ("brugere", "aktive"):
        cur["participants"] = body["participants"]
    for k, lo, hi in (("aktive_max", 10, 2000), ("year_lists", 1, 15)):
        if k in body:
            cur[k] = max(lo, min(hi, int(body[k])))
    await put_setting(db, krydslister.SETTINGS_KEY, {k: cur[k] for k in krydslister.DEFAULT_SETTINGS})
    if isinstance(body.get("tekster"), dict):
        t = await get_setting_dict(db, krydslister.TEXTS_KEY, {"om": "", "regler": ""})
        for k in ("om", "regler"):
            if k in body["tekster"]:
                t[k] = str(body["tekster"][k])[:50000]
        await put_setting(db, krydslister.TEXTS_KEY, t)
    await activity.track(user.id, "kl_indstillinger", "/krydslister/admin")
    await worker_command("kryds_recompute")
    return await indstillinger(acc, db)
