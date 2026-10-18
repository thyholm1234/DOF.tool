"""E-læring: kurser med multiple choice (billede/lyd/tekst), spaced repetition og
automatisk genererede quizzer ud fra portalens egne data (fænologi, status, sjældenhed)."""

from __future__ import annotations

import json
import random
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import get_json, kv
from portal.config import SEED_DIR
from portal.models import Course, Question, QuestionProgress

BOX_DAYS = [0, 1, 3, 7, 16, 35, 90]
MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
          "november", "december"]


def doy_label(doy: float) -> str:
    d = date(2025, 1, 1) + timedelta(days=int(round(doy)) - 1)
    half = "først i" if d.day <= 15 else "sidst i"
    return f"{half} {MONTHS[d.month - 1]}"


def public_question(q: Question, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    choices = [{"id": i, "text": c["text"]} for i, c in enumerate(q.choices)]
    rng.shuffle(choices)
    return {"id": q.id, "prompt": q.prompt, "media_kind": q.media_kind, "media_url": q.media_url,
            "media_credit": q.media_credit, "choices": choices, "multi": sum(1 for c in q.choices if c.get("correct")) > 1}


async def session_questions(session: AsyncSession, user_id: int | None, course: Course, n: int = 10,
                            mode: str = "smart") -> list[dict]:
    qs = (await session.execute(select(Question).where(Question.course_id == course.id))).scalars().all()
    if not qs:
        return []
    rng = random.Random()
    if user_id and mode == "smart":
        prog = {p.question_id: p for p in (await session.execute(
            select(QuestionProgress).where(QuestionProgress.user_id == user_id,
                                           QuestionProgress.question_id.in_([q.id for q in qs])))).scalars()}
        now = datetime.now(timezone.utc)
        due = [q for q in qs if q.id in prog and prog[q.id].due_at <= now]
        new = [q for q in qs if q.id not in prog]
        rest = [q for q in qs if q.id in prog and prog[q.id].due_at > now]
        rng.shuffle(due)
        rng.shuffle(new)
        rest.sort(key=lambda q: (prog[q.id].box, prog[q.id].due_at))
        picked = (due + new + rest)[:n]
    else:
        picked = rng.sample(list(qs), min(n, len(qs)))
    rng.shuffle(picked)
    return [public_question(q, rng) for q in picked]


async def answer(session: AsyncSession, user_id: int | None, question: Question, chosen: list[int]) -> dict:
    correct_ids = {i for i, c in enumerate(question.choices) if c.get("correct")}
    ok = set(chosen) == correct_ids
    if user_id:
        prog = await session.get(QuestionProgress, (user_id, question.id))
        now = datetime.now(timezone.utc)
        if prog is None:
            prog = QuestionProgress(user_id=user_id, question_id=question.id, box=0, correct=0, wrong=0)
            session.add(prog)
        prog.box = min((prog.box or 0) + 1, len(BOX_DAYS) - 1) if ok else 0
        prog.correct = (prog.correct or 0) + (1 if ok else 0)
        prog.wrong = (prog.wrong or 0) + (0 if ok else 1)
        prog.due_at = now + timedelta(days=BOX_DAYS[prog.box]) if ok else now + timedelta(minutes=10)
        prog.last_at = now
        await session.commit()
    return {"correct": ok, "correct_ids": sorted(correct_ids), "explanation": question.explanation}


async def course_progress(session: AsyncSession, user_id: int) -> dict[int, dict]:
    rows = (await session.execute(text("""
        SELECT q.course_id, count(*) AS seen, count(*) FILTER (WHERE p.box >= 3) AS mastered,
               count(*) FILTER (WHERE p.due_at <= now()) AS due
        FROM question_progress p JOIN questions q ON q.id = p.question_id WHERE p.user_id = :u GROUP BY q.course_id
    """), {"u": user_id})).mappings().all()
    return {r["course_id"]: dict(r) for r in rows}


# ---------------------------------------------------------------- automatiske quizzer

AUTO_KINDS = {
    "saeson": "Årstidens fugle",
    "traek": "Hvornår trækker de?",
    "status": "SU, SUB eller almindelig?",
    "sjaelden": "Hvor sjælden er den lokalt?",
}


async def _store(correct: int, explanation: str) -> str:
    token = secrets.token_urlsafe(9)
    await kv().set(f"quiz:auto:{token}", json.dumps({"correct": correct, "explanation": explanation}), ex=7200)
    return token


async def auto_answer(token: str, chosen: int) -> dict | None:
    raw = await kv().get(f"quiz:auto:{token}")
    if not raw:
        return None
    data = json.loads(raw)
    return {"correct": chosen == data["correct"], "correct_ids": [data["correct"]], "explanation": data["explanation"]}


async def _mc(prompt: str, options: list[str], correct_text: str, explanation: str, rng: random.Random) -> dict:
    rng.shuffle(options)
    idx = options.index(correct_text)
    token = await _store(idx, explanation)
    return {"id": token, "prompt": prompt, "choices": [{"id": i, "text": t} for i, t in enumerate(options)],
            "media_kind": None, "media_url": None, "auto": True, "multi": False}


async def auto_quiz(session: AsyncSession, kind: str, n: int = 10, afdeling_id: int | None = None) -> list[dict]:
    rng = random.Random()
    overview = (await get_json("pheno:overview") or {}).get("species", [])
    weekly = await get_json("pheno:weekly") or {}
    out: list[dict] = []
    if kind == "saeson" and weekly:
        species = list(weekly.items())
        kinds = {o["artnr"]: o["type"] for o in overview}
        for _ in range(n * 4):
            if len(out) >= n:
                break
            week = rng.randrange(52)
            # Rigtigt svar: almindelig art der er på sit højeste; forkerte: udprægede trækfugle der er væk
            present = [(a, w) for a, w in species if w["rr"][week] >= 0.5 * max(w["rr"]) and w["rr"][week] >= 0.04]
            absent = [(a, w) for a, w in species if kinds.get(int(a)) == "trækfugl"
                      and w["rr"][week] <= 0.02 * max(w["rr"]) and max(w["rr"]) >= 0.03]
            if not present or len(absent) < 3:
                continue
            a, right = rng.choice(present)
            wrong = rng.sample(absent, 3)
            d = date(2025, 1, 1) + timedelta(weeks=week)
            label = f"uge {week + 1} (omkring {d.day}. {MONTHS[d.month - 1]})"
            out.append(await _mc(
                f"Hvilken art har du størst chance for at se i Danmark i {label}?",
                [right["navn"]] + [w["navn"] for _, w in wrong], right["navn"],
                f"{right['navn']} er normalt almindelig på denne tid; de andre er stort set fraværende i {MONTHS[d.month - 1]}.",
                rng))
    elif kind == "traek" and overview:
        cands = [s for s in overview if "spring" in s["seasons"] and s["type"] == "trækfugl"
                 and (s["seasons"]["spring"]["trend_q50"] or {}).get("n_years", 0) >= 8]
        rng.shuffle(cands)
        for s in cands[:n]:
            q50 = s["seasons"]["spring"]["normal"]["q50"]
            right = doy_label(q50)
            options = {right}
            while len(options) < 4:
                options.add(doy_label(max(15, min(180, q50 + rng.choice([-45, -30, -15, 15, 30, 45])))))
            ev = s["seasons"]["spring"].get("event", "træktop")
            question = {
                "ankomst": f"Hvornår ankommer {s['navn']} normalt til Danmark (når forekomsten har nået halvdelen af sit forårsniveau)?",
                "afrejse": f"Hvornår forlader {s['navn']} normalt Danmark om foråret?",
            }.get(ev, f"Hvornår topper forårstrækket af {s['navn']} normalt i Danmark?")
            out.append(await _mc(question, list(options), right,
                f"Nøgledatoen for {s['navn']} er normalt {right} – median over {s['seasons']['spring']['n_years']} år, "
                f"beregnet fra indsatskorrigerede DOFbasen-data.", rng))
    elif kind == "status":
        rows = (await session.execute(text("""
            SELECT s.navn, s.klass FROM species s JOIN area_species_counts c ON c.area_key = 'dk' AND c.artnr = s.artnr
            WHERE s.art_type = 'art' AND s.list_artnr = s.artnr AND c.n_obs >= 3
        """))).all()
        by = {"SU": [r[0] for r in rows if r[1] == "SU"], "SUB": [r[0] for r in rows if r[1] == "SUB"],
              "Alm": [r[0] for r in rows if r[1] == "Alm"]}
        labels = {"SU": "SU – kræver dokumentation til SU", "SUB": "SUB – lokal sjældenhedsudvalg",
                  "Alm": "Ingen særlig behandling"}
        for _ in range(n):
            klass = rng.choice([k for k in by if by[k]] or ["Alm"])
            if not by.get(klass):
                break
            navn = rng.choice(by[klass])
            out.append(await _mc(f"Hvordan behandles fund af {navn} i DOFbasen?", list(labels.values()), labels[klass],
                                 f"{navn} er klassificeret som {klass}.", rng))
    elif kind == "sjaelden":
        key = f"afd:{afdeling_id}" if afdeling_id else "dk"
        place = "din lokalafdeling" if afdeling_id else "Danmark"
        rows = (await session.execute(text("""
            SELECT s.navn, c.n_obs FROM area_species_counts c JOIN species s ON s.artnr = c.artnr
            WHERE c.area_key = :k AND s.art_type = 'art' AND s.list_artnr = s.artnr AND c.n_obs >= 2
        """), {"k": key})).all()
        if len(rows) >= 8:
            ordered = sorted(rows, key=lambda r: r[1])
            for _ in range(n):
                rare = rng.choice(ordered[: max(4, len(ordered) // 6)])
                common = rng.sample([r for r in ordered if r[1] >= rare[1] * 20] or ordered[-30:], 3)
                out.append(await _mc(
                    f"Hvilken af disse arter er sjældnest i {place} (færrest observationer i databasen)?",
                    [rare[0]] + [c[0] for c in common], rare[0],
                    f"{rare[0]}: {rare[1]} obs. " + ", ".join(f"{c[0]}: {c[1]:,} obs".replace(",", ".") for c in common) + ".",
                    rng))
    return out


# ---------------------------------------------------------------- seed


async def seed_courses(session: AsyncSession) -> None:
    path = SEED_DIR / "learning.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for c in data["courses"]:
        exists = (await session.execute(select(Course).where(Course.slug == c["slug"]))).scalar_one_or_none()
        if exists:
            continue
        course = Course(slug=c["slug"], title=c["title"], summary=c.get("summary", ""), topic=c.get("topic", "id"),
                        level=c.get("level", "øvet"), intro=c.get("intro", ""), published=True)
        session.add(course)
        await session.flush()
        for i, q in enumerate(c["questions"]):
            session.add(Question(course_id=course.id, position=i, prompt=q["prompt"], choices=q["choices"],
                                 explanation=q.get("explanation", ""), media_kind=q.get("media_kind"),
                                 media_url=q.get("media_url"), media_credit=q.get("media_credit")))
    await session.commit()
