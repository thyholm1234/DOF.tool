"""Læring 2.0: afspilning – katalog, lektioner, øvelser og svar (docs/laering-spec.md §4–5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import kv
from portal.db import get_session
from portal.models import Course, Unit, User
from portal.services import learn_access, learn_play, sound_quiz
from portal.services.auth import can

router = APIRouter(prefix="/api/learn", tags=["learn"])


async def _portal(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)) -> User | None:
    if not await learn_access.portal(db, user):
        raise HTTPException(404, "Funktionen er ikke slået til")
    return user


async def _rate(user: User | None, key: str, limit: int) -> None:
    from time import time

    who = user.id if user else "anon"
    k = f"lr:rate:{key}:{who}:{int(time() // 60)}"
    n = await kv().incr(k)
    if n == 1:
        await kv().expire(k, 90)
    if n > limit:
        raise HTTPException(429, "For mange forespørgsler – vent et øjeblik")


@router.get("/portal")
async def portal_info(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_session)):
    """Hvilke dele af Læring brugeren kan se (funktionsflag og rettigheder)."""
    on = await learn_access.portal(db, user)
    return {"portal": on, "data": on and await learn_access.data(db, user), "byg": on and await learn_access.byg(db, user) and learn_access.can_build(user),
            "forloeb": on and await learn_access.forloeb(db, user), "felt": on and await learn_access.felt(db, user),
            "artsquiz": bool(user) and await learn_access.artsquiz(db, user), "lydquiz": bool(user) and await learn_access.lydquiz(db, user),
            "editor": can(user, "laering"), "underviser": can(user, "laering_undervis"), "skills": learn_play.SKILLS, "metode": learn_play.METODE}


async def _visible_courses(db: AsyncSession, user: User | None) -> list[Course]:
    rows = (await db.execute(select(Course).order_by(Course.number.nulls_last(), Course.title))).scalars().all()
    return [c for c in rows if (c.published or learn_play.can_edit_course(user, c)) and (await learn_play.can_view(db, user, c))]


def _card(c: Course, prog: dict) -> dict:
    st = c.published_snapshot or {}
    n_units = len(st.get("units", [])) if st else None
    n_acts = sum(len(u["acts"]) for u in st.get("units", [])) if st else None
    return {**learn_play.course_public(c), "n_units": n_units, "n_acts": n_acts, "progress": prog.get(c.id)}


@router.get("/katalog")
async def katalog(user: User | None = Depends(_portal), db: AsyncSession = Depends(get_session)):
    courses = await _visible_courses(db, user)
    prog: dict[int, dict] = {}
    if user:
        rows = (await db.execute(text("""SELECT u.course_id, count(*) AS total, count(p.completed_at) AS done FROM lr_units u
            LEFT JOIN lr_unit_progress p ON p.unit_id = u.id AND p.user_id = :u GROUP BY u.course_id"""), {"u": user.id})).mappings().all()
        prog = {r["course_id"]: {"done": r["done"], "total": r["total"]} for r in rows}
        due = (await db.execute(text("SELECT count(*) FROM question_progress WHERE user_id = :u AND due_at <= now()"), {"u": user.id})).scalar() or 0
        due += (await db.execute(text("SELECT count(*) FROM lr_species_mastery WHERE user_id = :u AND due_at <= now()"), {"u": user.id})).scalar() or 0
    else:
        due = 0
    cards = [_card(c, prog) for c in courses]
    out = {"kurser": [c for c in cards if c["kind"] == "kursus"], "oevelser": [c for c in cards if c["kind"] != "kursus"], "forfald": due,
           "auto": [{"kind": k, "title": v} for k, v in __import__("portal.services.learning", fromlist=["AUTO_KINDS"]).AUTO_KINDS.items()]}
    if await learn_access.forloeb(db, user):
        from portal.services import learn_paths

        out["forloeb"] = await learn_paths.catalog(db, user)
    return out


async def _course(db: AsyncSession, slug: str, user: User | None) -> Course:
    c = (await db.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
    if c is None or not await learn_play.can_view(db, user, c):
        raise HTTPException(404, "Kurset findes ikke")
    return c


@router.get("/courses/{slug}/units")
async def course_units(slug: str, preview: bool = False, user: User | None = Depends(_portal), db: AsyncSession = Depends(get_session)):
    c = await _course(db, slug, user)
    preview = preview and learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id))
    st = await learn_play.struct(db, c, preview)
    ids = [u["id"] for u in st["units"] if u["id"]]
    states = learn_play.unit_states([u for u in st["units"] if u["id"]], await learn_play.unit_progress_map(db, user, ids))
    units = []
    for u, s in zip([u for u in st["units"] if u["id"]], states):
        units.append({"id": u["id"], "title": u["title"], "html": await learn_play.render_md(db, u["lesson_md"]), "blocks": await learn_play.resolve_blocks(db, u["blocks"]),
                      "n_acts": len(u["acts"]), "est_minutes": u["est_minutes"], "min_score_pct": u["min_score_pct"], **s})
    return {"course": st["course"], "units": units, "preview": preview, "anonymous": user is None}


@router.get("/units/{unit_id}/session")
async def unit_session(unit_id: int, preview: bool = False, user: User | None = Depends(_portal), db: AsyncSession = Depends(get_session)):
    await _rate(user, "session", 60)
    u = await db.get(Unit, unit_id)
    c = await db.get(Course, u.course_id) if u else None
    if u is None or c is None or not await learn_play.can_view(db, user, c):
        raise HTTPException(404, "Lektionen findes ikke")
    preview = preview and learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id))
    st = await learn_play.struct(db, c, preview)
    unit = next((x for x in st["units"] if x["id"] == unit_id), None)
    if unit is None:
        raise HTTPException(404, "Lektionen findes ikke (endnu) i den publicerede udgave")
    units = [x for x in st["units"] if x["id"]]
    states = learn_play.unit_states(units, await learn_play.unit_progress_map(db, user, [x["id"] for x in units]))
    if states[units.index(unit)]["locked"] and not preview:
        raise HTTPException(403, "Lektionen er låst – klar den forrige først")
    return {"course": st["course"], "unit": {"id": unit_id, "title": unit["title"]}, "questions": await learn_play.unit_questions(db, user, st, unit)}


@router.get("/ovelser/{slug}/session")
async def drill_session(slug: str, n: int = Query(10, ge=1, le=40), mode: str = Query("smart", pattern="^(smart|random|tid)$"), preview: bool = False,
                        user: User | None = Depends(_portal), db: AsyncSession = Depends(get_session)):
    await _rate(user, "session", 60)
    c = await _course(db, slug, user)
    preview = preview and learn_play.can_edit_course(user, c, await learn_play.editor_ids(db, c.id))
    st = await learn_play.struct(db, c, preview)
    return {"course": st["course"], "questions": await learn_play.drill_questions(db, user, st, n, mode), "tid_s": 15 if mode == "tid" else None}


@router.get("/auto-session/{kind}")
async def auto_session(kind: str, n: int = Query(10, ge=1, le=30), afdeling: int | None = None, user: User | None = Depends(_portal),
                       db: AsyncSession = Depends(get_session)):
    from portal.services import learning

    if kind not in learning.AUTO_KINDS or not await learn_access.data(db, user):
        raise HTTPException(404)
    qs = await learn_play.auto_session(db, kind, n, afdeling)
    if not qs:
        raise HTTPException(409, "Der er endnu ikke nok data til denne quiz (kræver importerede observationer).")
    return {"title": learning.AUTO_KINDS[kind], "questions": qs}


class SvarIn(BaseModel):
    id: int | str
    chosen: list[int] = Field(default_factory=list, max_length=10)
    text: str | None = Field(None, max_length=300)
    order: list[int] = Field(default_factory=list, max_length=30)
    point: dict | None = None
    confidence: int | None = Field(None, ge=0, le=2)
    ms: int | None = Field(None, ge=0, le=3_600_000)
    niveau: str | None = Field(None, pattern="^(ubestemt)$")
    preview: bool = False


@router.post("/svar")
async def svar(body: SvarIn, user: User | None = Depends(_portal), db: AsyncSession = Depends(get_session)):
    await _rate(user, "svar", 240)
    try:
        return await learn_play.answer(db, user, body.model_dump())
    except learn_play.AnswerError as exc:
        raise HTTPException(exc.status, exc.msg) from exc


class DoneIn(BaseModel):
    score: int = Field(ge=0)
    total: int = Field(ge=0, le=200)


@router.post("/units/{unit_id}/faerdig")
async def unit_done(unit_id: int, body: DoneIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not await learn_access.portal(db, user):
        raise HTTPException(404)
    u = await db.get(Unit, unit_id)
    c = await db.get(Course, u.course_id) if u else None
    if u is None or c is None or not await learn_play.can_view(db, user, c):
        raise HTTPException(404)
    res = await learn_play.finish_unit(db, user, unit_id, body.score, body.total)
    from portal.services import learn_paths

    await learn_paths.after_progress(db, user, c.id)
    return res


@router.get("/lyd/fil/{rid}")
async def sound_file(rid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Optagelse, der vises åbent i en lektion (med art og kreditering)."""
    if not await learn_access.lydquiz(db, user):
        raise HTTPException(404, "Funktionen er ikke slået til")
    r = (await db.execute(text("SELECT source, ext_id, file_url FROM snd_recordings WHERE id = :i AND enabled"), {"i": rid})).mappings().first()
    if r is None:
        raise HTTPException(404)
    try:
        path = await sound_quiz.audio_file(r["source"], r["ext_id"], r["file_url"])
    except sound_quiz.SoundError as exc:
        raise HTTPException(502, str(exc)) from exc
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- egne træninger, repetition og forbered en tur (spec §6–7)

class TrainIn(BaseModel):
    kilde: str | None = Field(None, max_length=12)
    gruppe: str | None = Field(None, max_length=40)
    arter: list[int] = Field(default_factory=list, max_length=600)
    loknr: int | None = None
    afdeling_id: int | None = None
    kursus: str | None = Field(None, max_length=80)
    m: str = Field("foto", pattern="^(foto|lyd|begge)$")
    metode: str = Field("valg", pattern="^(valg|tekst)$")
    rtype: str = Field("begge", pattern="^(sang|kald|begge)$")
    n: int = Field(20, ge=1, le=40)


async def _mods(db: AsyncSession, user: User) -> tuple[bool, bool]:
    return await learn_access.artsquiz(db, user), await learn_access.lydquiz(db, user)


@router.post("/traen")
async def traen(body: TrainIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.services import learn_train, species_quiz as sq

    if not await learn_access.portal(db, user):
        raise HTTPException(404)
    await _rate(user, "traen", 30)
    foto, lyd = await _mods(db, user)
    try:
        return {"questions": await learn_train.build(db, user, body.model_dump(), foto, lyd)}
    except (learn_train.TrainError, sq.QuizError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/traen/muligheder")
async def traen_muligheder(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.services import species_quiz as sq

    if not await learn_access.portal(db, user):
        raise HTTPException(404)
    foto, lyd = await _mods(db, user)
    return {"foto": foto, "lyd": lyd, "grupper": [{"key": g["key"], "navn": g["navn"], "beskrivelse": g["beskrivelse"], "n": len(g["arter"])} for g in await sq.preset_groups(db)],
            "ml": can(user, "laering")}


@router.get("/traen/arter")
async def traen_arter(q: str = Query("", max_length=60), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Artssøgning til artsvælgeren."""
    rows = (await db.execute(text("""SELECT artnr, navn, latin FROM species WHERE art_type = 'art' AND list_artnr = artnr AND NOT excluded
        AND (navn ILIKE :q OR latin ILIKE :q) ORDER BY sortering LIMIT 15"""), {"q": f"%{q.strip()}%"})).mappings().all()
    return [dict(r) for r in rows]


class SaveIn(TrainIn):
    title: str = Field(max_length=160)


@router.get("/traeninger")
async def traeninger(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.models import LrTraining

    rows = (await db.execute(select(LrTraining).where(LrTraining.user_id == user.id).order_by(LrTraining.created_at.desc()))).scalars().all()
    return [{"id": t.id, "title": t.title, "config": t.config, "share_token": t.share_token} for t in rows]


@router.post("/traeninger")
async def traening_gem(body: SaveIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.services import learn_train

    t = await learn_train.save_training(db, user, body.title, body.model_dump(exclude={"title"}))
    return {"id": t.id, "title": t.title, "config": t.config}


@router.delete("/traeninger/{tid}")
async def traening_slet(tid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.models import LrTraining

    t = await db.get(LrTraining, tid)
    if t is None or t.user_id != user.id:
        raise HTTPException(404)
    await db.delete(t)
    await db.commit()
    return {"ok": True}


@router.post("/traeninger/{tid}/del")
async def traening_del(tid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.models import LrTraining
    from portal.services import learn_train

    t = await db.get(LrTraining, tid)
    if t is None or t.user_id != user.id:
        raise HTTPException(404)
    return {"token": await learn_train.share(db, t)}


@router.get("/traeninger/delt/{token}")
async def traening_delt(token: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.models import LrTraining

    t = (await db.execute(select(LrTraining).where(LrTraining.share_token == token))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Træningen findes ikke (mere)")
    return {"title": t.title, "config": t.config}


@router.get("/repetition")
async def repetition(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from portal.services import learn_train

    if not await learn_access.portal(db, user):
        raise HTTPException(404)
    foto, lyd = await _mods(db, user)
    return {"antal": await learn_train.due_counts(db, user), "questions": await learn_train.repetition(db, user, foto, lyd)}


@router.get("/forbered")
async def forbered(loknr: int | None = None, dato: str | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    from datetime import date

    from portal.services import learn_train

    if not (await learn_access.portal(db, user) and await learn_access.data(db, user)):
        raise HTTPException(404)
    try:
        d = date.fromisoformat(dato) if dato else date.today()
    except ValueError as exc:
        raise HTTPException(400, "Ugyldig dato") from exc
    try:
        return await learn_train.prepare(db, loknr, d)
    except learn_train.TrainError as exc:
        raise HTTPException(404, str(exc)) from exc
