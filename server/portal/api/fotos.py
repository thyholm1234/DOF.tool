"""Fotomodulet (brugerdelen): galleri, fotoside, kommentarer, upload og Mine fotos.

Alt kræver, at funktionen «fotos» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. Moderation og import ligger i
api/admin/fotos.py. SQL ligger i services (photo_*)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import photo_access, photo_codes, photo_comments, photos

router = APIRouter(prefix="/api/fotos", tags=["fotos"])
FILTERS = {"q", "fra", "til", "md", "art", "fotograf", "land", "lok", "loknr", "sj", "koen", "alder", "dragt", "adf", "tag"}


def need(feature: str = "fotos"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> photo_access.Access:
        acc = await photo_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


@router.get("/koder")
async def koder(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Kodelister (adfærd, køn, alder, dragt), emneord, rettigheder, sjældenhed og lande."""
    out = await photo_codes.all_codes(db)
    await db.commit()  # all_codes lægger seedet ind første gang
    return out


# ---------------------------------------------------------------- galleri og fotoside

@router.get("")
async def galleri(request: Request, side: int = Query(1, ge=1, le=100000), per: int = Query(24), sort: str = "dato", acc: photo_access.Access = Depends(need()),
                  db: AsyncSession = Depends(get_session)):
    """Galleriet: søgning og filtre (q, fra, til, md, art, fotograf, land, lok, loknr, sj, koen, alder, dragt, adf, tag). Kun publicerede fotos."""
    q = {k: v for k, v in request.query_params.items() if k in FILTERS}
    return await photos.search(db, {**q, "side": side, "per": per, "sort": sort})


@router.get("/n/{netfugl_id}")
async def fra_netfugl(netfugl_id: int, acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Gamle netfugl.dk-adresser (/fotos/{id}): slå fotoet op på dets oprindelige nummer."""
    pid = await photos.by_netfugl(db, netfugl_id)
    if pid is None:
        raise HTTPException(404, "Fotoet findes ikke i arkivet")
    return {"id": pid}


@router.get("/{pid}")
async def foto(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    p = await photos.get(db, pid, user_id=user.id, moderator=acc.can_moderate, hide_obs=not acc.on("fotos_dofbasen"))
    if p is None:
        raise HTTPException(404, "Fotoet findes ikke")
    if p["status"] == "publiceret":
        await photos.register_view(db, pid, user.id)
    p["kan"] = {"rette": acc.can_moderate or (p["egen"] and not p["laast"] and acc.on("fotos_upload")), "moderere": acc.can_moderate,
                "kommentere": acc.on("fotos_kommentarer") and p["status"] == "publiceret", "like": acc.on("fotos_kommentarer") and p["status"] == "publiceret",
                "bannere": acc.can_moderate and acc.on("fotos_bannere")}
    return p


@router.post("/{pid}/like")
async def like(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        return await photos.toggle_like(db, pid, user.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- kommentarer

class CommentIn(BaseModel):
    body: str = Field(max_length=6000)
    parent_id: int | None = None
    intern: bool = False


class EditIn(BaseModel):
    body: str = Field(max_length=6000)


class ReportIn(BaseModel):
    reason: str = Field(max_length=40)
    note: str = Field("", max_length=500)


def _cerr(exc: photo_comments.CommentError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/{pid}/kommentarer")
async def kommentarer(pid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    if await photos.get(db, pid, user_id=user.id, moderator=acc.can_moderate) is None:
        raise HTTPException(404, "Fotoet findes ikke")
    return await photo_comments.list_comments(db, pid, user.id, acc.can_moderate)


@router.post("/{pid}/kommentarer")
async def ny_kommentar(pid: int, body: CommentIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                       db: AsyncSession = Depends(get_session)):
    try:
        c = await photo_comments.add(db, user, pid, body.body, body.parent_id, moderator=acc.can_moderate, intern=body.intern)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"id": c.id}


@router.patch("/kommentarer/{cid}")
async def ret_kommentar(cid: int, body: EditIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                        db: AsyncSession = Depends(get_session)):
    try:
        await photo_comments.edit(db, user, cid, body.body)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True}


@router.delete("/kommentarer/{cid}")
async def slet_kommentar(cid: int, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        await photo_comments.remove_own(db, user, cid)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True}


@router.post("/kommentarer/{cid}/anmeld")
async def anmeld(cid: int, body: ReportIn, user: User = Depends(require_user), acc: photo_access.Access = Depends(need("fotos_kommentarer")),
                 db: AsyncSession = Depends(get_session)):
    try:
        hidden = await photo_comments.report(db, user, cid, body.reason, body.note)
    except photo_comments.CommentError as exc:
        raise _cerr(exc) from exc
    return {"ok": True, "skjult": hidden}
