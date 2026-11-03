"""E-læring: kurser, spørgsmål, quizzer og redigering (editor/admin)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_perm, require_user
from portal.config import get_settings
from portal.db import get_session
from portal.models import Course, Question, QuizResult, User
from portal.services import imagemeta, learn_access, learning, sound_quiz, species_quiz
from portal.services.auth import can

router = APIRouter(prefix="/api/learn", tags=["learn"])

MEDIA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/webm": ".webm"}


def course_out(c: Course, n_questions: int = 0, progress: dict | None = None) -> dict:
    return {"id": c.id, "slug": c.slug, "title": c.title, "summary": c.summary, "topic": c.topic, "level": c.level,
            "intro": c.intro, "published": c.published, "n_questions": n_questions, "progress": progress}


@router.get("/courses")
async def courses(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    editor = can(user, "laering")
    q = select(Course, func.count(Question.id)).outerjoin(Question).group_by(Course.id).order_by(Course.topic, Course.title)
    if not editor:
        q = q.where(Course.published)
    rows = (await db.execute(q)).all()
    prog = await learning.course_progress(db, user.id) if user else {}
    return {"courses": [course_out(c, n, prog.get(c.id)) for c, n in rows],
            "auto": [{"kind": k, "title": v} for k, v in learning.AUTO_KINDS.items()],
            "artsquiz": bool(user) and await learn_access.artsquiz(db, user),
            "lydquiz": bool(user) and await learn_access.lydquiz(db, user)}


async def _course(db: AsyncSession, slug: str, user: User | None) -> Course:
    c = (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
    if c is None or (not c.published and not can(user, "laering")):
        raise HTTPException(404, "Kurset findes ikke")
    return c


@router.get("/courses/{slug}")
async def course(slug: str, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    qs = (await db.execute(select(Question).where(Question.course_id == c.id).order_by(Question.position, Question.id))).scalars().all()
    out = course_out(c, len(qs))
    if can(user, "laering"):
        out["questions"] = [{"id": q.id, "prompt": q.prompt, "choices": q.choices, "explanation": q.explanation,
                             "media_kind": q.media_kind, "media_url": q.media_url, "media_credit": q.media_credit,
                             "position": q.position} for q in qs]
    return out


@router.get("/courses/{slug}/session")
async def session(slug: str, n: int = Query(10, ge=1, le=50), mode: str = Query("smart", pattern="^(smart|random)$"),
                  user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    return {"course": course_out(c), "questions": await learning.session_questions(db, user.id if user else None, c, n, mode)}


class AnswerIn(BaseModel):
    question_id: int
    chosen: list[int] = Field(max_length=10)


@router.post("/answer")
async def answer(body: AnswerIn, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    q = await db.get(Question, body.question_id)
    if q is None:
        raise HTTPException(404)
    return await learning.answer(db, user.id if user else None, q, body.chosen)


@router.get("/auto/{kind}")
async def auto(kind: str, n: int = Query(10, ge=1, le=30), afdeling: int | None = None, db: AsyncSession = Depends(get_session)):
    if kind not in learning.AUTO_KINDS:
        raise HTTPException(404)
    qs = await learning.auto_quiz(db, kind, n, afdeling)
    if not qs:
        raise HTTPException(409, "Der er endnu ikke nok data til denne quiz (kræver importerede observationer).")
    return {"title": learning.AUTO_KINDS[kind], "questions": qs}


class AutoAnswerIn(BaseModel):
    token: str = Field(max_length=40)
    chosen: int


@router.post("/auto/answer")
async def auto_answer(body: AutoAnswerIn):
    res = await learning.auto_answer(body.token, body.chosen)
    if res is None:
        raise HTTPException(410, "Spørgsmålet er udløbet")
    return res


# ---------------------------------------------------------------- artsquiz (fotos fra galleriet)

async def _artsquiz(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not await learn_access.artsquiz(db, user):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("/arter/grupper")
async def arter_grupper(user: User = Depends(_artsquiz), db: AsyncSession = Depends(get_session)):
    """Forudindstillede artsgrupper (artnr) og antal egnede fotos pr. art."""
    return await species_quiz.groups(db)


class SpeciesQuizIn(BaseModel):
    arter: list[int] = Field(min_length=2, max_length=600)
    metode: str = Field(pattern="^(valg|tekst)$")


@router.post("/arter/quiz")
async def arter_quiz(body: SpeciesQuizIn, user: User = Depends(_artsquiz), db: AsyncSession = Depends(get_session)):
    try:
        qs = await species_quiz.build(db, body.arter, body.metode)
    except species_quiz.QuizError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"questions": qs}


class SpeciesAnswerIn(BaseModel):
    token: str = Field(max_length=40)
    artnr: int = Field(ge=0)  # 0 = opgivet


@router.post("/arter/svar")
async def arter_svar(body: SpeciesAnswerIn, user: User = Depends(_artsquiz), db: AsyncSession = Depends(get_session)):
    res = await species_quiz.answer(db, body.token, body.artnr)
    if res is None:
        raise HTTPException(410, "Spørgsmålet er udløbet")
    return res


# ---------------------------------------------------------------- lydquiz (xeno-canto og Macaulay Library)

async def _lydquiz(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> User:
    if not await learn_access.lydquiz(db, user):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


@router.get("/lyd/grupper")
async def lyd_grupper(user: User = Depends(_lydquiz), db: AsyncSession = Depends(get_session)):
    """Artsgrupperne (som i artsquizzen) og antal kendte optagelser pr. art. Arter uden tal er endnu ikke slået op hos xeno-canto."""
    return {"grupper": await species_quiz.preset_groups(db), "fotos": await sound_quiz.counts(db), "ukendt": True}


@router.post("/lyd/quiz")
async def lyd_quiz(body: SpeciesQuizIn, user: User = Depends(_lydquiz), db: AsyncSession = Depends(get_session)):
    try:
        qs = await sound_quiz.build(db, body.arter, body.metode)
    except species_quiz.QuizError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"questions": qs}


@router.get("/lyd/audio/{token}")
async def lyd_audio(token: str, user: User = Depends(_lydquiz), db: AsyncSession = Depends(get_session)):
    """Lyden til et spørgsmål. Adressen indeholder kun spørgsmålets nøgle – hverken art eller optagelsesnummer."""
    try:
        path = await sound_quiz.question_file(db, token)
    except sound_quiz.SoundError as exc:
        raise HTTPException(502, str(exc)) from exc
    if path is None:
        raise HTTPException(410, "Spørgsmålet er udløbet")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/lyd/svar")
async def lyd_svar(body: SpeciesAnswerIn, user: User = Depends(_lydquiz), db: AsyncSession = Depends(get_session)):
    res = await species_quiz.answer(db, body.token, body.artnr, "quiz:snd:")
    if res is None:
        raise HTTPException(410, "Spørgsmålet er udløbet")
    return res


# Macaulay Library: redaktører (modulet «laering») lægger optagelser ind, da ML ikke har et åbent API

class MlIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)
    artnr: int
    rtype: str | None = Field(None, max_length=80)
    recordist: str | None = Field(None, max_length=200)


@router.get("/lyd/ml")
async def lyd_ml(user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""SELECT r.id, r.ext_id, r.artnr, s.navn, r.latin, r.recordist, r.rtype, r.page_url, r.enabled
                                     FROM snd_recordings r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.source = 'ml' ORDER BY s.navn, r.id"""))).mappings().all()
    return [dict(r) for r in rows]


@router.post("/lyd/ml")
async def lyd_ml_add(body: MlIn, user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    try:
        return await sound_quiz.add_ml(db, body.ref, body.artnr, user.id, body.rtype, body.recordist)
    except sound_quiz.SoundError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/lyd/ml/{rid}")
async def lyd_ml_delete(rid: int, user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    n = (await db.execute(text("DELETE FROM snd_recordings WHERE id = :i AND source = 'ml'"), {"i": rid})).rowcount
    await db.commit()
    if not n:
        raise HTTPException(404)
    return {"ok": True}


class ResultIn(BaseModel):
    quiz_key: str = Field(max_length=80)
    score: int = Field(ge=0)
    total: int = Field(ge=1, le=100)


@router.post("/result")
async def result(body: ResultIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    db.add(QuizResult(user_id=user.id, **body.model_dump()))
    await db.commit()
    best = (await db.execute(select(func.max(QuizResult.score * 100 / QuizResult.total)).where(
        QuizResult.user_id == user.id, QuizResult.quiz_key == body.quiz_key))).scalar()
    return {"best_pct": best}


# ---------------------------------------------------------------- redigering


class CourseIn(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    summary: str = Field("", max_length=1000)
    intro: str = Field("", max_length=20000)
    topic: str = Field("id", pattern="^(id|kald|saeson|andet)$")
    level: str = Field("øvet", max_length=16)
    published: bool = False


def slugify(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:70] or uuid.uuid4().hex[:8]


@router.post("/courses")
async def create_course(body: CourseIn, user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    slug = slugify(body.title)
    if (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    c = Course(slug=slug, created_by=user.id, **body.model_dump())
    db.add(c)
    await db.commit()
    return course_out(c)


@router.put("/courses/{slug}")
async def update_course(slug: str, body: CourseIn, user: User = Depends(require_perm("laering")),
                        db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    await db.commit()
    return course_out(c)


@router.delete("/courses/{slug}")
async def delete_course(slug: str, user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    await db.delete(c)
    await db.commit()
    return {"ok": True}


class ChoiceIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    correct: bool = False


class QuestionIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    choices: list[ChoiceIn] = Field(min_length=2, max_length=8)
    explanation: str = Field("", max_length=4000)
    media_kind: str | None = Field(None, pattern="^(image|audio)$")
    media_url: str | None = Field(None, max_length=400)
    media_credit: str | None = Field(None, max_length=200)
    position: int = 0


def _validate(body: QuestionIn) -> None:
    if not any(c.correct for c in body.choices):
        raise HTTPException(400, "Mindst ét svar skal være korrekt")
    if body.media_url and not (body.media_url.startswith("/media/") or body.media_url.startswith("https://")):
        raise HTTPException(400, "Medie-URL skal være en upload eller https")


@router.post("/courses/{slug}/questions")
async def add_question(slug: str, body: QuestionIn, user: User = Depends(require_perm("laering")),
                       db: AsyncSession = Depends(get_session)):
    _validate(body)
    c = await _course(db, slug, user)
    q = Question(course_id=c.id, **{**body.model_dump(), "choices": [ch.model_dump() for ch in body.choices]})
    db.add(q)
    await db.commit()
    return {"id": q.id}


@router.put("/questions/{qid}")
async def update_question(qid: int, body: QuestionIn, user: User = Depends(require_perm("laering")),
                          db: AsyncSession = Depends(get_session)):
    _validate(body)
    q = await db.get(Question, qid)
    if q is None:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(q, k, [ch.model_dump() for ch in body.choices] if k == "choices" else v)
    await db.commit()
    return {"id": q.id}


@router.delete("/questions/{qid}")
async def delete_question(qid: int, user: User = Depends(require_perm("laering")), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(Question).where(Question.id == qid))
    await db.commit()
    return {"ok": True}


@router.post("/media")
async def upload(file: UploadFile = File(...), user: User = Depends(require_perm("laering"))):
    ext = MEDIA_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Kun billeder (jpg/png/webp/gif) og lyd (mp3/ogg/wav/m4a/webm)")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Filen er større end 15 MB")
    if not ext.endswith((".mp3", ".ogg", ".wav", ".m4a", ".webm")):
        try:
            data = imagemeta.strip(data)  # GPS-position og andre metadata fjernes, før filen gemmes
        except ValueError as exc:
            raise HTTPException(400, "Billedet kunne ikke læses") from exc
    root = Path(get_settings().media_dir)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (root / name).write_bytes(data)
    return {"url": f"/media/{name}", "kind": "audio" if ext in (".mp3", ".ogg", ".wav", ".m4a", ".webm") else "image"}


@router.get("/me")
async def my_learning(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    results = (await db.execute(select(QuizResult).where(QuizResult.user_id == user.id)
                                .order_by(QuizResult.created_at.desc()).limit(30))).scalars().all()
    return {"progress": await learning.course_progress(db, user.id),
            "results": [{"quiz_key": r.quiz_key, "score": r.score, "total": r.total, "at": r.created_at} for r in results]}
