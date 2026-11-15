"""Snak (brugerdelen): feed, opslag, kommentarer og reaktioner (docs/snak-modul-spec.md §10).

Alt kræver, at funktionen «snak» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. SQL ligger i services (snak*)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import snak, snak_access, snak_comments, snak_reactions

router = APIRouter(prefix="/api/snak", tags=["snak"])
ReactionType = Field(pattern="^(like|love|haha|wow|sad|angry)$")


def need(feature: str = "snak"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> snak_access.Access:
        acc = await snak_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


def _wrap(exc: Exception) -> HTTPException:
    return HTTPException(exc.status, exc.message)


class PostIn(BaseModel):
    category_id: int
    titel: str | None = Field(None, max_length=200)
    body: str = Field("", max_length=snak.MAX_BODY + 1000)


class CommentIn(BaseModel):
    body: str = Field(max_length=snak_comments.MAX_LEN + 1000)
    parent_id: int | None = None


class ReactionIn(BaseModel):
    type: str = ReactionType


class ModerateIn(BaseModel):
    action: str = Field(pattern="^(skjul|vis|laas|aabn|flyt)$")
    reason: str = Field("", max_length=500)
    category_id: int | None = None


class CommentModerateIn(BaseModel):
    action: str = Field(pattern="^(skjul|vis)$")
    reason: str = Field("", max_length=500)


class PinIn(BaseModel):
    pinned: bool


@router.get("/kategorier")
async def kategorier(acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    cfg = await snak.settings(db)
    return {"categories": await snak.categories(db), "feed": {"kommentarer": cfg["feed_kommentarer"], "svar": cfg["feed_svar"]}, "reactions": {k: {"emoji": e, "navn": n} for k, (e, n) in snak_reactions.TYPES.items()}}


@router.get("/feed")
async def feed(sort: str = "aktivitet", kategori: str | None = Query(None, max_length=40), uloeste: bool = False, cursor: str | None = Query(None, max_length=200),
               user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await snak.feed(db, user, staff=acc.can_moderate, sort=sort, category=kategori, uloeste=uloeste, cursor=cursor)
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.post("")
async def opret(body: PostIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return {"id": await snak.create(db, user, staff=acc.staff, category_id=body.category_id, titel=body.titel, body=body.body)}
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.get("/{post_id}")
async def opslag(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await snak.get_post(db, post_id, user, staff=acc.can_moderate)
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.put("/{post_id}")
async def ret(post_id: int, body: PostIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.edit(db, user, post_id, titel=body.titel, body=body.body)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


@router.delete("/{post_id}")
async def slet(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.remove_own(db, user, post_id, staff=acc.can_moderate)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


@router.post("/{post_id}/foelg")
async def foelg(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.set_follow(db, user.id, post_id, True)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"following": True}


@router.delete("/{post_id}/foelg")
async def foelg_ikke(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.set_follow(db, user.id, post_id, False)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"following": False}


@router.post("/{post_id}/visning")
async def visning(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    await snak.register_view(db, user.id, post_id)
    return {"ok": True}


@router.post("/{post_id}/moderer")
async def moderer(post_id: int, body: ModerateIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    if not acc.can_moderate:
        raise HTTPException(403, "Kun moderatorer kan det")
    try:
        await snak.moderate(db, user, post_id, body.action, reason=body.reason, category_id=body.category_id)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


@router.post("/{post_id}/fastgoer")
async def fastgoer(post_id: int, body: PinIn, acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    if not acc.admin:
        raise HTTPException(403, "Kun administratorer kan fastgøre opslag")
    try:
        await snak.pin(db, post_id, body.pinned)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


# ---------------------------------------------------------------- reaktioner

@router.put("/{post_id}/reaktion")
async def reager_opslag(post_id: int, body: ReactionIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _react(db, user, "post", post_id, body.type)


@router.delete("/{post_id}/reaktion")
async def fjern_opslag(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _react(db, user, "post", post_id, None)


@router.get("/{post_id}/reaktioner")
async def hvem_opslag(post_id: int, type: str | None = Query(None, max_length=5), offset: int = Query(0, ge=0, le=100000),
                      acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _who(db, "post", post_id, type, offset)


@router.put("/kommentarer/{cid}/reaktion")
async def reager_kommentar(cid: int, body: ReactionIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _react(db, user, "comment", cid, body.type)


@router.delete("/kommentarer/{cid}/reaktion")
async def fjern_kommentar(cid: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _react(db, user, "comment", cid, None)


@router.get("/kommentarer/{cid}/reaktioner")
async def hvem_kommentar(cid: int, type: str | None = Query(None, max_length=5), offset: int = Query(0, ge=0, le=100000),
                         acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await _who(db, "comment", cid, type, offset)


async def _react(db, user, kind, target_id, rtype):
    try:
        return await snak_reactions.set_reaction(db, user, kind, target_id, rtype)
    except snak_reactions.ReactionError as e:
        raise _wrap(e) from e


async def _who(db, kind, target_id, rtype, offset):
    try:
        return await snak_reactions.who(db, kind, target_id, rtype, offset)
    except snak_reactions.ReactionError as e:
        raise _wrap(e) from e


# ---------------------------------------------------------------- kommentarer

@router.get("/{post_id}/kommentarer")
async def kommentarer(post_id: int, sort: str = "nyeste", skip: int = Query(0, ge=0, le=100000), around: int | None = None,
                      user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.get_post(db, post_id, user, staff=acc.can_moderate)  # 404 for skjulte/slettede opslag
        return await snak_comments.thread(db, post_id, user.id, acc.can_moderate, sort=sort, skip=skip, around=around)
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.post("/{post_id}/kommentarer")
async def kommenter(post_id: int, body: CommentIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        c = await snak_comments.add(db, user, post_id, body.body, body.parent_id, staff=acc.staff)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"id": c.id}


@router.patch("/kommentarer/{cid}")
async def ret_kommentar(cid: int, body: CommentIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak_comments.edit(db, user, cid, body.body)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


@router.delete("/kommentarer/{cid}")
async def slet_kommentar(cid: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak_comments.remove_own(db, user, cid)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


@router.post("/kommentarer/{cid}/moderer")
async def moderer_kommentar(cid: int, body: CommentModerateIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()),
                            db: AsyncSession = Depends(get_session)):
    if not acc.can_moderate:
        raise HTTPException(403, "Kun moderatorer kan det")
    try:
        await snak_comments.moderate(db, user, cid, body.action, body.reason)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}
