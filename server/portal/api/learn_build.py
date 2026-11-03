"""Læring 2.0: kursusbyggeren (docs/laering-spec.md §8) – redigering, publicering, import/eksport og medie-søgning."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_learn_builder, require_user
from portal.config import get_settings
from portal.db import get_session
from portal.models import Course, Question, Unit, User
from portal.services import imagemeta, learn_access, learn_build, learn_play, sound_quiz
from portal.services.auth import can

router = APIRouter(prefix="/api/learn/byg", tags=["learn"])
MEDIA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
               "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/webm": ".webm"}


builder = require_learn_builder


def _err(exc: learn_build.BuildError):
    raise HTTPException(exc.status, {"msg": exc.msg, **exc.extra} if exc.extra else exc.msg)


async def _course(db: AsyncSession, user: User, slug: str) -> Course:
    try:
        return await learn_build.load_editable(db, user, slug)
    except learn_build.BuildError as exc:
        _err(exc)


@router.get("/oversigt")
async def oversigt(user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Course).order_by(Course.kind, Course.number.nulls_last(), Course.title))).scalars().all()
    out = []
    for c in rows:
        if not learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id)):
            continue
        st = await learn_play.live_struct(db, c)
        w = await learn_build.warnings(db, st)
        owner = (await db.execute(text("SELECT navn FROM users WHERE id = :i"), {"i": c.created_by})).scalar() if c.created_by else None
        out.append({"slug": c.slug, "title": c.title, "kind": c.kind, "published": c.published, "visibility": c.visibility, "version": c.version, "owner": owner, "mine": c.created_by == user.id,
                    "n_units": len([u for u in st["units"] if u["id"]]), "n_acts": sum(len(u["acts"]) for u in st["units"]), "fejl": sum(1 for x in w if x["level"] == "fejl"),
                    "advarsler": sum(1 for x in w if x["level"] == "advarsel"), "changed": learn_build.changed_since_publish(c, st) if c.published else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None})
    return out


class NewIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    kind: str = "kursus"


@router.post("/kurser")
async def nyt(body: NewIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    try:
        c = await learn_build.create_course(db, user, body.title, body.kind)
    except learn_build.BuildError as exc:
        _err(exc)
    return {"slug": c.slug}


@router.get("/kurser/{slug}")
async def hent(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    return await learn_build.full(db, await _course(db, user, slug))


class CourseIn(BaseModel):
    model_config = {"extra": "ignore"}
    stamp: str | None = None
    title: str | None = None
    summary: str | None = None
    intro: str | None = None
    topic: str | None = Field(None, pattern="^(id|kald|saeson|andet)$")
    level: str | None = Field(None, max_length=16)
    kind: str | None = None
    number: int | None = None
    stage: str | None = None
    category: str | None = None
    card: str | None = None
    audience: str | None = None
    learn: list[dict] | None = None
    skills: list[str] | None = None
    hours: str | None = None
    hue: str | None = None
    sil: str | None = None
    visibility: str | None = None
    cover_media_id: int | None = None
    featured: bool | None = None


@router.put("/kurser/{slug}")
async def ret(slug: str, body: CourseIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    try:
        learn_build.check_stamp(c, body.stamp)
        for k, v in learn_build.clean_course_fields(user, c, body.model_dump(exclude_unset=True, exclude={"stamp"})).items():
            setattr(c, k, v)
        if body.visibility and body.visibility != "hold" and not can(user, "laering"):
            c.visibility = "hold"
        learn_build.touch(c, user)
    except learn_build.BuildError as exc:
        _err(exc)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


@router.delete("/kurser/{slug}")
async def slet(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    if c.published and not can(user, "laering"):
        raise HTTPException(403, "Kun redaktører kan slette et publiceret kursus")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


class UnitIn(BaseModel):
    stamp: str | None = None
    title: str | None = Field(None, max_length=160)
    lesson_md: str | None = Field(None, max_length=40000)
    blocks: list[dict] | None = None
    min_score_pct: int | None = Field(None, ge=0, le=100)
    est_minutes: int | None = Field(None, ge=0, le=600)


@router.post("/kurser/{slug}/enheder")
async def ny_enhed(slug: str, body: UnitIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    n = (await db.execute(select(func.count()).select_from(Unit).where(Unit.course_id == c.id))).scalar() or 0
    u = Unit(course_id=c.id, position=n, title=body.title or f"Lektion {n + 1}", lesson_md=body.lesson_md or "", blocks=learn_build.clean_blocks(body.blocks or []))
    db.add(u)
    learn_build.touch(c, user)
    await db.commit()
    return {"id": u.id, "updated_at": c.updated_at.isoformat()}


async def _unit(db: AsyncSession, user: User, slug: str, uid: int) -> tuple[Course, Unit]:
    c = await _course(db, user, slug)
    u = await db.get(Unit, uid)
    if u is None or u.course_id != c.id:
        raise HTTPException(404, "Lektionen findes ikke")
    return c, u


@router.put("/kurser/{slug}/enheder/{uid}")
async def ret_enhed(slug: str, uid: int, body: UnitIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c, u = await _unit(db, user, slug, uid)
    try:
        learn_build.check_stamp(c, body.stamp)
    except learn_build.BuildError as exc:
        _err(exc)
    data = body.model_dump(exclude_unset=True, exclude={"stamp"})
    if "blocks" in data:
        data["blocks"] = learn_build.clean_blocks(data["blocks"])
    for k, v in data.items():
        setattr(u, k, v)
    learn_build.touch(c, user)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


@router.delete("/kurser/{slug}/enheder/{uid}")
async def slet_enhed(slug: str, uid: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c, u = await _unit(db, user, slug, uid)
    await db.execute(delete(Question).where(Question.unit_id == uid))
    await db.delete(u)
    learn_build.touch(c, user)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


class ActIn(BaseModel):
    model_config = {"extra": "ignore"}
    stamp: str | None = None
    unit_id: int | None = None
    type: str = "valg"
    prompt: str = ""
    choices: list[dict] = Field(default_factory=list)
    explanation: str = ""
    config: dict = Field(default_factory=dict)
    accept: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    media_kind: str | None = None
    media_url: str | None = None
    media_credit: str | None = None
    media_photo_id: int | None = None
    media_rec_id: int | None = None
    ask_confidence: bool = True


@router.post("/kurser/{slug}/aktiviteter")
async def ny_aktivitet(slug: str, body: ActIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    try:
        data = learn_build.clean_act(body.model_dump())
    except learn_build.BuildError as exc:
        _err(exc)
    unit_id = body.unit_id
    if unit_id and not (await db.get(Unit, unit_id) or Unit(course_id=-1)).course_id == c.id:
        raise HTTPException(404, "Lektionen findes ikke")
    n = (await db.execute(select(func.count()).select_from(Question).where(Question.course_id == c.id, Question.unit_id == unit_id if unit_id else Question.unit_id.is_(None)))).scalar() or 0
    q = Question(course_id=c.id, unit_id=unit_id, position=n, **data)
    db.add(q)
    learn_build.touch(c, user)
    await db.commit()
    return {"id": q.id, "updated_at": c.updated_at.isoformat()}


async def _act(db: AsyncSession, user: User, slug: str, qid: int) -> tuple[Course, Question]:
    c = await _course(db, user, slug)
    q = await db.get(Question, qid)
    if q is None or q.course_id != c.id:
        raise HTTPException(404, "Aktiviteten findes ikke")
    return c, q


@router.put("/kurser/{slug}/aktiviteter/{qid}")
async def ret_aktivitet(slug: str, qid: int, body: ActIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c, q = await _act(db, user, slug, qid)
    try:
        learn_build.check_stamp(c, body.stamp)
        data = learn_build.clean_act(body.model_dump())
    except learn_build.BuildError as exc:
        _err(exc)
    for k, v in data.items():
        setattr(q, k, v)
    learn_build.touch(c, user)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


@router.delete("/kurser/{slug}/aktiviteter/{qid}")
async def slet_aktivitet(slug: str, qid: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c, q = await _act(db, user, slug, qid)
    await db.delete(q)
    learn_build.touch(c, user)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


class OrderIn(BaseModel):
    units: list[int] = Field(default_factory=list, max_length=200)
    acts: dict[str, list[int]] = Field(default_factory=dict)


@router.post("/kurser/{slug}/orden")
async def orden(slug: str, body: OrderIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    await learn_build.reorder(db, c, body.units, body.acts)
    learn_build.touch(c, user)
    await db.commit()
    return {"updated_at": c.updated_at.isoformat()}


@router.post("/kurser/{slug}/publicer")
async def publicer(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    try:
        await learn_build.publish(db, user, c)
    except learn_build.BuildError as exc:
        _err(exc)
    return {"version": c.version, "visibility": c.visibility}


@router.post("/kurser/{slug}/afpublicer")
async def afpublicer(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    c.published = False
    learn_build.touch(c, user)
    await db.commit()
    return {"ok": True}


@router.post("/kurser/{slug}/fortryd")
async def fortryd(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    try:
        await learn_build.revert(db, user, c)
    except learn_build.BuildError as exc:
        _err(exc)
    return {"ok": True}


@router.post("/kurser/{slug}/kopi")
async def kopi(slug: str, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    return {"slug": (await learn_build.duplicate(db, user, c)).slug}


class EditorIn(BaseModel):
    user_id: int


@router.post("/kurser/{slug}/medforfattere")
async def medforfatter(slug: str, body: EditorIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    if c.created_by != user.id and not can(user, "laering"):
        raise HTTPException(403, "Kun ejeren kan tilføje medforfattere")
    if await db.get(User, body.user_id) is None:
        raise HTTPException(404, "Brugeren findes ikke")
    await db.execute(text("INSERT INTO lr_course_editors (course_id, user_id) VALUES (:c, :u) ON CONFLICT DO NOTHING"), {"c": c.id, "u": body.user_id})
    await db.commit()
    return {"ok": True}


@router.delete("/kurser/{slug}/medforfattere/{uid}")
async def fjern_medforfatter(slug: str, uid: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    c = await _course(db, user, slug)
    await db.execute(text("DELETE FROM lr_course_editors WHERE course_id = :c AND user_id = :u"), {"c": c.id, "u": uid})
    await db.commit()
    return {"ok": True}


class ExampleIn(BaseModel):
    type: str
    config: dict = Field(default_factory=dict)
    prompt: str = ""


@router.post("/eksempler")
async def eksempler(body: ExampleIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    if body.type not in learn_play.TYPES or body.type in learn_play.STATIC:
        raise HTTPException(400, "Kun datadrevne aktiviteter har eksempler")
    return {"questions": await learn_build.examples(db, user, {"type": body.type, "config": body.config, "prompt": body.prompt, "ask_confidence": True})}


class ImportIn(BaseModel):
    data: dict
    mode: str = Field("kopi", pattern="^(kopi|overskriv)$")
    dry_run: bool = False


@router.post("/import")
async def importer(body: ImportIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    try:
        return {"rapport": await learn_build.import_courses(db, user, body.data, body.mode, body.dry_run)}
    except learn_build.BuildError as exc:
        _err(exc)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Filen kunne ikke læses: {exc}") from exc


@router.get("/eksport")
async def eksport(slug: str | None = None, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    return await learn_build.export_courses(db, user, [s for s in (slug or "").split(",") if s] or None)


@router.get("/fotos")
async def fotos(q: str = "", artnr: int | None = None, offset: int = 0, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    return await learn_build.search_photos(db, q, artnr, 40, max(0, offset))


@router.get("/lyde")
async def lyde(artnr: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    return await learn_build.search_sounds(db, artnr)


@router.post("/lyde/hent")
async def lyde_hent(artnr: int, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    """Hent optagelser fra xeno-canto til en art (kræver lydquizzen og en nøgle under Admin → Funktioner → Lydquiz)."""
    if not await learn_access.lydquiz(db, user):
        raise HTTPException(404, "Lydquizzen er ikke slået til")
    try:
        n = 0
        for rtype in ("song", "call"):
            n += await sound_quiz.ensure_species(db, artnr, rtype)
    except sound_quiz.SoundError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"hentet": n}


@router.post("/medie")
async def medie(file: UploadFile = File(...), user: User = Depends(builder)):
    ext = MEDIA_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Kun billeder (jpg/png/webp/gif) og lyd (mp3/ogg/wav/m4a/webm)")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Filen er større end 15 MB")
    audio = ext in (".mp3", ".ogg", ".wav", ".m4a", ".webm")
    if not audio:
        try:
            data = imagemeta.strip(data)
        except ValueError as exc:
            raise HTTPException(400, "Billedet kunne ikke læses") from exc
    root = Path(get_settings().media_dir)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (root / name).write_bytes(data)
    return {"url": f"/media/{name}", "kind": "audio" if audio else "image"}


class MdIn(BaseModel):
    md: str = Field(max_length=40000)


@router.post("/forhaandsvis")
async def forhaandsvis(body: MdIn, user: User = Depends(builder), db: AsyncSession = Depends(get_session)):
    return {"html": await learn_play.render_md(db, body.md)}
