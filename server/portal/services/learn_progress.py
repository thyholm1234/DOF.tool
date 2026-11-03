"""Læring: fremgang – færdigheder, artsmestring, kalibrering, aktivitet og mål (docs/laering-spec.md §11)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import LrGoal, User
from portal.services import learn_play, species_quiz

MASTERED_BOX = 4
CAL_TARGET = {0: (0, 60), 1: (50, 85), 2: (88, 100)}  # (fra, til) – rimelig rigtig-procent for lav/middel/høj sikkerhed


async def skills(db: AsyncSession, user_id: int, days: int = 90) -> list[dict]:
    rows = (await db.execute(text("""SELECT s AS skill, count(*) AS n, count(*) FILTER (WHERE correct) AS ok FROM lr_answers, unnest(skills) s
        WHERE user_id = :u AND created_at > now() - make_interval(days => :d) GROUP BY s"""), {"u": user_id, "d": days})).mappings().all()
    by = {r["skill"]: r for r in rows}
    return [{"skill": k, "n": by[k]["n"] if k in by else 0, "pct": round(100 * by[k]["ok"] / by[k]["n"]) if k in by and by[k]["n"] else None} for k in learn_play.SKILLS]


async def calibration(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(text("""SELECT confidence AS c, count(*) AS n, count(*) FILTER (WHERE correct) AS ok FROM lr_answers WHERE user_id = :u AND confidence IS NOT NULL
        AND created_at > now() - interval '180 days' GROUP BY confidence"""), {"u": user_id})).mappings().all()
    by = {r["c"]: r for r in rows}
    out = []
    for c, label in ((0, "Lav"), (1, "Middel"), (2, "Høj")):
        n = by[c]["n"] if c in by else 0
        pct = round(100 * by[c]["ok"] / n) if n else None
        lo, hi = CAL_TARGET[c]
        out.append({"niveau": label, "n": n, "pct": pct, "godt": (lo <= pct <= hi) if pct is not None and n >= 10 else None, "maal": [lo, hi]})
    return out


async def mastery_summary(db: AsyncSession, user_id: int) -> dict:
    rows = (await db.execute(text("""SELECT modality, count(*) AS n, count(*) FILTER (WHERE box >= :b) AS mastered FROM lr_species_mastery WHERE user_id = :u GROUP BY modality"""),
                             {"u": user_id, "b": MASTERED_BOX})).mappings().all()
    by = {r["modality"]: {"set": r["n"], "mestret": r["mastered"]} for r in rows}
    return {"foto": by.get("foto", {"set": 0, "mestret": 0}), "lyd": by.get("lyd", {"set": 0, "mestret": 0})}


async def species_list(db: AsyncSession, user_id: int, group: str | None = None) -> list[dict]:
    allowed: set[int] | None = None
    if group:
        for g in await species_quiz.preset_groups(db):
            if g["key"] == group:
                allowed = set(g["arter"])
    rows = (await db.execute(text("""SELECT m.artnr, s.navn, m.modality, m.box, m.correct, m.wrong, m.due_at FROM lr_species_mastery m JOIN species s ON s.artnr = m.artnr
        WHERE m.user_id = :u ORDER BY s.sortering NULLS LAST"""), {"u": user_id})).mappings().all()
    out: dict[int, dict] = {}
    for r in rows:
        if allowed is not None and r["artnr"] not in allowed:
            continue
        e = out.setdefault(r["artnr"], {"artnr": r["artnr"], "navn": r["navn"], "foto": None, "lyd": None})
        e[r["modality"]] = {"box": r["box"], "ok": r["correct"], "forkert": r["wrong"], "forfalden": r["due_at"] <= datetime.now(timezone.utc)}
    return list(out.values())


async def streak(db: AsyncSession, user_id: int) -> dict:
    days = [r[0] for r in (await db.execute(text("SELECT DISTINCT (created_at AT TIME ZONE 'Europe/Copenhagen')::date AS d FROM lr_answers WHERE user_id = :u AND created_at > now() - interval '400 days' ORDER BY d DESC"), {"u": user_id})).all()]
    today = date.today()
    n, cur = 0, today
    if days and days[0] == today - timedelta(days=1):
        cur = today - timedelta(days=1)  # dagen i dag er ikke slut endnu
    for d in days:
        if d == cur:
            n += 1
            cur -= timedelta(days=1)
        elif d < cur:
            break
    return {"dage_i_traek": n, "traenet_i_dag": bool(days and days[0] == today), "dage_30": len([d for d in days if d > today - timedelta(days=30)])}


async def activity(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(text("""SELECT * FROM (
        SELECT 'lektion' AS kind, c.title || ' · ' || u.title AS title, p.best_score AS score, p.total AS total, p.completed_at AS t FROM lr_unit_progress p JOIN lr_units u ON u.id = p.unit_id JOIN courses c ON c.id = u.course_id WHERE p.user_id = :u AND p.completed_at IS NOT NULL
        UNION ALL SELECT 'øvelse', replace(quiz_key, 'ovelse:', ''), score, total, created_at FROM quiz_results WHERE user_id = :u
        UNION ALL SELECT 'forløb', p.title, NULL, NULL, e.completed_at FROM lr_enrollments e JOIN lr_paths p ON p.id = e.path_id WHERE e.user_id = :u AND e.completed_at IS NOT NULL) x
        ORDER BY t DESC LIMIT 40"""), {"u": user_id})).mappings().all()
    return [dict(r) for r in rows]


async def goal(db: AsyncSession, user_id: int) -> dict | None:
    g = await db.get(LrGoal, user_id)
    if g is None:
        return None
    m = (await mastery_summary(db, user_id))[g.modality if g.modality in ("foto", "lyd") else "lyd"]
    return {"n_species": g.n_species, "modality": g.modality, "deadline": g.deadline.isoformat() if g.deadline else None, "mestret": m["mestret"]}


async def overview(db: AsyncSession, user: User) -> dict:
    certs = (await db.execute(text("SELECT e.certificate_no, p.title, e.completed_at FROM lr_enrollments e JOIN lr_paths p ON p.id = e.path_id WHERE e.user_id = :u AND e.certificate_no IS NOT NULL ORDER BY e.completed_at DESC"), {"u": user.id})).mappings().all()
    return {"faerdigheder": await skills(db, user.id), "kalibrering": await calibration(db, user.id), "mestring": await mastery_summary(db, user.id), "streak": await streak(db, user.id),
            "aktivitet": await activity(db, user.id), "maal": await goal(db, user.id), "beviser": [dict(c) for c in certs]}


async def set_goal(db: AsyncSession, user: User, n: int | None, modality: str, deadline: datetime | None) -> None:
    g = await db.get(LrGoal, user.id)
    if n is None:
        if g:
            await db.delete(g)
        await db.commit()
        return
    if g is None:
        g = LrGoal(user_id=user.id, n_species=n)
        db.add(g)
    g.n_species, g.modality, g.deadline = n, modality, deadline
    await db.commit()
