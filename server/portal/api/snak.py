"""Snak (brugerdelen): feed, opslag, kommentarer og reaktioner (docs/snak-modul-spec.md §10).

Alt kræver, at funktionen «snak» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. SQL ligger i services (snak*)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import activity, content_media, content_render, snak, snak_access, snak_admin, snak_attach, snak_comments, snak_links, snak_notify, snak_reactions, snak_reports

router = APIRouter(prefix="/api/snak", tags=["snak"])
ReactionType = Field(pattern="^(like|love|haha|wow|sad|angry)$")


def need(feature: str = "snak"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> snak_access.Access:
        acc = await snak_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


def _media(acc: snak_access.Access, ids: list[int]) -> list[int]:
    if ids and not acc.on("snak_billeder"):
        raise HTTPException(403, "Billeder er slået fra")
    return ids


def _att(acc: snak_access.Access, att: AttachmentIn | None) -> dict | None:
    if att and att.type == "link" and not acc.on("snak_links"):
        raise HTTPException(403, "Links med forhåndsvisning er slået fra")
    return att.model_dump() if att else None


def _wrap(exc: Exception) -> HTTPException:
    return HTTPException(exc.status, exc.message)


class AttachmentIn(BaseModel):
    type: str = Field(max_length=12)
    ref: str = Field(max_length=2000)


class PostIn(BaseModel):
    category_id: int
    titel: str | None = Field(None, max_length=200)
    body: str = Field("", max_length=snak.MAX_BODY + 1000)
    media_ids: list[int] = Field(default_factory=list, max_length=20)
    attachment: AttachmentIn | None = None
    arter: list[int] = Field(default_factory=list, max_length=10)
    loknr: int | None = None


class CommentIn(BaseModel):
    body: str = Field("", max_length=snak_comments.MAX_LEN + 1000)
    parent_id: int | None = None
    media_id: int | None = None


class SolutionIn(BaseModel):
    comment_id: int | None = None


class LinkIn(BaseModel):
    url: str = Field(max_length=2000)


class RenderIn(BaseModel):
    body: str = Field(max_length=snak.MAX_BODY + 1000)


class ReactionIn(BaseModel):
    type: str = ReactionType


class ModerateIn(BaseModel):
    action: str = Field(pattern="^(skjul|vis|laas|aabn|flyt|afvis_anmeldelser)$")
    reason: str = Field("", max_length=500)
    category_id: int | None = None


class CommentModerateIn(BaseModel):
    action: str = Field(pattern="^(skjul|vis|afvis_anmeldelser)$")
    reason: str = Field("", max_length=500)


class PinIn(BaseModel):
    pinned: bool


class ReportIn(BaseModel):
    reason: str = Field(max_length=20)
    note: str = Field("", max_length=500)


class PrefsIn(BaseModel):
    valg: dict[str, bool] = Field(default_factory=dict, max_length=20)


class CategoryIn(BaseModel):
    slug: str | None = Field(None, max_length=40)
    navn: str | None = Field(None, max_length=60)
    beskrivelse: str | None = Field(None, max_length=300)
    farve: str | None = Field(None, max_length=16)
    ikon: str | None = Field(None, max_length=24)
    sort: int | None = Field(None, ge=0, le=100_000)
    kraever_titel: bool | None = None
    har_loesning: bool | None = None
    kun_staff: bool | None = None
    arkiveret: bool | None = None


class MoveIn(BaseModel):
    to: int


class SettingsIn(BaseModel):
    values: dict[str, int] = Field(max_length=30)


def admin_only():
    async def dep(acc: snak_access.Access = Depends(need())) -> snak_access.Access:
        if not acc.admin:
            raise HTTPException(403, "Kun administratorer af Snak kan det")
        return acc

    return dep


def mod_only():
    async def dep(acc: snak_access.Access = Depends(need())) -> snak_access.Access:
        if not acc.can_moderate:
            raise HTTPException(403, "Kun moderatorer kan det")
        return acc

    return dep


@router.get("/kategorier")
async def kategorier(acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    cfg = await snak.settings(db)
    return {"categories": await snak.categories(db), "feed": {"kommentarer": cfg["feed_kommentarer"], "svar": cfg["feed_svar"]}, "reactions": {k: {"emoji": e, "navn": n} for k, (e, n) in snak_reactions.TYPES.items()}}


@router.get("/feed")
async def feed(sort: str = "aktivitet", kategori: str | None = Query(None, max_length=40), uloeste: bool = False, cursor: str | None = Query(None, max_length=200),
               user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await snak.feed(db, user, staff=acc.can_moderate, sort=sort, category=kategori, uloeste=uloeste, cursor=cursor, flags_on=acc.on)
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.post("")
async def opret(body: PostIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return {"id": await snak.create(db, user, staff=acc.staff, category_id=body.category_id, titel=body.titel, body=body.body,
                                                      media_ids=_media(acc, body.media_ids), attachment=_att(acc, body.attachment), arter=body.arter, loknr=body.loknr)}
    except snak.SnakError as e:
        raise _wrap(e) from e


# ---------------------------------------------------------------- notifikationsvalg, moderationskø og admin (faste adresser før /{post_id})

@router.get("/notifikationer")
async def notifikationer(user: User = Depends(require_user), acc: snak_access.Access = Depends(need("snak_notifikationer")), db: AsyncSession = Depends(get_session)):
    return await snak_notify.get_prefs(db, user.id)


@router.put("/notifikationer")
async def sæt_notifikationer(body: PrefsIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need("snak_notifikationer")), db: AsyncSession = Depends(get_session)):
    await snak_notify.set_prefs(db, user.id, body.valg)
    return await snak_notify.get_prefs(db, user.id)


@router.get("/admin/koe")
async def koe(tab: str = Query("anmeldte", max_length=12), side: int = Query(1, ge=1, le=1000), acc: snak_access.Access = Depends(mod_only()), db: AsyncSession = Depends(get_session)):
    try:
        return {"rows": await snak_reports.queue(db, tab, snak_reports.PAGE, (side - 1) * snak_reports.PAGE)}
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.get("/admin/kategorier")
async def admin_kategorier(acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    return {"categories": await snak.categories(db, include_archived=True)}


@router.post("/admin/kategorier")
async def admin_ny_kategori(body: CategoryIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    try:
        cid = await snak_admin.create_category(db, body.model_dump(exclude_none=True))
    except snak.SnakError as e:
        raise _wrap(e) from e
    await activity.track(user.id, "snak_kategori", "/snak/admin", {"handling": "opret", "id": cid})
    return {"id": cid}


@router.patch("/admin/kategorier/{cid}")
async def admin_ret_kategori(cid: int, body: CategoryIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    try:
        await snak_admin.update_category(db, cid, body.model_dump(exclude_none=True))
    except snak.SnakError as e:
        raise _wrap(e) from e
    await activity.track(user.id, "snak_kategori", "/snak/admin", {"handling": "ret", "id": cid})
    return {"ok": True}


@router.delete("/admin/kategorier/{cid}")
async def admin_slet_kategori(cid: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    try:
        await snak_admin.delete_category(db, cid)
    except snak.SnakError as e:
        raise _wrap(e) from e
    await activity.track(user.id, "snak_kategori", "/snak/admin", {"handling": "slet", "id": cid})
    return {"ok": True}


@router.post("/admin/kategorier/{cid}/flyt")
async def admin_flyt_kategori(cid: int, body: MoveIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    try:
        n = await snak_admin.move_posts(db, cid, body.to)
    except snak.SnakError as e:
        raise _wrap(e) from e
    await activity.track(user.id, "snak_kategori", "/snak/admin", {"handling": "flyt", "id": cid, "til": body.to, "antal": n})
    return {"moved": n}


@router.get("/admin/indstillinger")
async def admin_indstillinger(acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    return {"values": await snak.settings(db), "defaults": snak.DEFAULTS}


@router.put("/admin/indstillinger")
async def admin_gem_indstillinger(body: SettingsIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    values = await snak.save_settings(db, body.values)
    await activity.track(user.id, "snak_indstillinger", "/snak/admin", {"values": body.values})
    return {"values": values, "defaults": snak.DEFAULTS}


@router.get("/admin/eksempler")
async def admin_eksempler(acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    return await snak_admin.examples(db)


@router.post("/admin/eksempler")
async def admin_gendan_eksempler(user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    n = await snak_admin.restore_examples(db)
    await activity.track(user.id, "snak_eksempler", "/snak/admin", {"handling": "gendan", "antal": n})
    return {"created": n}


@router.delete("/admin/eksempler")
async def admin_slet_eksempler(user: User = Depends(require_user), acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    n = await snak_admin.delete_examples(db)
    await activity.track(user.id, "snak_eksempler", "/snak/admin", {"handling": "slet", "antal": n})
    return {"deleted": n}


@router.get("/admin/statistik")
async def admin_statistik(acc: snak_access.Access = Depends(admin_only()), db: AsyncSession = Depends(get_session)):
    return await snak_admin.stats(db)


@router.get("/{post_id}")
async def opslag(post_id: int, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return await snak.get_post(db, post_id, user, staff=acc.can_moderate, flags_on=acc.on)
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.put("/{post_id}")
async def ret(post_id: int, body: PostIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.edit(db, user, post_id, titel=body.titel, body=body.body, media_ids=_media(acc, body.media_ids), attachment=_att(acc, body.attachment),
                        arter=body.arter, loknr=body.loknr, fields=True)
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
    await activity.track(user.id, "snak_moderation", f"/snak/{post_id}", {"kind": "post", "id": post_id, "handling": body.action})
    return {"ok": True}


@router.post("/{post_id}/anmeld")
async def anmeld_opslag(post_id: int, body: ReportIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return {"hidden": await snak_reports.report(db, user, "post", post_id, body.reason, body.note)}
    except snak.SnakError as e:
        raise _wrap(e) from e


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
        c = await snak_comments.add(db, user, post_id, body.body, body.parent_id, staff=acc.staff, media_id=body.media_id if acc.on('snak_billeder') else None)
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
    await activity.track(user.id, "snak_moderation", "/snak", {"kind": "comment", "id": cid, "handling": body.action})
    return {"ok": True}


@router.post("/kommentarer/{cid}/anmeld")
async def anmeld_kommentar(cid: int, body: ReportIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return {"hidden": await snak_reports.report(db, user, "comment", cid, body.reason, body.note)}
    except snak.SnakError as e:
        raise _wrap(e) from e


@router.post("/{post_id}/loesning")
async def loesning(post_id: int, body: SolutionIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        await snak.set_solution(db, user, post_id, body.comment_id, staff=acc.can_moderate)
    except snak.SnakError as e:
        raise _wrap(e) from e
    return {"ok": True}


# ---------------------------------------------------------------- billeder, forhåndsvisning og vedhæftninger

@router.post("/media")
async def upload(file: UploadFile = File(...), kind: str = Form("post"), user: User = Depends(require_user), acc: snak_access.Access = Depends(need("snak_billeder")),
                 db: AsyncSession = Depends(get_session)):
    """Billede til et opslag (kind=post) eller en kommentar (kind=comment). Hører til brugeren, indtil det gemmes sammen med opslaget."""
    if kind not in snak.MEDIA_KIND:
        raise HTTPException(400, "Ukendt type")
    data = await file.read(content_media.MAX_IMAGE + 1)
    key = f"snak:upload:rate:{user.id}"
    from portal.cache import kv

    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 600)
    if n > 60:
        raise HTTPException(429, "For mange uploads – vent lidt")
    try:
        m = await content_media.store_image(db, data, filename=file.filename or "billede", owner_kind=snak.MEDIA_KIND[kind], owner_id=None, user_id=user.id)
    except content_media.MediaError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return snak.media_out({"id": m.id, "storage_key": m.storage_key, "thumb_key": m.thumb_key, "medium_key": m.medium_key, "width": m.width, "height": m.height, "alt": m.alt})


@router.post("/render")
async def render(body: RenderIn, acc: snak_access.Access = Depends(need())):
    return {"html": content_render.render(body.body, "kommentar").html}


@router.post("/link-preview")
async def link_preview(body: LinkIn, user: User = Depends(require_user), acc: snak_access.Access = Depends(need("snak_links")), db: AsyncSession = Depends(get_session)):
    if not (await snak.settings(db))["link_preview"]:
        raise HTTPException(403, "Forhåndsvisning af links er slået fra")
    try:
        return await snak_links.preview(user.id, body.url)
    except snak_links.LinkError as e:
        raise _wrap(e) from e


@router.get("/vedhaeft/soeg")
async def vedhaeft_soeg(type: str = Query(max_length=12), q: str = Query("", max_length=100), acc: snak_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    try:
        return {"rows": await snak_attach.search(db, type, q)}
    except snak_attach.AttachError as e:
        raise _wrap(e) from e
