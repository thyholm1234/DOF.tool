"""Artikler → Admin: redigering, revisioner, emneord, arter, import af zip, indstillinger og kommentarmoderation.

Redigering kræver modulet «artikler» og funktionen «artikler_editor»; kommentarmoderation kræver modulet «moderation»."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import worker_command
from portal.db import get_session
from portal.models import ArtArticle, ArtImport, User
from portal.services import (
    activity,
    article_access,
    article_comments,
    article_edit,
    article_import,
    article_notify,
    article_settings,
    article_upload,
)
from portal.timeutil import TZ

router = APIRouter(prefix="/api/admin/artikler", tags=["admin"])


def need(kind: str = "admin"):
    """kind: admin (modulet artikler) · edit (+ editor-funktionen) · moderate (modulet moderation)."""
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> article_access.Access:
        acc = await article_access.context(db, user)
        if not acc.on("artikler" if kind != "moderate" else "artikler_kommentarer"):
            raise HTTPException(404, "Funktionen er ikke slået til")
        ok = {"admin": acc.admin, "edit": acc.can_edit, "moderate": acc.can_moderate}[kind]
        if not ok:
            raise HTTPException(403, "Du har ikke adgang til dette")
        return acc

    return dep


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=TZ) if dt is not None and dt.tzinfo is None else dt


# ---------------------------------------------------------------- artikler

class AuthorIn(BaseModel):
    navn: str = Field(max_length=160)
    user_id: int | None = None


class ArticleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    lead: str | None = Field(None, max_length=2000)
    body_md: str = Field("", max_length=400_000)
    authors: list[AuthorIn] = Field(default_factory=list, max_length=10)
    dato: datetime | None = None
    tags: list[str] = Field(default_factory=list, max_length=40)
    cover_media_id: int | None = None
    cover_caption: str | None = Field(None, max_length=300)
    status: Literal["kladde", "planlagt", "publiceret", "skjult"] = "kladde"
    publish_at: datetime | None = None
    featured_until: datetime | None = None
    comments_open: bool = True
    source_url: str | None = Field(None, max_length=400)
    kind: Literal["artikel", "pdf"] = "artikel"
    version: int | None = None

    def data(self) -> dict:
        d = self.model_dump()
        for k in ("dato", "publish_at", "featured_until"):
            d[k] = aware(d[k])
        return d


async def _save(db: AsyncSession, user: User, body: ArticleIn, art: ArtArticle | None, tasks: BackgroundTasks) -> dict:
    try:
        art, first = await article_edit.save(db, user, body.data(), art)
    except article_edit.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except article_edit.Invalid as exc:
        raise HTTPException(400, str(exc)) from exc
    if first:
        tasks.add_task(article_notify.on_publish, art.id)
    await activity.track(user.id, "artikel", f"/artikler/{art.id}", {"handling": "gem", "version": art.version})
    return {"id": art.id, "version": art.version}


@router.post("")
async def opret(body: ArticleIn, tasks: BackgroundTasks, user: User = Depends(require_user), acc: article_access.Access = Depends(need("edit")),
                db: AsyncSession = Depends(get_session)):
    return await _save(db, user, body, None, tasks)


@router.put("/{aid:int}")
async def ret(aid: int, body: ArticleIn, tasks: BackgroundTasks, user: User = Depends(require_user), acc: article_access.Access = Depends(need("edit")),
              db: AsyncSession = Depends(get_session)):
    art = await db.get(ArtArticle, aid)
    if art is None:
        raise HTTPException(404, "Artiklen findes ikke")
    return await _save(db, user, body, art, tasks)


@router.delete("/{aid:int}")
async def slet(aid: int, user: User = Depends(require_user), acc: article_access.Access = Depends(need("edit")), db: AsyncSession = Depends(get_session)):
    art = await db.get(ArtArticle, aid)
    if art is None:
        raise HTTPException(404, "Artiklen findes ikke")
    await article_edit.delete_article(db, art)
    await activity.track(user.id, "artikel", f"/artikler/{aid}", {"handling": "slet"})
    return {"ok": True}


@router.get("/{aid:int}/revisioner")
async def revisioner(aid: int, acc: article_access.Access = Depends(need("edit")), db: AsyncSession = Depends(get_session)):
    return await article_edit.revisions(db, aid)


@router.get("/{aid:int}/revisioner/{rid}")
async def revision(aid: int, rid: int, acc: article_access.Access = Depends(need("edit")), db: AsyncSession = Depends(get_session)):
    r = await article_edit.revision(db, aid, rid)
    if r is None:
        raise HTTPException(404, "Revisionen findes ikke")
    return r


@router.post("/{aid:int}/revisioner/{rid}/gendan")
async def gendan(aid: int, rid: int, tasks: BackgroundTasks, user: User = Depends(require_user), acc: article_access.Access = Depends(need("edit")),
                 db: AsyncSession = Depends(get_session)):
    """Gendan en tidligere version. Det laver en ny revision, så intet går tabt."""
    art = await db.get(ArtArticle, aid)
    r = await article_edit.revision(db, aid, rid)
    if art is None or r is None:
        raise HTTPException(404, "Artiklen eller revisionen findes ikke")
    m = r["meta"] or {}
    data = ArticleIn(title=r["title"], lead=m.get("lead"), body_md=r["body_md"], authors=m.get("authors") or [], tags=m.get("tags") or [],
                     cover_media_id=m.get("cover_media_id"), cover_caption=m.get("cover_caption"), status=art.status, publish_at=art.publish_at,
                     featured_until=art.featured_until, comments_open=bool(m.get("comments_open", True)), source_url=m.get("source_url"),
                     kind=m.get("kind") or "artikel", dato=art.dato, version=art.version)
    return await _save(db, user, data, art, tasks)


class SpeciesIn(BaseModel):
    add: list[int] = Field(default_factory=list, max_length=50)
    remove: list[int] = Field(default_factory=list, max_length=50)
    restore: list[int] = Field(default_factory=list, max_length=50)


@router.put("/{aid:int}/arter")
async def arter(aid: int, body: SpeciesIn, acc: article_access.Access = Depends(need("edit")), db: AsyncSession = Depends(get_session)):
    if await db.get(ArtArticle, aid) is None:
        raise HTTPException(404, "Artiklen findes ikke")
    await article_edit.set_species(db, aid, body.add, body.remove, body.restore)
    return {"ok": True}


# ---------------------------------------------------------------- emneord

class TagIn(BaseModel):
    navn: str | None = Field(None, max_length=120)
    kind: str | None = Field(None, max_length=12)
    artnr: int | None = None
    hidden: bool | None = None


class MergeIn(BaseModel):
    into: int


@router.get("/emneord")
async def emneord(acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    return await article_edit.tags_admin(db)


@router.put("/emneord/{tid}")
async def ret_emneord(tid: int, body: TagIn, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    try:
        await article_edit.tag_update(db, tid, navn=body.navn, kind=body.kind, artnr=body.artnr, hidden=body.hidden)
    except article_edit.Invalid as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.post("/emneord/{tid}/flet")
async def flet(tid: int, body: MergeIn, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    try:
        return {"flyttet": await article_edit.tag_merge(db, tid, body.into)}
    except article_edit.Invalid as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- import

class ImportIn(BaseModel):
    filename: str = Field(max_length=300)
    size: int = Field(gt=0)
    sha256: str | None = Field(None, pattern="^[0-9a-fA-F]{64}$")


class StartIn(BaseModel):
    overwrite_local: bool = False
    import_comments: bool = True
    publish: bool = True
    fetch_images: bool = True
    dry_run: bool = False
    keep_zip: bool = False
    force: bool = False


def _imp_out(i: ArtImport) -> dict:
    return {"id": i.id, "filename": i.filename, "size": i.size, "status": i.status, "options": i.options, "stats": i.stats, "log": i.log,
            "created_at": i.created_at.isoformat(), "started_at": i.started_at.isoformat() if i.started_at else None,
            "finished_at": i.finished_at.isoformat() if i.finished_at else None}


async def _import(db: AsyncSession, iid: int) -> ArtImport:
    imp = await db.get(ArtImport, iid)
    if imp is None:
        raise HTTPException(404, "Importen findes ikke")
    return imp


@router.post("/import")
async def import_opret(body: ImportIn, user: User = Depends(require_user), acc: article_access.Access = Depends(need("admin")),
                       db: AsyncSession = Depends(get_session)):
    try:
        imp = await article_upload.create(db, user.id, body.filename, body.size, body.sha256)
    except article_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"id": imp.id, "chunk_size": article_upload.CHUNK, "parts": article_upload.parts_for(imp.size)}


@router.put("/import/{iid}/del/{n}")
async def import_del(iid: int, n: int, request: Request, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    try:
        size = await article_upload.write_part(imp, n, request.stream())
    except article_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"del": n, "bytes": size}


@router.get("/import/{iid}/dele")
async def import_dele(iid: int, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    """Hvilke dele serveren allerede har – til at genoptage en afbrudt upload."""
    imp = await _import(db, iid)
    return {"dele": article_upload.received(imp), "i_alt": article_upload.parts_for(imp.size)}


@router.post("/import/{iid}/start")
async def import_start(iid: int, body: StartIn, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    imp = await _import(db, iid)
    try:
        await article_upload.finish(db, imp, body.model_dump())
    except article_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return _imp_out(imp)


@router.get("/import")
async def import_liste(acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    return [_imp_out(i) for i in await article_import.recent(db)]


@router.get("/import/{iid}")
async def import_status(iid: int, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    return _imp_out(await _import(db, iid))


@router.delete("/import/{iid}")
async def import_slet(iid: int, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    try:
        await article_upload.discard(db, await _import(db, iid))
    except article_upload.UploadError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"ok": True}


@router.post("/genberegn-arter")
async def genberegn(acc: article_access.Access = Depends(need("admin"))):
    await worker_command("articles_species")
    return {"ok": True}


@router.post("/render-alle")
async def render_alle(acc: article_access.Access = Depends(need("admin"))):
    await worker_command("content_rerender")
    return {"ok": True}


# ---------------------------------------------------------------- indstillinger

@router.get("/indstillinger")
async def indstillinger(acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    return await article_settings.get(db)


@router.put("/indstillinger")
async def gem_indstillinger(body: dict, acc: article_access.Access = Depends(need("admin")), db: AsyncSession = Depends(get_session)):
    try:
        return await article_settings.save(db, body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------- kommentarmoderation

class ModerateIn(BaseModel):
    reason: str = Field("", max_length=500)
    notify: bool = False


@router.get("/kommentarer")
async def kommentar_kø(tab: Literal["anmeldte", "skjulte", "seneste"] = "anmeldte", side: int = Query(1, ge=1), acc: article_access.Access = Depends(need("moderate")),
                       db: AsyncSession = Depends(get_session)):
    return await article_comments.queue(db, tab, 50, (side - 1) * 50)


@router.post("/kommentarer/{cid}/{action}")
async def moderer(cid: int, action: Literal["skjul", "gendan", "slet", "afvis"], body: ModerateIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                  acc: article_access.Access = Depends(need("moderate")), db: AsyncSession = Depends(get_session)):
    try:
        await article_comments.moderate(db, user, cid, action, body.reason)
    except article_comments.CommentError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if action == "skjul" and body.notify:
        tasks.add_task(article_notify.on_hidden, cid, body.reason)
    await activity.track(user.id, "artikel_kommentar", f"/artikler", {"handling": action, "kommentar": cid})
    return {"ok": True}

