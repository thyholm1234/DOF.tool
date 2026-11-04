"""Læring: afspilning – aktiviteter gøres klar til klienten (uden facit) og bedømmes (docs/laering-spec.md §5).

En *aktivitet* er en ``Question``-række (eller dens låste kopi i kursets ``published_snapshot``). Statiske typer har facit i rækken;
generatorer (foto/lyd/DOFbasen) udfoldes til konkrete spørgsmål, hvis facit lægges i Valkey under et token. Klienten ser aldrig facit
før svaret er afgivet. Alle spørgsmål har et ``id``: et tal for statiske, ellers ``lr:``/``art:``/``snd:``/``auto:`` + token.
"""

from __future__ import annotations

import difflib
import json
import random
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.models import Course, LrAnswer, LrSpeciesMastery, Question, QuestionProgress, Unit, User
from portal.services import learn_access, learn_gen, learning, sound_quiz, species_quiz
from portal.services.auth import can

STATIC = {"valg", "tekst", "raekkefoelge", "silhuet", "refleksion", "billedpunkt"}
TYPES = STATIC | learn_gen.GENERATORS
SPECIES_BOX_DAYS = [0, 1, 3, 7, 16, 35]
TOKEN_TTL = 7200
AUTO_KIND = {"obs_saeson": "traek", "obs_sjaelden": "sjaelden", "obs_status": "status"}
SKILLS = ["Bemærk", "Lyt", "Genkald", "Sammenlign", "Afprøv", "Vurdér", "Opdatér", "Forbered", "Fasthold"]


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.!?,;:]", " ", str(s or "").strip().lower())).strip()


def text_matches(answer: str, accept: list[str], fuzzy: bool = False) -> bool:
    a = norm(answer)
    if not a:
        return False
    for x in accept:
        n = norm(x)
        if a == n or (fuzzy and len(n) >= 5 and difflib.SequenceMatcher(None, a, n).ratio() >= 0.86):
            return True
    return False


# ---------------------------------------------------------------- aktiviteter som dicts

def act_from_row(q: Question) -> dict:
    return {"id": q.id, "type": q.type or "valg", "prompt": q.prompt, "choices": q.choices or [], "config": q.config or {},
            "accept": list(q.accept or []), "explanation": q.explanation or "", "media_kind": q.media_kind, "media_url": q.media_url,
            "media_credit": q.media_credit, "media_photo_id": q.media_photo_id, "media_rec_id": q.media_rec_id,
            "skills": list(q.skills or []), "ask_confidence": q.ask_confidence is not False, "position": q.position, "unit_id": q.unit_id}


def can_edit_course(user: User | None, c: Course, editor_ids: set[int] | None = None) -> bool:
    if user is None:
        return False
    if can(user, "laering"):
        return True
    return can(user, "laering_undervis") and (c.created_by == user.id or user.id in (editor_ids or set()))


async def editor_ids(db: AsyncSession, course_id: int) -> set[int]:
    return {r[0] for r in (await db.execute(text("SELECT user_id FROM lr_course_editors WHERE course_id = :c"), {"c": course_id})).all()}


async def can_view(db: AsyncSession, user: User | None, c: Course) -> bool:
    if can_edit_course(user, c, await editor_ids(db, c.id) if user else None):
        return True
    if not c.published:
        return False
    if c.visibility == "alle":
        return True
    if user is None:
        return False
    if c.visibility == "login":
        return True
    return bool((await db.execute(text("""SELECT 1 FROM lr_cohort_members m JOIN lr_cohorts h ON h.id = m.cohort_id AND NOT h.archived
        JOIN lr_path_items i ON i.path_id = h.path_id WHERE m.user_id = :u AND i.course_id = :c LIMIT 1"""), {"u": user.id, "c": c.id})).first())


# ---------------------------------------------------------------- kursusstruktur (levende eller låst kopi)

def course_public(c: Course) -> dict:
    return {"id": c.id, "slug": c.slug, "title": c.title, "summary": c.summary, "intro": c.intro, "topic": c.topic, "level": c.level,
            "kind": c.kind, "number": c.number, "stage": c.stage, "category": c.category, "card": c.card, "audience": c.audience,
            "learn": c.learn or [], "skills": list(c.skills or []), "hours": c.hours, "hue": c.hue, "sil": c.sil, "featured": c.featured,
            "published": c.published, "visibility": c.visibility, "version": c.version, "cover_media_id": c.cover_media_id}


async def live_struct(db: AsyncSession, c: Course) -> dict:
    units = (await db.execute(select(Unit).where(Unit.course_id == c.id).order_by(Unit.position, Unit.id))).scalars().all()
    qs = (await db.execute(select(Question).where(Question.course_id == c.id).order_by(Question.position, Question.id))).scalars().all()
    by_unit: dict[int | None, list] = {}
    for q in qs:
        by_unit.setdefault(q.unit_id, []).append(act_from_row(q))
    out = [{"id": u.id, "title": u.title, "lesson_md": u.lesson_md, "blocks": u.blocks or [], "min_score_pct": u.min_score_pct,
            "est_minutes": u.est_minutes, "acts": by_unit.get(u.id, [])} for u in units]
    loose = by_unit.get(None, [])
    if loose:  # aktiviteter uden lektion (øvelser) samles i en skjult lektion
        out.append({"id": None, "title": "", "lesson_md": "", "blocks": [], "min_score_pct": None, "est_minutes": None, "acts": loose})
    return {"course": course_public(c), "units": out}


async def struct(db: AsyncSession, c: Course, preview: bool = False) -> dict:
    if not preview and c.published_snapshot:
        return c.published_snapshot
    return await live_struct(db, c)


def find_act(st: dict, act_id: int) -> dict | None:
    for u in st["units"]:
        for a in u["acts"]:
            if a["id"] == act_id:
                return a
    return None


# ---------------------------------------------------------------- medier

async def photo_media(db: AsyncSession, photo_id: int) -> dict | None:
    r = (await db.execute(text("""SELECT p.id, p.rettighed, m.medium_key, m.storage_key, ph.navn AS fotograf FROM gal_photos p
        JOIN content_media m ON m.id = p.media_id LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
        WHERE p.id = :i AND p.status = 'publiceret'"""), {"i": photo_id})).mappings().first()
    if not r:
        return None
    return {"kind": "image", "value": "/media/art/" + (r["medium_key"] or r["storage_key"]), "credit": r["fotograf"] or "", "photo_id": r["id"]}


async def sound_media(db: AsyncSession, rec_id: int, hide: bool) -> dict | None:
    r = (await db.execute(text("""SELECT r.id, r.artnr, r.source, r.ext_id, r.recordist, r.license, r.page_url, r.rtype, s.navn, s.latin, s.list_artnr
        FROM snd_recordings r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.id = :i AND r.enabled"""), {"i": rec_id})).mappings().first()
    if not r:
        return None
    credit = f"{r['recordist'] or 'ukendt'} ({r['source'].upper()} {r['ext_id']}{', ' + r['license'] if r['license'] else ''})"
    if hide:  # lyd som spørgsmål: adressen afslører hverken art eller optagelse
        tok = await species_quiz.store_token({"artnr": r["artnr"], "liste": r["list_artnr"] or r["artnr"], "navn": r["navn"], "latin": r["latin"],
                                              "extra": {"url": r["page_url"], "recordist": r["recordist"], "license": r["license"], "rtype": r["rtype"]}, "rid": r["id"]}, "quiz:snd:")
        return {"kind": "audio", "value": f"/api/learn/lyd/audio/{tok}", "credit": ""}
    return {"kind": "audio", "value": f"/api/learn/lyd/fil/{r['id']}", "credit": credit, "artnr": r["artnr"], "navn": r["navn"], "url": r["page_url"]}


# Automatiske medier: startindhold (fx Phylloscopus-kurset) kan ikke kende en installations foto- og optagelses-id'er, så det peger på
# medier med en reference, der løses ved visning. Et fast valgt id (photo_id/rec_id, media_photo_id/media_rec_id) har altid forrang,
# så redaktøren kan skifte billedet eller lyden i byggeren.

async def photo_by_ref(db: AsyncSession, ref: dict | None) -> int | None:
    """``{"nf": <netfugl-nr>}`` = et bestemt foto fra netfugl-arkivet; ``{"artnr"|"latin": …, "n": k}`` = artens k'te mest likede
    publicerede enkeltfugl-foto, evt. kun med en bestemt ``alder`` (fotobibliotekets aldertekst). Begge kan angives – ``nf`` prøves først.

    Der vælges **kun** fotos, DOF må bruge fast (rettighed 1 eller fotografens DOF-samtykke, spec §13.2). Har arten intet sådant foto,
    returneres ``None`` – blokken vises uden foto, og byggeren advarer, så redaktøren kan vælge et selv."""
    if not isinstance(ref, dict):
        return None
    nf = int(ref["nf"]) if str(ref.get("nf") or "").isdigit() else None
    artnr = int(ref["artnr"]) if str(ref.get("artnr") or "").isdigit() else None
    latin = str(ref.get("latin") or "").strip() or None
    base = """SELECT p.id FROM gal_photos p LEFT JOIN gal_photographers ph ON ph.id = p.photographer_id
        WHERE p.status = 'publiceret' AND (coalesce(p.rettighed, ph.standard_rettighed) = 1 OR coalesce(ph.dof_samtykke, false))"""
    if nf and (pid := (await db.execute(text(f"{base} AND p.netfugl_id = :nf"), {"nf": nf})).scalar()):
        return pid
    if artnr is None and latin is None:
        return None
    alder = str(ref.get("alder") or "").strip() or None  # fx «1. kalenderår» – vigtigt for måger: et erstatningsfoto skal have samme alder
    return (await db.execute(text(f"""{base} AND NOT ('tvivl_art' = ANY(p.tags))
          AND EXISTS (SELECT 1 FROM gal_birds b WHERE b.photo_id = p.id AND b.pos = 0
                      AND (b.artnr = :a OR (CAST(:a AS int) IS NULL AND lower(b.latin) = lower(:l)))
                      AND (CAST(:alder AS text) IS NULL OR EXISTS (SELECT 1 FROM gal_codes gc WHERE gc.kind = 'alder' AND gc.code = b.alder
                                                                  AND lower(gc.text) = lower(:alder))))
          AND NOT EXISTS (SELECT 1 FROM gal_birds b2 WHERE b2.photo_id = p.id AND b2.pos > 0)
        ORDER BY p.likes DESC, p.views DESC, p.id LIMIT 1 OFFSET :o"""),
        {"a": artnr, "l": latin, "alder": alder, "o": max(0, min(int(ref.get("n") or 0), 50))})).scalar()


async def rec_by_ref(db: AsyncSession, ref: dict | None) -> int | None:
    """``{"xc": "<XC-nr>"}`` = en bestemt xeno-canto-optagelse; ``{"artnr"|"latin": …, "type": "sang"|"kald", "n": k}`` = artens k'te
    optagelse af typen. Mangler optagelsen, hentes den hos xeno-canto med portalens nøgle (husket, så det kun sker én gang)."""
    if not isinstance(ref, dict):
        return None
    artnr = int(ref["artnr"]) if str(ref.get("artnr") or "").isdigit() else None
    latin = str(ref.get("latin") or "").strip() or None
    xc = str(ref.get("xc") or "").strip()
    if xc.isdigit():
        rid = (await db.execute(text("SELECT id FROM snd_recordings WHERE source = 'xc' AND ext_id = :x AND enabled"), {"x": xc})).scalar()
        if rid is None:
            try:
                rid = await sound_quiz.ensure_xc(db, xc, artnr=artnr, latin=latin)
            except sound_quiz.SoundError:
                rid = None
        if rid:
            return rid
    if artnr is None and latin is None:
        return None
    kind = "sang" if ref.get("type") == "sang" else "kald" if ref.get("type") == "kald" else "begge"
    sql = text(f"""SELECT r.id FROM snd_recordings r WHERE r.enabled AND {sound_quiz.KIND_SQL[kind]}
        AND (CASE WHEN CAST(:l AS text) IS NOT NULL THEN lower(r.latin) = lower(:l) ELSE r.artnr = :a END)
        ORDER BY r.quality NULLS LAST, r.id LIMIT 1 OFFSET :o""")
    args = {"a": artnr, "l": latin, "o": max(0, min(int(ref.get("n") or 0), 20))}
    rid = (await db.execute(sql, args)).scalar()
    if rid is None and latin:
        try:
            await sound_quiz.ensure_latin(db, latin, "song" if kind == "sang" else "call", artnr=artnr)
        except sound_quiz.SoundError:
            return None
        rid = (await db.execute(sql, {**args, "o": 0})).scalar()
    return rid


async def act_media(db: AsyncSession, a: dict, hue: str) -> dict | None:
    t = a["type"]
    cfg = a.get("config") or {}
    if a.get("media_photo_id"):
        return await photo_media(db, a["media_photo_id"])
    if a.get("media_rec_id"):
        return await sound_media(db, a["media_rec_id"], hide=True)
    if cfg.get("foto_ref") and not a.get("media_url") and (pid := await photo_by_ref(db, cfg["foto_ref"])):
        return await photo_media(db, pid)
    if cfg.get("lyd_ref") and not a.get("media_url") and (rid := await rec_by_ref(db, cfg["lyd_ref"])):
        return await sound_media(db, rid, hide=True)
    if t == "silhuet":
        return {"kind": "silhouette", "value": (a.get("config") or {}).get("sil", "spurvefugl"), "hue": (a.get("config") or {}).get("hue", hue), "credit": ""}
    if a.get("media_url"):
        return {"kind": a.get("media_kind") or "image", "value": a["media_url"], "credit": a.get("media_credit") or ""}
    return None


# ---------------------------------------------------------------- udfoldning til spørgsmål

def _base(a: dict, hue_skills: list[str]) -> dict:
    return {"type": a["type"], "ask_confidence": a.get("ask_confidence", True), "skills": a.get("skills") or hue_skills, "act": a["id"]}


async def public_static(db: AsyncSession, a: dict, rng: random.Random, hue: str, course_skills: list[str]) -> dict:
    t = a["type"]
    q = {"id": a["id"], **_base(a, course_skills), "prompt": a["prompt"], "media": await act_media(db, a, hue)}
    if t in ("valg", "silhuet"):
        ch = [{"id": i, "text": c["text"]} for i, c in enumerate(a["choices"])]
        rng.shuffle(ch)
        q["choices"] = ch
        q["multi"] = sum(1 for c in a["choices"] if c.get("correct")) > 1
    elif t == "raekkefoelge":
        items = [{"id": i, "text": x} for i, x in enumerate(a["config"].get("items", []))]
        rng.shuffle(items)
        q["items"] = items
    elif t == "billedpunkt":
        q["media"] = q["media"] or None
    elif t == "refleksion":
        q["min_chars"] = int(a["config"].get("min_chars") or 0)
        q["ask_confidence"] = False
    elif t == "tekst":
        pass
    return q


async def _store(data: dict) -> str:
    tok = secrets.token_urlsafe(9)
    await kv().set("quiz:lr:" + tok, json.dumps(data, ensure_ascii=False), ex=TOKEN_TTL)
    return tok


def _locked(a: dict, why: str) -> dict:
    return {"id": f"lock:{a['id']}", "type": a["type"], "locked": why, "prompt": a.get("prompt") or ""}


async def species_set(db: AsyncSession, cfg: dict) -> list[int]:
    arter = [int(x) for x in cfg.get("arter") or []]
    if cfg.get("gruppe"):
        for g in await species_quiz.preset_groups(db):
            if g["key"] == cfg["gruppe"]:
                arter += g["arter"]
    return sorted(set(arter))


async def resolve_text(db: AsyncSession, answer: str) -> int:
    n = norm(answer)
    if not n:
        return -1
    r = (await db.execute(text("SELECT artnr FROM species WHERE (lower(navn) = :n OR lower(latin) = :n) AND NOT excluded ORDER BY (list_artnr = artnr) DESC LIMIT 1"), {"n": n})).scalar()
    if r is not None:
        return r
    names = (await db.execute(text("SELECT artnr, navn FROM species WHERE art_type = 'art' AND list_artnr = artnr AND NOT excluded"))).all()
    best = max(names, key=lambda x: difflib.SequenceMatcher(None, n, norm(x[1])).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, n, norm(best[1])).ratio() >= 0.88:
        return best[0]
    return -1


async def expand_generator(db: AsyncSession, user: User | None, a: dict, rng: random.Random, course_skills: list[str]) -> list[dict]:
    t, cfg = a["type"], a.get("config") or {}
    if user is None:
        return [_locked(a, "Log ind for at få denne aktivitet")]
    if t in learn_gen.NEEDS_PHOTOS and not await learn_access.artsquiz(db, user):
        return [_locked(a, "Fotoquizzen er ikke slået til")]
    if t in learn_gen.NEEDS_SOUND and not await learn_access.lydquiz(db, user):
        return [_locked(a, "Lydquizzen er ikke slået til")]
    if t in learn_gen.NEEDS_DATA and not await learn_access.data(db, user):
        return [_locked(a, "DOFbasen-data er ikke slået til")]
    n = max(1, min(20, int(cfg.get("antal") or 1)))
    base = _base(a, course_skills)
    try:
        if t in ("foto_art", "lyd_art"):
            arter = await species_set(db, cfg)
            metode = cfg.get("metode", "valg")
            if t == "foto_art":
                qs, prefix, kind = await species_quiz.build(db, arter, metode, n), "art:", "image"
            else:
                qs, prefix, kind = await sound_quiz.build(db, arter, metode, cfg.get("rtype", "begge"), n), "snd:", "audio"
            return [{"id": prefix + q["id"], **base, "prompt": a["prompt"] or ("Hvilken art er det?" if t == "foto_art" else "Hvilken art synger her?"),
                     "media": {"kind": kind, "value": q["media_url"], "credit": q.get("media_credit") or ""}, "metode": metode,
                     "choices": q.get("choices")} for q in qs]
        if t == "foto_sammenlign":
            par = cfg.get("par") or []
            if len(par) != 2:
                raise learn_gen.GenError("Vælg to arter")
            out = []
            for _ in range(n):
                d = await learn_gen.photo_pair(db, int(par[0]), int(par[1]), rng)
                tok = await _store({"correct": d["correct"], "explanation": d["explanation"], "reveal": d["reveal"], "artnr": d["artnr"], "options": d["options"], "mod": "foto"})
                out.append({"id": "lr:" + tok, **base, "prompt": d["prompt"], "media": d["media"], "choices": [{"id": i, "text": x} for i, x in enumerate(d["options"])]})
            return out
        if t in ("obs_odds", "obs_habitat"):
            out = []
            for _ in range(n):
                d = await learn_gen.generate_obs(t, cfg, rng)
                opts = list(d["options"])
                rng.shuffle(opts)
                tok = await _store({"correct": d["correct"], "explanation": d["explanation"], "options": opts, "artnr": d.get("artnr"), "mod": "data", "map": d.get("map")})
                out.append({"id": "lr:" + tok, **base, "prompt": d["prompt"], "map": d.get("map"),
                            "choices": [{"id": i, "text": x} for i, x in enumerate(opts)], "multi": False})
            return out
        if t in AUTO_KIND:
            qs = await learning.auto_quiz(db, AUTO_KIND[t], n, cfg.get("afdeling_id"))
            if not qs:
                raise learn_gen.GenError("Der er endnu ikke nok data til denne aktivitet")
            return [{"id": "auto:" + q["id"], **base, "prompt": q["prompt"], "choices": q["choices"], "multi": False} for q in qs]
    except (learn_gen.GenError, species_quiz.QuizError, sound_quiz.SoundError) as exc:
        return [_locked(a, str(exc))]
    return [_locked(a, "Ukendt aktivitetstype")]


async def expand(db: AsyncSession, user: User | None, a: dict, rng: random.Random, hue: str, course_skills: list[str]) -> list[dict]:
    if a["type"] in STATIC:
        return [await public_static(db, a, rng, hue, course_skills)]
    return await expand_generator(db, user, a, rng, course_skills)


async def unit_questions(db: AsyncSession, user: User | None, st: dict, unit: dict, rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random()
    c = st["course"]
    out: list[dict] = []
    for a in unit["acts"]:
        out += await expand(db, user, a, rng, c["hue"], c["skills"])
    return out


async def drill_questions(db: AsyncSession, user: User | None, st: dict, n: int, mode: str) -> list[dict]:
    """Spørgsmål til en øvelse: alle aktiviteter i kurset, blandet; ``smart`` tager forfaldne/ukendte statiske først."""
    rng = random.Random()
    acts = [a for u in st["units"] for a in u["acts"]]
    c = st["course"]
    if user and mode == "smart":
        static_ids = [a["id"] for a in acts if a["type"] in STATIC]
        prog = {p.question_id: p for p in (await db.execute(select(QuestionProgress).where(
            QuestionProgress.user_id == user.id, QuestionProgress.question_id.in_(static_ids)))).scalars()} if static_ids else {}
        now = datetime.now(timezone.utc)

        def key(a):
            p = prog.get(a["id"])
            return (0 if p and p.due_at <= now else 1 if p is None or a["type"] not in STATIC else 2, rng.random())
        acts.sort(key=key)
    else:
        rng.shuffle(acts)
    out: list[dict] = []
    for a in acts:
        if len(out) >= n:
            break
        out += await expand(db, user, a, rng, c["hue"], c["skills"])
    rng.shuffle(out)
    return [q for q in out[:max(n, 1)]]


# ---------------------------------------------------------------- svar

def _box(box: int, ok: bool) -> int:
    return min(box + 1, len(SPECIES_BOX_DAYS) - 1) if ok else 0


async def mastery(db: AsyncSession, user_id: int, artnr: int, modality: str, ok: bool) -> None:
    row = await db.get(LrSpeciesMastery, (user_id, artnr, modality))
    now = datetime.now(timezone.utc)
    if row is None:
        row = LrSpeciesMastery(user_id=user_id, artnr=artnr, modality=modality, box=0, correct=0, wrong=0)
        db.add(row)
    row.box = _box(row.box or 0, ok)
    row.correct = (row.correct or 0) + (1 if ok else 0)
    row.wrong = (row.wrong or 0) + (0 if ok else 1)
    row.due_at = now + timedelta(days=SPECIES_BOX_DAYS[row.box]) if ok else now + timedelta(minutes=10)
    row.last_at = now


def log_answer(db: AsyncSession, user: User, *, source: str, question_id: int | None, artnr: int | None, modality: str, ok: bool,
               confidence: int | None, skills: list[str], ms: int | None) -> None:
    db.add(LrAnswer(user_id=user.id, source=source, question_id=question_id, artnr=artnr, modality=modality, correct=ok,
                    confidence=confidence if confidence in (0, 1, 2) else None, skills=[s for s in skills if s in SKILLS][:6], ms=ms if ms and 0 < ms < 3_600_000 else None))


class AnswerError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _zone_hit(zones: list[dict], pt: dict | None) -> bool:
    if not pt:
        return False
    for z in zones:
        if z.get("correct") and ((pt["x"] - z["x"]) ** 2 + (pt["y"] - z["y"]) ** 2) ** 0.5 <= float(z.get("r", 0.08)):
            return True
    return False


def judge_static(a: dict, body: dict) -> tuple[bool, dict]:
    """Bedøm en statisk aktivitet. Returnerer (rigtigt, ekstra felter i svaret)."""
    t, cfg = a["type"], a.get("config") or {}
    if t in ("valg", "silhuet"):
        right = {i for i, c in enumerate(a["choices"]) if c.get("correct")}
        chosen = {int(x) for x in body.get("chosen") or []}
        return chosen == right, {"correct_ids": sorted(right)}
    if t == "tekst":
        accept = a.get("accept") or [c["text"] for c in a["choices"] if c.get("correct")]
        return text_matches(body.get("text", ""), accept, bool(cfg.get("fuzzy"))), {"answer": accept[0] if accept else ""}
    if t == "raekkefoelge":
        n = len(cfg.get("items", []))
        order = [int(x) for x in body.get("order") or []]
        return order == list(range(n)), {"order": list(range(n)), "items": cfg.get("items", [])}
    if t == "billedpunkt":
        return _zone_hit(cfg.get("zones", []), body.get("point")), {"zones": cfg.get("zones", [])}
    if t == "refleksion":
        return True, {"example": cfg.get("example", "")}
    raise AnswerError(400, "Ukendt aktivitetstype")


async def answer_static(db: AsyncSession, user: User | None, body: dict) -> dict:
    qid = int(body["id"])
    q = await db.get(Question, qid)
    c = await db.get(Course, q.course_id) if q else None
    if q is None or c is None or not await can_view(db, user, c):
        raise AnswerError(404, "Spørgsmålet findes ikke")
    a = act_from_row(q)
    preview = bool(body.get("preview")) and can_edit_course(user, c, await editor_ids(db, c.id))
    if not preview and c.published_snapshot:
        a = find_act(c.published_snapshot, qid)
        if a is None:
            raise AnswerError(410, "Spørgsmålet er ændret – genindlæs siden")
    ok, extra = judge_static(a, body)
    honest = False
    if a["type"] == "valg" and body.get("niveau") == "ubestemt":
        ok, honest = False, body.get("confidence") == 0
    res = {"correct": ok, "honest": honest, "explanation": a["explanation"], **extra}
    if user and not preview:
        scored = a["type"] != "refleksion"
        if scored:
            await learning.touch_progress(db, user.id, qid, ok)
        log_answer(db, user, source="kursus" if c.kind == "kursus" else "ovelse", question_id=qid, artnr=None,
                   modality="silhuet" if a["type"] == "silhuet" else "tekst", ok=ok, confidence=body.get("confidence"),
                   skills=a["skills"] or list(c.skills or []), ms=body.get("ms"))
        await db.commit()
    return res


async def answer_token(db: AsyncSession, user: User | None, body: dict) -> dict:
    sid = str(body["id"])
    prefix, _, tok = sid.partition(":")
    conf, ms = body.get("confidence"), body.get("ms")
    if prefix == "auto":
        res = await learning.auto_answer(tok, int(body.get("chosen", [-1])[0]) if body.get("chosen") else -1)
        if res is None:
            raise AnswerError(410, "Spørgsmålet er udløbet")
        if user:
            log_answer(db, user, source="obs", question_id=None, artnr=None, modality="data", ok=res["correct"], confidence=conf, skills=["Forbered"], ms=ms)
            await db.commit()
        return {**res, "honest": False}
    if prefix in ("art", "snd"):
        if user is None:
            raise AnswerError(401, "Log ind")
        chosen = body.get("chosen")
        artnr = int(chosen[0]) if chosen else await resolve_text(db, body.get("text", ""))
        if body.get("niveau") == "ubestemt":
            artnr = 0
        res = await species_quiz.answer(db, tok, artnr, "quiz:art:" if prefix == "art" else "quiz:snd:")
        if res is None:
            raise AnswerError(410, "Spørgsmålet er udløbet")
        mod = "foto" if prefix == "art" else "lyd"
        honest = artnr == 0 and conf == 0
        log_answer(db, user, source="arter" if prefix == "art" else "lyd", question_id=None, artnr=res["artnr"], modality=mod, ok=res["correct"],
                   confidence=conf, skills=["Genkald"] if mod == "foto" else ["Lyt", "Genkald"], ms=ms)
        if not honest:
            await mastery(db, user.id, res["artnr"], mod, res["correct"])
        await db.commit()
        return {**res, "honest": honest, "explanation": f"Det var {res['navn']} ({res['latin']})."}
    if prefix == "lr":
        raw = await kv().get("quiz:lr:" + tok)
        if not raw:
            raise AnswerError(410, "Spørgsmålet er udløbet")
        d = json.loads(raw)
        chosen = body.get("chosen") or []
        idx = int(chosen[0]) if chosen else -1
        ok = 0 <= idx < len(d["options"]) and d["options"][idx] == d["correct"]
        cid = d["options"].index(d["correct"])
        if user:
            log_answer(db, user, source="obs" if d["mod"] == "data" else "forbered", question_id=None, artnr=d.get("artnr"), modality=d["mod"], ok=ok,
                       confidence=conf, skills=["Forbered", "Vurdér"] if d["mod"] == "data" else ["Sammenlign"], ms=ms)
            if d["mod"] == "foto" and d.get("artnr") and body.get("niveau") != "ubestemt":
                await mastery(db, user.id, d["artnr"], "foto", ok)
            await db.commit()
        return {"correct": ok, "honest": False, "correct_ids": [cid], "explanation": d["explanation"], **(d.get("reveal") or {}), "map": d.get("map")}
    raise AnswerError(404, "Ukendt spørgsmål")


async def answer(db: AsyncSession, user: User | None, body: dict) -> dict:
    sid = body.get("id")
    if isinstance(sid, int) or (isinstance(sid, str) and sid.isdigit()):
        body = {**body, "id": int(sid)}
        return await answer_static(db, user, body)
    if isinstance(sid, str) and ":" in sid:
        return await answer_token(db, user, body)
    raise AnswerError(400, "Mangler spørgsmål")


async def make_snapshot(db: AsyncSession, c: Course) -> dict:
    """Låst kopi af kursets nuværende indhold (bruges ved publicering og ved migrering)."""
    return json.loads(json.dumps(await live_struct(db, c), default=str))


# ---------------------------------------------------------------- lektioner: tekst og indholdsblokke

async def render_md(db: AsyncSession, md: str | None) -> str:
    from portal.services import content_render

    return (await content_render.render_for(db, md or "", "artikel")).html if md else ""


async def resolve_blocks(db: AsyncSession, blocks: list[dict]) -> list[dict]:
    """Gør en lektions indholdsblokke klar til visning (fotos, lyd, artskort, kort og markdown → html)."""
    out: list[dict] = []
    wk = await learn_gen.weekly()
    for b in blocks or []:
        t = b.get("type")
        r = {"type": t}
        if t in ("tekst", "tip", "advarsel"):
            r["html"] = await render_md(db, b.get("md"))
            r["titel"] = b.get("titel", "")
        elif t == "foto":
            pid = int(b.get("photo_id") or 0) or await photo_by_ref(db, b.get("ref"))
            m = await photo_media(db, pid) if pid else None
            r.update({"foto": m, "tekst": b.get("tekst", "")})
        elif t == "fotoraekke":
            labels = b.get("labels") or []
            ids = [int(i) for i in b.get("photo_ids") or []] or [await photo_by_ref(db, x) for x in b.get("refs") or []]
            fotos = []
            for i, pid in enumerate(ids):
                m = await photo_media(db, pid) if pid and pid not in ids[:i] else None  # samme foto kun én gang i rækken
                if m:
                    fotos.append({**m, "label": labels[i] if i < len(labels) and isinstance(labels[i], str) else ""})
            r["fotos"] = fotos
            r["tekst"] = b.get("tekst", "")
        elif t == "lyd":
            rid = int(b.get("rec_id") or 0) or (await rec_by_ref(db, b.get("ref")) if b.get("ref") else None)
            if rid:
                r["lyd"] = await sound_media(db, rid, hide=False)
            elif b.get("url"):
                r["lyd"] = {"kind": "audio", "value": b["url"], "credit": b.get("credit", "")}
            r["tekst"] = b.get("tekst", "")
        elif t == "silhuet":
            r.update({"sil": b.get("sil", "spurvefugl"), "hue": b.get("hue", "himmel"), "tekst": b.get("tekst", "")})
        elif t == "artskort":
            sp = (await db.execute(text("SELECT artnr, navn, latin, klass FROM species WHERE artnr = :a"), {"a": int(b.get("artnr") or 0)})).mappings().first()
            if sp:
                r.update({"art": dict(sp), "uger": (wk.get(str(sp["artnr"])) or {}).get("rr")})
        elif t == "kort":
            loc = await learn_gen.location(int(b["loknr"])) if b.get("loknr") else None
            r["sted"] = loc
        elif t == "artikel":
            row = (await db.execute(text("SELECT id, title FROM art_articles WHERE id = :i"), {"i": int(b.get("id") or 0)})).mappings().first()
            r["artikel"] = dict(row) if row else None
        elif t == "metode":
            r["trin"] = int(b.get("trin") or 0)
        else:
            continue
        out.append(r)
    return out


METODE = ["Forudsig", "Opdag", "Tolk", "Adskil", "Beslut"]


async def unit_progress_map(db: AsyncSession, user: User | None, unit_ids: list[int]) -> dict[int, dict]:
    if not user or not unit_ids:
        return {}
    rows = (await db.execute(text("SELECT unit_id, best_score, total, attempts, completed_at FROM lr_unit_progress WHERE user_id = :u AND unit_id = ANY(:i)"),
                             {"u": user.id, "i": unit_ids})).mappings().all()
    return {r["unit_id"]: dict(r) for r in rows}


def unit_states(units: list[dict], prog: dict[int, dict]) -> list[dict]:
    """Låst/åben/færdig pr. lektion: en lektion er låst, hvis den forrige har et scorekrav, der ikke er opfyldt."""
    out = []
    for i, u in enumerate(units):
        locked = False
        if i > 0:
            prev = units[i - 1]
            need = prev.get("min_score_pct")
            p = prog.get(prev["id"])
            if need:
                locked = not p or not p["total"] or (100 * p["best_score"] / p["total"]) < need
        p = prog.get(u["id"])
        out.append({"locked": locked, "done": bool(p and p["completed_at"]), "best": (p["best_score"], p["total"]) if p else None})
    return out


async def finish_unit(db: AsyncSession, user: User, unit_id: int, score: int, total: int) -> dict:
    from portal.models import LrUnitProgress

    total = max(0, min(total, 200))
    score = max(0, min(score, total))
    row = await db.get(LrUnitProgress, (user.id, unit_id))
    now = datetime.now(timezone.utc)
    if row is None:
        row = LrUnitProgress(user_id=user.id, unit_id=unit_id, best_score=0, total=0, attempts=0)
        db.add(row)
    row.attempts = (row.attempts or 0) + 1
    if not row.total or (score / max(1, total)) >= (row.best_score / max(1, row.total)):
        row.best_score, row.total = score, total
    row.completed_at = row.completed_at or now
    await db.commit()
    return {"best": [row.best_score, row.total], "attempts": row.attempts}


async def auto_session(db: AsyncSession, kind: str, n: int, afdeling_id: int | None) -> list[dict]:
    qs = await learning.auto_quiz(db, kind, n, afdeling_id)
    return [{"id": "auto:" + q["id"], "type": "obs", "ask_confidence": True, "skills": ["Forbered", "Vurdér"], "prompt": q["prompt"], "choices": q["choices"], "multi": False} for q in qs]
