"""Læring 2.0: forløb, hold, feltopgaver og beviser (docs/laering-spec.md §9–10)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_learn_builder as builder
from portal.api.deps import require_user
from portal.db import get_session
from portal.models import Course, LrCohort, LrCohortMember, LrPath, LrPathItem, User
from portal.services import learn_access, learn_paths, notify
from portal.services.auth import can

router = APIRouter(prefix="/api/learn", tags=["learn"])


async def forloeb_user(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not (await learn_access.portal(db, user) and await learn_access.forloeb(db, user)):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


def _err(exc: learn_paths.PathError):
    raise HTTPException(exc.status, {"msg": exc.msg, **exc.extra} if exc.extra else exc.msg)


async def _path(db: AsyncSession, user: User, slug: str, edit: bool = False) -> LrPath:
    p = (await db.execute(select(LrPath).where(LrPath.slug == slug))).scalar_one_or_none()
    if p is None or not (learn_paths.can_edit_path(user, p) if edit else await learn_paths.can_view_path(db, user, p)):
        raise HTTPException(404 if not edit else 403, "Forløbet findes ikke" if not edit else "Du må ikke redigere forløbet")
    return p


# ---------------------------------------------------------------- deltagere

@router.get("/forloeb")
async def forloeb_liste(user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    return {"forloeb": await learn_paths.catalog(db, user), "hold": await learn_paths.member_cohorts(db, user.id)}


@router.get("/forloeb/{slug}")
async def forloeb_vis(slug: str, hold: int | None = None, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    p = await _path(db, user, slug)
    return await learn_paths.path_view(db, user, p, hold)


@router.post("/forloeb/{slug}/tilmeld")
async def tilmeld(slug: str, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    p = await _path(db, user, slug)
    if not p.published:
        raise HTTPException(403, "Forløbet er ikke publiceret")
    await learn_paths.enroll(db, user, p)
    return {"ok": True}


class MarkIn(BaseModel):
    hold: int | None = None
    note: str | None = Field(None, max_length=2000)


async def _item(db: AsyncSession, p: LrPath, item_id: int) -> LrPathItem:
    it = await db.get(LrPathItem, item_id)
    if it is None or it.path_id != p.id:
        raise HTTPException(404, "Punktet findes ikke")
    return it


@router.post("/forloeb/{slug}/punkter/{item_id}/marker")
async def marker(slug: str, item_id: int, body: MarkIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    p = await _path(db, user, slug)
    await learn_paths.enroll(db, user, p, body.hold)
    try:
        await learn_paths.mark_self(db, user, p, await _item(db, p, item_id), body.hold, body.note)
    except learn_paths.PathError as exc:
        _err(exc)
    return {"ok": True}


@router.post("/forloeb/{slug}/punkter/{item_id}/tjek")
async def tjek_felt(slug: str, item_id: int, body: MarkIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    if not await learn_access.felt(db, user):
        raise HTTPException(404, "Feltopgaver er ikke slået til")
    p = await _path(db, user, slug)
    it = await _item(db, p, item_id)
    if it.kind != "felt":
        raise HTTPException(400, "Punktet er ikke en feltopgave")
    await learn_paths.enroll(db, user, p, body.hold)
    cohorts = await learn_paths.member_cohorts(db, user.id, p.id)
    cohort = next((c for c in cohorts if c["id"] == body.hold), None) if body.hold else next((c for c in cohorts if not c["archived"]), None)
    return await learn_paths.check_field(db, user, p, it, cohort)


@router.get("/bevis/{number}")
async def bevis(number: str, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await learn_paths.certificate(db, number)
    if c is None:
        raise HTTPException(404, "Beviset findes ikke")
    return c


# ---------------------------------------------------------------- hold

class CohortIn(BaseModel):
    path: str
    title: str = Field(min_length=2, max_length=160)
    starts_on: datetime | None = None
    ends_on: datetime | None = None
    afdeling_id: int | None = None
    open_join: bool = False
    max_members: int | None = Field(None, ge=1, le=500)


@router.post("/hold")
async def hold_opret(body: CohortIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    p = (await db.execute(select(LrPath).where(LrPath.slug == body.path))).scalar_one_or_none()
    if p is None or not (p.published or learn_paths.can_edit_path(user, p)):
        raise HTTPException(404, "Forløbet findes ikke")
    try:
        c = await learn_paths.create_cohort(db, user, p, body.title, body.starts_on, body.ends_on, body.afdeling_id, body.open_join, body.max_members)
    except learn_paths.PathError as exc:
        _err(exc)
    await learn_paths.enroll(db, user, p, c.id)
    return {"id": c.id, "join_code": c.join_code}


@router.get("/hold")
async def hold_mine(user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    rows = await learn_paths.member_cohorts(db, user.id)
    for r in rows:
        r["path"] = (await db.execute(text("SELECT slug, title FROM lr_paths WHERE id = :i"), {"i": r["path_id"]})).mappings().first()
        if r["role"] != "underviser":
            r.pop("join_code", None)
    return rows


class JoinIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.post("/hold/tilmeld")
async def hold_tilmeld(body: JoinIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    try:
        c = await learn_paths.join(db, user, body.code)
    except learn_paths.PathError as exc:
        _err(exc)
    return {"id": c.id}


async def _cohort(db: AsyncSession, user: User, cid: int, teacher: bool = False) -> LrCohort:
    c = await db.get(LrCohort, cid)
    if c is None:
        raise HTTPException(404, "Holdet findes ikke")
    member = await db.get(LrCohortMember, (cid, user.id))
    if teacher and not await learn_paths.is_teacher(db, user, cid):
        raise HTTPException(403, "Kun holdets undervisere kan det")
    if not teacher and member is None and not can(user, "laering"):
        raise HTTPException(404, "Holdet findes ikke")
    return c


@router.get("/hold/{cid}")
async def hold_vis(cid: int, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await _cohort(db, user, cid)
    out = await learn_paths.cohort_view(db, user, c)
    if out["teacher"]:
        out["venter"] = await learn_paths.pending_field(db, c)
    return out


class CohortPut(BaseModel):
    title: str | None = Field(None, max_length=160)
    starts_on: datetime | None = None
    ends_on: datetime | None = None
    open_join: bool | None = None
    max_members: int | None = Field(None, ge=1, le=500)
    archived: bool | None = None


@router.put("/hold/{cid}")
async def hold_ret(cid: int, body: CohortPut, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await _cohort(db, user, cid, teacher=True)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    await db.commit()
    return {"ok": True}


@router.post("/hold/{cid}/forlad")
async def hold_forlad(cid: int, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    m = await db.get(LrCohortMember, (cid, user.id))
    if m is None:
        raise HTTPException(404)
    if m.role == "underviser" and (await db.execute(select(func.count()).select_from(LrCohortMember).where(LrCohortMember.cohort_id == cid, LrCohortMember.role == "underviser"))).scalar() <= 1:
        raise HTTPException(409, "Holdet skal have mindst én underviser – arkivér det i stedet")
    await db.delete(m)
    await db.commit()
    return {"ok": True}


@router.delete("/hold/{cid}/medlemmer/{uid}")
async def hold_fjern(cid: int, uid: int, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    await _cohort(db, user, cid, teacher=True)
    m = await db.get(LrCohortMember, (cid, uid))
    if m is None or m.role == "underviser":
        raise HTTPException(404)
    await db.delete(m)
    await db.commit()
    return {"ok": True}


class RoleIn(BaseModel):
    user_id: int


@router.post("/hold/{cid}/undervisere")
async def hold_underviser(cid: int, body: RoleIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    await _cohort(db, user, cid, teacher=True)
    m = await db.get(LrCohortMember, (cid, body.user_id))
    if m is None:
        raise HTTPException(404, "Brugeren er ikke på holdet")
    m.role = "underviser"
    await db.commit()
    return {"ok": True}


@router.get("/hold/{cid}/fremgang.csv")
async def hold_csv(cid: int, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await _cohort(db, user, cid, teacher=True)
    v = await learn_paths.cohort_view(db, user, c)
    return Response(learn_paths.csv_matrix(v["matrix"]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="hold-{cid}.csv"'})


class MsgIn(BaseModel):
    tekst: str = Field(min_length=1, max_length=500)


@router.post("/hold/{cid}/besked")
async def hold_besked(cid: int, body: MsgIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await _cohort(db, user, cid, teacher=True)
    ids = (await db.execute(select(LrCohortMember.user_id).where(LrCohortMember.cohort_id == cid, LrCohortMember.user_id != user.id))).scalars().all()
    stamp = int(datetime.now(timezone.utc).timestamp())
    n = await notify.notify(db, ids, title=f"Besked fra {user.navn or 'din underviser'}: {c.title}", body=body.tekst, url=f"/#/laer/hold/{cid}", dedup=f"lr:hold:{cid}:{stamp}")
    return {"sendt": len(ids), "push": n}


class ApproveIn(BaseModel):
    godkend: bool


@router.post("/hold/{cid}/felt/{item_id}/{uid}")
async def hold_godkend(cid: int, item_id: int, uid: int, body: ApproveIn, user: User = Depends(forloeb_user), db: AsyncSession = Depends(get_session)):
    c = await _cohort(db, user, cid, teacher=True)
    try:
        await learn_paths.approve_field(db, c, item_id, uid, body.godkend)
    except learn_paths.PathError as exc:
        _err(exc)
    return {"ok": True}


# ---------------------------------------------------------------- bygger: forløb

class PathIn(BaseModel):
    model_config = {"extra": "ignore"}
    title: str | None = Field(None, max_length=160)
    summary: str | None = Field(None, max_length=1000)
    intro_md: str | None = Field(None, max_length=20000)
    level: str | None = Field(None, max_length=16)
    audience: str | None = Field(None, max_length=1000)
    visibility: str | None = Field(None, pattern="^(alle|login|hold)$")
    certificate: bool | None = None
    certificate_text: str | None = Field(None, max_length=1000)


async def _bpath(db: AsyncSession, user: User, slug: str) -> LrPath:
    return await _path(db, user, slug, edit=True)


@router.get("/byg/forloeb")
async def byg_liste(user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    if not await learn_access.forloeb(db, user):
        raise HTTPException(404)
    rows = (await db.execute(select(LrPath).order_by(LrPath.title))).scalars().all()
    out = []
    for p in rows:
        if learn_paths.can_edit_path(user, p):
            w = await learn_paths.path_warnings(db, p)
            n = (await db.execute(select(func.count()).select_from(LrPathItem).where(LrPathItem.path_id == p.id))).scalar()
            out.append({"slug": p.slug, "title": p.title, "published": p.published, "visibility": p.visibility, "n_punkter": n, "fejl": sum(1 for x in w if x["level"] == "fejl")})
    return out


class NewPath(BaseModel):
    title: str = Field(min_length=2, max_length=160)


@router.post("/byg/forloeb")
async def byg_nyt(body: NewPath, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    if not await learn_access.forloeb(db, user):
        raise HTTPException(404)
    slug, n = learn_paths.slugify(body.title), 1
    while (await db.execute(select(LrPath.id).where(LrPath.slug == slug))).first():
        n += 1
        slug = f"{learn_paths.slugify(body.title)[:60]}-{n}"
    p = LrPath(slug=slug, title=body.title.strip(), created_by=user.id, visibility="hold" if not can(user, "laering") else "alle", updated_at=datetime.now(timezone.utc))
    db.add(p)
    await db.commit()
    return {"slug": p.slug}


@router.get("/byg/forloeb/{slug}")
async def byg_hent(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    items = await learn_paths.load_items(db, p.id)
    courses = {c.id: c for c in (await db.execute(select(Course).where(Course.id.in_([i.course_id for i in items if i.course_id] or [0])))).scalars()}
    return {"path": {k: getattr(p, k) for k in ("slug", "title", "summary", "intro_md", "level", "audience", "published", "visibility", "certificate", "certificate_text")},
            "items": [learn_paths.item_out(i, courses.get(i.course_id)) for i in items], "warnings": await learn_paths.path_warnings(db, p)}


@router.put("/byg/forloeb/{slug}")
async def byg_ret(slug: str, body: PathIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "visibility" and v != "hold" and not can(user, "laering"):
            continue
        setattr(p, k, v)
    p.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.delete("/byg/forloeb/{slug}")
async def byg_slet(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    await db.delete(p)
    await db.commit()
    return {"ok": True}


@router.post("/byg/forloeb/{slug}/publicer")
async def byg_publicer(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    errs = [w for w in await learn_paths.path_warnings(db, p) if w["level"] == "fejl"]
    if errs:
        raise HTTPException(400, {"msg": f"{len(errs)} fejl skal rettes, før forløbet kan publiceres", "warnings": errs})
    p.published = True
    if not can(user, "laering"):
        p.visibility = "hold"
    await db.commit()
    return {"ok": True}


@router.post("/byg/forloeb/{slug}/afpublicer")
async def byg_afpublicer(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    p.published = False
    await db.commit()
    return {"ok": True}


class ItemIn(BaseModel):
    model_config = {"extra": "ignore"}
    kind: str = Field(pattern="^(kursus|ovelse|traening|felt|tekst)$")
    section: str = Field("", max_length=80)
    title: str = Field("", max_length=160)
    course: str | None = None
    text_md: str | None = Field(None, max_length=10000)
    training: dict | None = None
    field_task: dict | None = None
    required: bool = True
    min_score_pct: int | None = Field(None, ge=0, le=100)
    unlock: dict | None = None


async def _fill(db: AsyncSession, it: LrPathItem, b: ItemIn) -> None:
    course_id = None
    if b.kind in ("kursus", "ovelse"):
        c = (await db.execute(select(Course).where(Course.slug == (b.course or "")))).scalar_one_or_none()
        if c is None:
            raise HTTPException(400, "Vælg et kursus eller en øvelse")
        if (b.kind == "kursus") != (c.kind == "kursus"):
            raise HTTPException(400, "Typen passer ikke til det valgte indhold")
        course_id = c.id
    it.kind, it.section, it.title, it.course_id, it.text_md = b.kind, b.section.strip(), b.title.strip(), course_id, b.text_md
    it.training = ({k: v for k, v in b.training.items() if k in ("kilde", "gruppe", "arter", "loknr", "afdeling_id", "kursus", "m", "metode", "rtype", "n")} if b.training and b.kind == "traening" else None)
    it.field_task = learn_paths.clean_field_task(b.field_task) if b.kind == "felt" else None
    it.required, it.min_score_pct, it.unlock = b.required, b.min_score_pct, learn_paths.clean_unlock(b.unlock)


@router.post("/byg/forloeb/{slug}/punkter")
async def byg_nyt_punkt(slug: str, body: ItemIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    n = (await db.execute(select(func.count()).select_from(LrPathItem).where(LrPathItem.path_id == p.id))).scalar() or 0
    it = LrPathItem(path_id=p.id, position=n, kind=body.kind)
    await _fill(db, it, body)
    db.add(it)
    await db.commit()
    return {"id": it.id}


@router.put("/byg/forloeb/{slug}/punkter/{item_id}")
async def byg_ret_punkt(slug: str, item_id: int, body: ItemIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    it = await _item(db, p, item_id)
    await _fill(db, it, body)
    await db.commit()
    return {"ok": True}


@router.delete("/byg/forloeb/{slug}/punkter/{item_id}")
async def byg_slet_punkt(slug: str, item_id: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    await db.execute(delete(LrPathItem).where(LrPathItem.id == (await _item(db, p, item_id)).id))
    await db.commit()
    return {"ok": True}


class OrdenIn(BaseModel):
    items: list[int] = Field(max_length=300)
    sections: dict[str, str] = Field(default_factory=dict)


@router.post("/byg/forloeb/{slug}/orden")
async def byg_orden(slug: str, body: OrdenIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    p = await _bpath(db, user, slug)
    items = {i.id: i for i in await learn_paths.load_items(db, p.id)}
    for pos, iid in enumerate(body.items):
        if iid in items:
            items[iid].position = pos
            if str(iid) in body.sections:
                items[iid].section = body.sections[str(iid)][:80]
    await db.commit()
    return {"ok": True}
