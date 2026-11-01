"""Fotos → Admin: import af fotoarkivet, (senere) kø, privilegier, fotografer, bannere, koder og indstillinger.
SQL ligger i services (photo_*)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import worker_command
from portal.db import get_session
from portal.models import GalImport, User
from portal.services import activity, photo_access, photo_codes, photo_comments, photo_edit, photo_import, photo_moderation, photo_photographers, photo_privileges, photo_settings, photo_upload

router = APIRouter(prefix="/api/admin/fotos", tags=["admin"])


def need(kind: str = "admin"):
    """kind: admin (modulet fotos) · moderate (fotomoderator – samme modul)."""
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> photo_access.Access:
        acc = await photo_access.context(db, user)
        if not acc.on("fotos"):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if not (acc.admin if kind == "admin" else acc.can_moderate):
            raise HTTPException(403, "Du har ikke adgang til dette")
        return acc

    return dep


# ---------------------------------------------------------------- import

class ImportIn(BaseModel):
    filename: str = Field(max_length=300)
    size: int = Field(gt=0)
    sha256: str | None = Field(None, pattern="^[0-9a-fA-F]{64}$")


class StartIn(BaseModel):
    import_comments: bool = True
    overwrite_local: bool = False
    dry_run: bool = False
    force: bool = False
    keep_files: bool = False
    limit: int | None = Field(None, ge=1, le=1_000_000)


class FolderIn(StartIn):
    sti: str = Field(max_length=300)


def _imp_out(i: GalImport) -> dict:
    return {"id": i.id, "kilde": i.kilde, "sti": i.sti, "filename": i.filename, "size": i.size, "status": i.status, "options": i.options,
            "stats": i.stats, "log": i.log, "created_at": i.created_at.isoformat(), "started_at": i.started_at.isoformat() if i.started_at else None,
            "finished_at": i.finished_at.isoformat() if i.finished_at else None}


async def _import(db: AsyncSession, iid: int) -> GalImport:
    imp = await db.get(GalImport, iid)
    if imp is None:
        raise HTTPException(404, "Importen findes ikke")
    return imp


@router.get("/import/mapper")
async def import_mapper(acc: photo_access.Access = Depends(need())):
    return photo_import.list_folders()


@router.post("/import/mappe")
async def import_fra_mappe(body: FolderIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()),
                           db: AsyncSession = Depends(get_session)):
    """Start en import fra en mappe eller zip-fil, der allerede ligger under importmappen på serveren."""
    try:
        imp = await photo_import.create_folder_import(db, user.id, body.sti, {})
    except photo_import.ImportAbort as exc:
        raise HTTPException(400, str(exc)) from exc
    opts = body.model_dump(exclude={"sti"}, exclude_none=True)
    imp.options = {k: (v if k == "limit" else bool(v)) for k, v in opts.items() if k in photo_upload.KEEP | {"limit"}}
    imp.status = "klar"
    await db.commit()
    await worker_command("photos_import", import_id=imp.id)
    return _imp_out(imp)


@router.post("/import")
async def import_opret(body: ImportIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()),
                       db: AsyncSession = Depends(get_session)):
    try:
        imp = await photo_upload.create(db, user.id, body.filename, body.size, body.sha256)
    except photo_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"id": imp.id, "chunk_size": photo_upload.CHUNK, "parts": photo_upload.parts_for(imp.size)}


@router.put("/import/{iid}/del/{n}")
async def import_del(iid: int, n: int, request: Request, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    try:
        size = await photo_upload.write_part(imp, n, request.stream())
    except photo_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"del": n, "bytes": size}


@router.get("/import/{iid}/dele")
async def import_dele(iid: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    return {"dele": photo_upload.received(imp), "i_alt": photo_upload.parts_for(imp.size)}


@router.post("/import/{iid}/start")
async def import_start(iid: int, body: StartIn, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    try:
        await photo_upload.finish(db, imp, body.model_dump())
    except photo_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if body.limit:
        imp.options = {**imp.options, "limit": body.limit}
        await db.commit()
    return _imp_out(imp)


@router.get("/import")
async def import_liste(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return [_imp_out(i) for i in await photo_import.recent(db)]


@router.get("/import/{iid}")
async def import_status(iid: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return _imp_out(await _import(db, iid))


@router.delete("/import/{iid}")
async def import_slet(iid: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_upload.discard(db, await _import(db, iid))
    except photo_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"ok": True}


@router.post("/fotografer/match")
async def match_fotografer(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Kør det automatiske navnematch igen (rører aldrig manuelle koblinger)."""
    out = await photo_photographers.match_all(db)
    await db.commit()
    return out


# ---------------------------------------------------------------- kommentarmoderation

class ModerateIn(BaseModel):
    reason: str = Field("", max_length=500)


def need_comments():
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> photo_access.Access:
        acc = await photo_access.context(db, user)
        if not acc.on("fotos_kommentarer"):
            raise HTTPException(404, "Funktionen er ikke slået til")
        if not (acc.can_moderate_comments or acc.can_moderate):
            raise HTTPException(403, "Du har ikke adgang til dette")
        return acc

    return dep


@router.get("/kommentarer")
async def kommentar_koe(tab: Literal["anmeldte", "skjulte", "seneste"] = "anmeldte", side: int = Query(1, ge=1), acc: photo_access.Access = Depends(need_comments()),
                        db: AsyncSession = Depends(get_session)):
    try:
        return await photo_comments.queue(db, tab, 50, (side - 1) * 50)
    except photo_comments.CommentError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.post("/kommentarer/{cid}/{action}")
async def moderer(cid: int, action: Literal["skjul", "gendan", "slet", "afvis"], body: ModerateIn, user: User = Depends(require_user),
                  acc: photo_access.Access = Depends(need_comments()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_comments.moderate(db, user, cid, action, body.reason)
    except photo_comments.CommentError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"ok": True}


# ---------------------------------------------------------------- privilegier

class PrivIn(BaseModel):
    niveau: Literal["standard", "maanedlig", "ubegraenset", "udelukket"]
    note: str | None = Field(None, max_length=300)


@router.get("/privilegier")
async def privilegier(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return {"niveauer": [{"niveau": k, "tekst": t} for k, t in photo_privileges.TEXTS.items()], "brugere": await photo_privileges.listing(db)}


@router.put("/privilegier/{uid}")
async def saet_privilegie(uid: int, body: PrivIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_privileges.set_level(db, user, uid, body.niveau, body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    await activity.track(user.id, "foto_privilegie", "/fotos/admin/privilegier", {"bruger": uid, "niveau": body.niveau})
    return {"ok": True}


@router.delete("/privilegier/{uid}")
async def fjern_privilegie(uid: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    await photo_privileges.clear(db, uid)
    return {"ok": True}


# ---------------------------------------------------------------- kø, status og lås

class StatusIn(BaseModel):
    status: Literal["publiceret", "afvist", "afpubliceret", "afventer"]
    grund: str | None = Field(None, max_length=1000)


class LockIn(BaseModel):
    locked: bool


def _merr(exc: photo_moderation.ModError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/koe")
async def koe(status: Literal["afventer", "afvist", "afpubliceret"] = "afventer", side: int = Query(1, ge=1), acc: photo_access.Access = Depends(need("moderate")),
              db: AsyncSession = Depends(get_session)):
    return await photo_moderation.queue(db, status, 30, (side - 1) * 30)


@router.put("/{pid:int}/status")
async def saet_status(pid: int, body: StatusIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("moderate")), db: AsyncSession = Depends(get_session)):
    try:
        await photo_moderation.set_status(db, user, pid, body.status, body.grund)
    except photo_moderation.ModError as exc:
        raise _merr(exc) from exc
    await activity.track(user.id, "foto_status", f"/fotos/{pid}", {"foto": pid, "status": body.status})
    return {"ok": True}


@router.put("/{pid:int}/laas")
async def laas(pid: int, body: LockIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("moderate")), db: AsyncSession = Depends(get_session)):
    try:
        await photo_moderation.set_lock(db, user, pid, body.locked)
    except photo_moderation.ModError as exc:
        raise _merr(exc) from exc
    await activity.track(user.id, "foto_laas", f"/fotos/{pid}", {"foto": pid, "laast": body.locked})
    return {"ok": True}


@router.get("/{pid:int}/gruppeforslag")
async def gruppeforslag(pid: int, acc: photo_access.Access = Depends(need("moderate")), db: AsyncSession = Depends(get_session)):
    """Fotos fra samme fotograf af samme fund (samme observation eller art + dato + lokalitet), som endnu ikke er i en gruppe."""
    try:
        return {"ids": await photo_edit.group_candidates_for(db, pid)}
    except photo_edit.PhotoError as exc:
        raise HTTPException(exc.status, exc.message) from exc


# ---------------------------------------------------------------- fotografer

class LinkIn(BaseModel):
    user_id: int | None = None
    obserkode: str | None = Field(None, max_length=16)
    unlink: bool = False


class MergeIn(BaseModel):
    from_id: int
    into_id: int


@router.get("/fotografer")
async def fotografer(match: str = Query("", pattern="^(|auto|manuel|ingen|tvetydig)$"), q: str = Query("", max_length=100), side: int = Query(1, ge=1),
                     acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await photo_photographers.listing(db, match=match, q=q, limit=50, offset=(side - 1) * 50)


@router.patch("/fotografer/{pid}")
async def ret_fotograf(pid: int, body: LinkIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_photographers.link(db, pid, user_id=body.user_id, obserkode=body.obserkode, unlink=body.unlink)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "foto_fotograf", "/fotos/admin/fotografer", {"fotograf": pid, **body.model_dump(exclude_none=True)})
    return {"ok": True}


@router.post("/fotografer/flet")
async def flet_fotografer(body: MergeIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_photographers.merge(db, body.from_id, body.into_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "foto_flet", "/fotos/admin/fotografer", body.model_dump())
    return {"ok": True}


# ---------------------------------------------------------------- koder, indstillinger og tekster

class CodeIn(BaseModel):
    kind: Literal["adfaerd", "koen", "alder", "dragt"]
    code: str = Field("", max_length=16)
    text: str = Field(max_length=120)
    sort: int = 100
    dofbasen_code: str | None = Field(None, max_length=16)


class CodesIn(BaseModel):
    items: list[CodeIn] = Field(max_length=500)


class CodeMergeIn(BaseModel):
    kind: Literal["adfaerd", "koen", "alder", "dragt"]
    from_code: str
    into_code: str


@router.get("/koder")
async def koder(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    out = await photo_codes.admin_list(db)
    await db.commit()
    return out


@router.put("/koder")
async def gem_koder(body: CodesIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await photo_codes.admin_save(db, [i.model_dump() for i in body.items])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "foto_koder", "/fotos/admin/koder", {"antal": len(body.items)})
    return {"ok": True}


@router.post("/koder/flet")
async def flet_koder(body: CodeMergeIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        n = await photo_codes.admin_merge(db, body.kind, body.from_code, body.into_code)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "foto_koder", "/fotos/admin/koder", body.model_dump())
    return {"ok": True, "fugle": n}


@router.get("/indstillinger")
async def indstillinger(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return {**await photo_settings.get(db), "tekster": await photo_settings.get_texts(db)}


@router.put("/indstillinger")
async def gem_indstillinger(body: dict, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        if "tekster" in body:
            await photo_settings.save_texts(db, body["tekster"] or {})
        cfg = await photo_settings.save(db, {k: v for k, v in body.items() if k != "tekster"})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "foto_indstillinger", "/fotos/admin/indstillinger")
    return {**cfg, "tekster": await photo_settings.get_texts(db)}


@router.get("/brugere")
async def find_brugere(q: str = Query("", max_length=60), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await photo_privileges.find_users(db, q)
