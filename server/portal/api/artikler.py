"""Artikelmodulet (brugerdelen): forside, søgning, emneord, arter, forfattere, artikelside, kommentarer og notifikationer.

Alt kræver, at funktionen «artikler» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. Redigering og import ligger i
api/admin/artikler.py. SQL ligger i services (article_*)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import article_access, article_comments, article_notify, article_settings, article_subs, articles

router = APIRouter(prefix="/api/artikler", tags=["artikler"])


def need(feature: str = "artikler"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> article_access.Access:
        acc = await article_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


def _comment_error(exc: article_comments.CommentError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# ---------------------------------------------------------------- læsning

@router.get("/forside")
async def forside(acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await articles.front(db, acc.can_edit, await article_settings.get(db))


@router.get("")
async def soeg(q: str = Query("", max_length=200), mode: str = Query("ord", pattern="^(ord|praecis)$"), tag: list[int] = Query([], max_length=8),
               art: list[int] = Query([], max_length=20), forfatter: str = Query("", max_length=160), fra: int | None = Query(None, ge=1900, le=2100),
               til: int | None = Query(None, ge=1900, le=2100), type: str = Query("", max_length=60), kind: str = Query("", max_length=12),
               har_billeder: bool = False, har_pdf: bool = False, sort: str = "relevans", side: int = Query(1, ge=1, le=10000),
               acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await articles.search(db, q=q, mode=mode, tags=tag, arter=art, forfatter=forfatter, fra=fra, til=til, type_=type, kind=kind,
                                 har_billeder=har_billeder, har_pdf=har_pdf, sort=sort, page=side, can_edit=acc.can_edit)


@router.get("/emneord")
async def emneord(acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await articles.tags_overview(db, acc.can_edit)


@router.get("/arter")
async def arter(acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await articles.species_overview(db, acc.can_edit)


@router.get("/arter/{artnr}")
async def art_artikler(artnr: int, limit: int = Query(5, ge=1, le=50), acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Artikler om en art (boksen på artssiden)."""
    return await articles.species_box(db, artnr, acc.can_edit, limit)


@router.get("/forfattere")
async def forfattere(acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    return await articles.authors_overview(db, acc.can_edit)


@router.get("/n/{netfugl_id}")
async def fra_netfugl(netfugl_id: int, acc: article_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Gamle netfugl.dk-adresser: slå artiklen op på dens oprindelige nummer."""
    aid = await articles.by_netfugl(db, netfugl_id)
    if aid is None:
        raise HTTPException(404, "Artiklen findes ikke i arkivet")
    return {"id": aid}


# ---------------------------------------------------------------- notifikationer

class PrefsIn(BaseModel):
    alle: bool | None = None
    kommentarer: bool | None = None


@router.get("/notifikationer")
async def prefs(user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_notifikationer")), db: AsyncSession = Depends(get_session)):
    return await article_subs.get_prefs(db, user.id)


@router.put("/notifikationer")
async def set_prefs(body: PrefsIn, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_notifikationer")),
                    db: AsyncSession = Depends(get_session)):
    await article_subs.set_prefs(db, user.id, body.alle, body.kommentarer)
    return await article_subs.get_prefs(db, user.id)


# ---------------------------------------------------------------- kommentarer (før /{id}, så stierne ikke forveksles)

class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=article_comments.MAX_LEN)
    parent_id: int | None = None


class ReportIn(BaseModel):
    reason: str = Field(max_length=40)
    note: str = Field("", max_length=500)


@router.patch("/kommentarer/{cid}")
async def ret_kommentar(cid: int, body: CommentIn, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_kommentarer")),
                        db: AsyncSession = Depends(get_session)):
    try:
        c = await article_comments.edit(db, user, cid, body.body)
    except article_comments.CommentError as exc:
        raise _comment_error(exc) from exc
    return {"id": c.id, "html": c.body_html}


@router.delete("/kommentarer/{cid}")
async def slet_kommentar(cid: int, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_kommentarer")),
                         db: AsyncSession = Depends(get_session)):
    try:
        await article_comments.remove_own(db, user, cid)
    except article_comments.CommentError as exc:
        raise _comment_error(exc) from exc
    return {"ok": True}


@router.post("/kommentarer/{cid}/anmeld")
async def anmeld(cid: int, body: ReportIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                 acc: article_access.Access = Depends(need("artikler_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        hidden = await article_comments.report(db, user, cid, body.reason, body.note)
    except article_comments.CommentError as exc:
        raise _comment_error(exc) from exc
    if hidden:
        tasks.add_task(article_notify.on_auto_hidden, cid)
    return {"ok": True}


# ---------------------------------------------------------------- artikelsiden

@router.get("/{aid:int}")
async def artikel(aid: int, tasks: BackgroundTasks, user: User = Depends(require_user), acc: article_access.Access = Depends(need()),
                  db: AsyncSession = Depends(get_session)):
    a = await articles.detail(db, aid, can_edit=acc.can_edit, user_id=user.id)
    if a is None:
        raise HTTPException(404, "Artiklen findes ikke")
    if a["status"] == "publiceret":
        tasks.add_task(articles.count_view, aid, user.id)
    a["comments_on"] = acc.on("artikler_kommentarer")
    return a


@router.get("/{aid:int}/kommentarer")
async def kommentarer(aid: int, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_kommentarer")),
                      db: AsyncSession = Depends(get_session)):
    return await article_comments.list_comments(db, aid, user.id, acc.can_moderate)


@router.post("/{aid:int}/kommentarer")
async def skriv_kommentar(aid: int, body: CommentIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                          acc: article_access.Access = Depends(need("artikler_kommentarer")), db: AsyncSession = Depends(get_session)):
    try:
        c = await article_comments.add(db, user, aid, body.body, body.parent_id, can_edit=acc.can_edit)
    except article_comments.CommentError as exc:
        raise _comment_error(exc) from exc
    tasks.add_task(article_notify.on_comment, c.id)
    return {"id": c.id}


@router.put("/{aid:int}/foelg")
async def foelg(aid: int, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_notifikationer")),
                db: AsyncSession = Depends(get_session)):
    await article_subs.follow(db, user.id, aid, True)
    return {"foelger": True}


@router.delete("/{aid:int}/foelg")
async def foelg_ikke(aid: int, user: User = Depends(require_user), acc: article_access.Access = Depends(need("artikler_notifikationer")),
                     db: AsyncSession = Depends(get_session)):
    await article_subs.follow(db, user.id, aid, False)
    return {"foelger": False}
