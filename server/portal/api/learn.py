"""E-læring: kurser, spørgsmål, quizzer og redigering (editor/admin)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_role, require_user
from portal.config import get_settings
from portal.db import get_session
from portal.models import Course, Question, QuizResult, User
from portal.services import learning
from portal.services.auth import role_at_least

router = APIRouter(prefix="/api/learn", tags=["learn"])

MEDIA_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/webm": ".webm"}


def course_out(c: Course, n_questions: int = 0, progress: dict | None = None) -> dict:
    return {"id": c.id, "slug": c.slug, "title": c.title, "summary": c.summary, "topic": c.topic, "level": c.level,
            "intro": c.intro, "published": c.published, "n_questions": n_questions, "progress": progress}


@router.get("/courses")
async def courses(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    editor = role_at_least(user, "editor")
    q = select(Course, func.count(Question.id)).outerjoin(Question).group_by(Course.id).order_by(Course.topic, Course.title)
    if not editor:
        q = q.where(Course.published)
    rows = (await db.execute(q)).all()
    prog = await learning.course_progress(db, user.id) if user else {}
    return {"courses": [course_out(c, n, prog.get(c.id)) for c, n in rows],
            "auto": [{"kind": k, "title": v} for k, v in learning.AUTO_KINDS.items()]}


async def _course(db: AsyncSession, slug: str, user: User | None) -> Course:
    c = (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
    if c is None or (not c.published and not role_at_least(user, "editor")):
        raise HTTPException(404, "Kurset findes ikke")
    return c


@router.get("/courses/{slug}")
async def course(slug: str, user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    qs = (await db.execute(select(Question).where(Question.course_id == c.id).order_by(Question.position, Question.id))).scalars().all()
    out = course_out(c, len(qs))
    if role_at_least(user, "editor"):
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
async def create_course(body: CourseIn, user: User = Depends(require_role("editor")), db: AsyncSession = Depends(get_session)):
    slug = slugify(body.title)
    if (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    c = Course(slug=slug, created_by=user.id, **body.model_dump())
    db.add(c)
    await db.commit()
    return course_out(c)


@router.put("/courses/{slug}")
async def update_course(slug: str, body: CourseIn, user: User = Depends(require_role("editor")),
                        db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    await db.commit()
    return course_out(c)


@router.delete("/courses/{slug}")
async def delete_course(slug: str, user: User = Depends(require_role("editor")), db: AsyncSession = Depends(get_session)):
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
async def add_question(slug: str, body: QuestionIn, user: User = Depends(require_role("editor")),
                       db: AsyncSession = Depends(get_session)):
    _validate(body)
    c = await _course(db, slug, user)
    q = Question(course_id=c.id, **{**body.model_dump(), "choices": [ch.model_dump() for ch in body.choices]})
    db.add(q)
    await db.commit()
    return {"id": q.id}


@router.put("/questions/{qid}")
async def update_question(qid: int, body: QuestionIn, user: User = Depends(require_role("editor")),
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
async def delete_question(qid: int, user: User = Depends(require_role("editor")), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(Question).where(Question.id == qid))
    await db.commit()
    return {"ok": True}


@router.post("/media")
async def upload(file: UploadFile = File(...), user: User = Depends(require_role("editor"))):
    ext = MEDIA_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(400, "Kun billeder (jpg/png/webp/gif) og lyd (mp3/ogg/wav/m4a/webm)")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "Filen er større end 15 MB")
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
