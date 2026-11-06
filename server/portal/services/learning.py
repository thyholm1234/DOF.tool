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
from portal.models import Course, Question, QuestionProgress, Unit

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


async def touch_progress(session: AsyncSession, user_id: int, question_id: int, ok: bool) -> None:
    """Leitner-kasse for et statisk spørgsmål (uden commit)."""
    prog = await session.get(QuestionProgress, (user_id, question_id))
    now = datetime.now(timezone.utc)
    if prog is None:
        prog = QuestionProgress(user_id=user_id, question_id=question_id, box=0, correct=0, wrong=0)
        session.add(prog)
    prog.box = min((prog.box or 0) + 1, len(BOX_DAYS) - 1) if ok else 0
    prog.correct = (prog.correct or 0) + (1 if ok else 0)
    prog.wrong = (prog.wrong or 0) + (0 if ok else 1)
    prog.due_at = now + timedelta(days=BOX_DAYS[prog.box]) if ok else now + timedelta(minutes=10)
    prog.last_at = now


async def answer(session: AsyncSession, user_id: int | None, question: Question, chosen: list[int]) -> dict:
    correct_ids = {i for i, c in enumerate(question.choices) if c.get("correct")}
    ok = set(chosen) == correct_ids
    if user_id:
        await touch_progress(session, user_id, question.id, ok)
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


SEED_COURSES_KEY = "laering_seed_learning_v1"


async def seed_courses(session: AsyncSession) -> None:
    """De første startkurser – kun én gang, så et kursus, en redaktør har slettet, ikke kommer igen ved næste opstart."""
    from portal.services.setting_store import get_setting, put_setting

    path = SEED_DIR / "learning.json"
    if not path.exists() or await get_setting(session, SEED_COURSES_KEY):
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
    await put_setting(session, SEED_COURSES_KEY, {"kurser": len(data["courses"])}, commit=False)
    await session.commit()


async def migrate_v2(session: AsyncSession) -> int:
    """Læring 2.0 (spec §12.1): hvert gammelt kursus får én lektion med sine spørgsmål, og publicerede kurser får en låst kopi. Idempotent."""
    from portal.services import learn_play

    n = 0
    courses = (await session.execute(text("""SELECT c.id FROM courses c WHERE c.kind = 'kursus'
        AND NOT EXISTS (SELECT 1 FROM lr_units u WHERE u.course_id = c.id) AND EXISTS (SELECT 1 FROM questions q WHERE q.course_id = c.id)"""))).scalars().all()
    for cid in courses:
        c = await session.get(Course, cid)
        u = Unit(course_id=cid, position=0, title="Spørgsmål", lesson_md=c.intro or "", blocks=[])
        session.add(u)
        await session.flush()
        await session.execute(text("UPDATE questions SET unit_id = :u WHERE course_id = :c AND unit_id IS NULL"), {"u": u.id, "c": cid})
        n += 1
    await session.flush()
    for c in (await session.execute(select(Course).where(Course.published, Course.published_snapshot.is_(None)))).scalars():
        c.published_snapshot = await learn_play.make_snapshot(session, c)
        c.version = max(c.version or 0, 1)
    await session.commit()
    return n


# ---------------------------------------------------------------- startindhold fra Trækrute (docs/laering-spec.md §12.2)

SEED_KEY = "laering_seed_v1"
# Øvelser fra Trækrute, der nu er indbyggede funktioner (Repetition, Færdighedstest, Fugl for fugl) – de importeres ikke
SKIP_DRILLS = {"mine-fejl", "faerdighedstest", "fugl-for-fugl", "familiequiz"}
# Trækrutes fem metodekurser er afløst af det samlede kursus «Se på fugle» (pakken se-paa-fugle.json) og importeres ikke længere.
# Allerede importerede kladder røres ikke (startindhold rører aldrig et kursus efter første import) – en redaktør kan slette dem.
SKIP_COURSES = {"iagttag-foer-du-navngiver", "en-bestemmelse-du-kan-forklare", "kend-dine-lokale-fugle", "fuglekig-naar-forholdene-skifter",
                "vanskelige-observationer"}
# Øvelser, der får datadrevne aktiviteter ovenpå de statiske spørgsmål
GENERATED = {
    "habitatdetektiv": [{"type": "obs_habitat", "prompt": "", "config": {"antal": 3, "n_valg": 3}}],
    "observationsodds": [{"type": "obs_odds", "prompt": "", "config": {"antal": 3, "uge": "nu", "n_valg": 2}}],
    "saesonskift": [{"type": "obs_saeson", "prompt": "", "config": {"antal": 3}}],
}


class _System:
    """Stand-in for en bruger ved seed (ingen række i users)."""
    id = None
    role = "superadmin"
    blocked = False
    permissions = None


async def seed_traekrute(session: AsyncSession) -> int:
    """Første gang: importér Trækrutes kurser og øvelser som KLADDER (redaktøren gennemlæser og publicerer) og lav tre forløb som kladder.
    Importeres kun én gang (markør i indstillingerne); findes en slug allerede, springes den over."""
    from portal.services import learn_build
    from portal.services.setting_store import get_setting, put_setting

    path = SEED_DIR / "laering" / "traekrute.json"
    if not path.exists() or await get_setting(session, SEED_KEY):
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    data["drills"] = [d for d in data.get("drills", []) if d["slug"] not in SKIP_DRILLS]
    existing = {r for r in (await session.execute(text("SELECT slug FROM courses"))).scalars()}
    data["courses"] = [c for c in data["courses"] if c["slug"] not in existing | SKIP_COURSES]
    data["drills"] = [d for d in data["drills"] if d["slug"] not in existing]
    report = await learn_build.import_courses(session, _System(), data)  # type: ignore[arg-type]
    for slug, acts in GENERATED.items():
        c = (await session.execute(select(Course).where(Course.slug == slug))).scalar_one_or_none()
        if c is None:
            continue
        n = (await session.execute(text("SELECT count(*) FROM questions WHERE course_id = :c"), {"c": c.id})).scalar() or 0
        for i, a in enumerate(acts):
            session.add(Question(course_id=c.id, position=n + i, choices=[], explanation="", skills=["Forbered"], **a))
    for c in (await session.execute(select(Course).where(Course.created_by.is_(None), Course.published.is_(False)))).scalars():
        c.card = c.card or c.summary
    await session.flush()
    await _seed_paths(session)
    await put_setting(session, SEED_KEY, {"kurser": len(report)}, commit=False)
    await session.commit()
    return len(report)


# Kursuspakker i DOF.tool-formatet (``learn_build.export_courses``, "laering-v2"): importeres én gang som KLADDER, så en redaktør
# kan gennemlæse, skifte fotos/lyde og publicere. Nøglen er markøren i indstillingerne; ny version af en pakke = ny nøgle.
SEED_PACKS = {"se-paa-fugle.json": "laering_seed_se_paa_fugle_v1", "phylloscopus.json": "laering_seed_phylloscopus_v1", "maager.json": "laering_seed_maager_v1",
              "rovfugle.json": "laering_seed_rovfugle_v1", "havfugle.json": "laering_seed_havfugle_v1",
              "vadefugle.json": "laering_seed_vadefugle_v1", "fugle100.json": "laering_seed_fugle100_v1",
              "traek.json": "laering_seed_traek_v1"}


async def seed_packs(session: AsyncSession) -> int:
    from portal.services import learn_build
    from portal.services.setting_store import get_setting, put_setting

    n = 0
    for name, key in SEED_PACKS.items():
        path = SEED_DIR / "laering" / name
        if not path.exists():
            continue
        if await get_setting(session, key):
            # allerede importeret: giv ÉN gang kurser uden forsidebillede pakkens billede (kom til efter første import).
            # Derefter rører pakken aldrig kurset igen – redaktørens rettelser (også et fjernet billede) bliver stående.
            if not await get_setting(session, key + "_cover"):
                for c in json.loads(path.read_text(encoding="utf-8"))["courses"]:
                    if c.get("cover_ref"):
                        await session.execute(text("""UPDATE courses SET cover_ref = CAST(:r AS jsonb)
                            WHERE slug = :s AND cover_ref IS NULL AND cover_media_id IS NULL"""), {"r": json.dumps(c["cover_ref"]), "s": c["slug"]})
                await put_setting(session, key + "_cover", {"ok": True}, commit=False)
                await session.commit()
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        existing = {r for r in (await session.execute(text("SELECT slug FROM courses"))).scalars()}
        data["courses"] = [c for c in data["courses"] if c["slug"] not in existing]
        report = await learn_build.import_courses(session, _System(), data)  # type: ignore[arg-type]
        await put_setting(session, key, {"kurser": len(report)}, commit=False)
        await put_setting(session, key + "_cover", {"ok": True}, commit=False)  # forsidebilledet fulgte med importen
        await session.commit()
        n += len(report)
    return n


async def _seed_paths(session: AsyncSession) -> None:
    """Tre eksempelforløb som kladder (spec §9.1). Kurserne er kladder, så forløbene kan først publiceres, når de er publiceret."""
    from portal.models import LrPath, LrPathItem

    async def course_id(slug):
        return (await session.execute(text("SELECT id FROM courses WHERE slug = :s"), {"s": slug})).scalar()

    felt = lambda titel, tekst, regel, mode="underviser": {"titel": titel, "tekst_md": tekst, "godkendelse": mode, "regel": {"min_arter": 1, **regel, "periode": "efter_oplaasning"}}  # noqa: E731

    async def make(slug, title, summary, level, items):
        if (await session.execute(text("SELECT 1 FROM lr_paths WHERE slug = :s"), {"s": slug})).first():
            return
        p = LrPath(slug=slug, title=title, summary=summary, level=level, published=False, visibility="alle", certificate=True,
                   certificate_text="Gennemført via DOF.tool", intro_md=summary)
        session.add(p)
        await session.flush()
        for i, it in enumerate(items):
            session.add(LrPathItem(path_id=p.id, position=i, unlock=it.pop("unlock", {"after": "none"}), **it))

    ids = {s: await course_id(s) for s in ["se-paa-fugle", "selvstaendigt-feltarbejde", "habitatdetektiv", "silhuet-foerst", "lignende-arter", "laes-sporene"]}
    nxt = {"after": "previous"}
    items = [{"kind": "kursus", "section": "Uge 1–4 – Se på fugle", "course_id": ids["se-paa-fugle"], "min_score_pct": 60},
             {"kind": "ovelse", "section": "Uge 1–4 – Se på fugle", "course_id": ids["silhuet-foerst"], "required": False, "title": "Silhuet først"},
             {"kind": "felt", "section": "Uge 1–4 – Se på fugle", "unlock": nxt, "field_task": felt("Beskriv tre fugle uden at navngive dem", "Gå en tur, og notér form, størrelse og bevægelse for tre fugle, før du ser efter navnet.", {"min_arter": 3}, "selv")},
             {"kind": "ovelse", "section": "Uge 1–4 – Se på fugle", "course_id": ids["habitatdetektiv"], "required": False},
             {"kind": "felt", "section": "Uge 1–4 – Se på fugle", "unlock": nxt, "field_task": felt("Indrapportér mindst 10 arter på en lokal tur", "Indtast turen i DOFbasen – så afkrydses opgaven.", {"min_arter": 10}, "auto")},
             {"kind": "kursus", "section": "Uge 5 – Selvstændighed", "course_id": ids["selvstaendigt-feltarbejde"], "unlock": nxt},
             {"kind": "felt", "section": "Uge 5 – Selvstændighed", "unlock": nxt, "field_task": felt("Planlæg og gennemfør en tur – og forklar én bestemmelse", "Fortæl underviseren, hvordan du kom frem til en bestemmelse.", {"min_arter": 5}, "underviser")}]
    items = [it for it in items if it["kind"] not in ("kursus", "ovelse") or it["course_id"]]  # et kursus, der ikke findes, springes over
    await make("fra-nysgerrig-til-feltbisse", "Fra nysgerrig til feltbisse", "Kurset «Se på fugle», selvstændigt feltarbejde og tre feltopgaver, der gør dig til en mere sikker og ærlig fuglebestemmer.", "begynder", items)
    groups = [("Uge 1 – Haven", "spurvefugle"), ("Uge 2 – Skov og krat", "sangere"), ("Uge 3 – Drosler og finker", "drosler"), ("Uge 4 – Søer og enge", "aender"), ("Uge 5 – Kysten", "maager")]
    tr = []
    for sec, g in groups:
        tr.append({"kind": "traening", "section": sec, "title": f"Foto og lyd: {g}", "training": {"kilde": "gruppe", "gruppe": g, "m": "begge", "metode": "valg", "rtype": "begge", "n": 20}, "text_md": "Træn til du kan svare uden at tøve – gentag gerne flere dage.", "unlock": nxt})
        tr.append({"kind": "felt", "section": sec, "unlock": nxt, "field_task": felt("Find mindst fem arter i gruppen", "Ud i felten – brug det, du har trænet.", {"gruppe": g, "min_arter": 5}, "auto")})
    await make("laer-50-almindelige-fugle", "Lær 50 almindelige fugle", "Fem uger med foto- og lydtræning, fra haven til kysten – og en feltopgave hver uge.", "begynder", tr)
    await make("vadefugle-i-traekket", "Vadefugle i trækket", "Lær de svære vadefugle at kende, og forbered en tur til en god lokalitet.", "øvet",
               [{"kind": "tekst", "section": "Forberedelse", "title": "Find en lokalitet", "text_md": "Brug **Forbered en tur** under Læring til at se, hvilke vadefugle der er sandsynlige på din lokalitet i trækugerne.", "required": False},
                {"kind": "ovelse", "section": "Forberedelse", "course_id": ids["lignende-arter"], "required": False},
                {"kind": "traening", "section": "Træning", "title": "Vadefugle på foto og lyd", "training": {"kilde": "gruppe", "gruppe": "vadefugle", "m": "begge", "metode": "valg", "rtype": "begge", "n": 30}, "unlock": nxt},
                {"kind": "felt", "section": "Felt", "unlock": nxt, "field_task": felt("Find tre vadefuglearter", "Gå til en mudderflade eller strand i trækperioden.", {"gruppe": "vadefugle", "min_arter": 3}, "auto")}])
