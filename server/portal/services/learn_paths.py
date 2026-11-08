"""Læring: forløb, hold, feltopgaver og beviser (docs/laering-spec.md §9–10).

Et *forløb* er en række punkter (kursus, øvelse, gemt træning, feltopgave, tekst) i sektioner. Et *hold* er en gruppe, der følger et forløb
sammen med en underviser; holdets startdato styrer oplåsning ``days_after_start``. En *feltopgave* afkrydses af brugerens egne DOFbasen-observationer
(``users.obserkode`` → observationsgruppen; to forespørgsler, aldrig blandet SQL).
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.db import ObsSession
from portal.models import Course, LrCohort, LrCohortMember, LrEnrollment, LrFieldDone, LrPath, LrPathItem, User
from portal.services import learn_play
from portal.services.auth import can
from portal.services import dataset

KINDS = ("kursus", "ovelse", "traening", "felt", "tekst")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PathError(Exception):
    def __init__(self, status: int, msg: str, extra: dict | None = None):
        super().__init__(msg)
        self.status, self.msg, self.extra = status, msg, extra or {}


def slugify(s: str) -> str:
    from portal.services.learn_build import slugify as sl

    return sl(s)


# ---------------------------------------------------------------- adgang

async def member_cohorts(db: AsyncSession, user_id: int, path_id: int | None = None) -> list[dict]:
    q = """SELECT h.id, h.title, h.path_id, h.starts_on, h.ends_on, h.archived, h.join_code, h.open_join, m.role FROM lr_cohort_members m
        JOIN lr_cohorts h ON h.id = m.cohort_id WHERE m.user_id = :u AND (CAST(:p AS int) IS NULL OR h.path_id = :p) ORDER BY h.starts_on DESC"""
    return [dict(r) for r in (await db.execute(text(q), {"u": user_id, "p": path_id})).mappings().all()]


def can_edit_path(user: User | None, p: LrPath) -> bool:
    return bool(user) and (can(user, "laering") or (can(user, "laering_undervis") and p.created_by == user.id))


async def can_view_path(db: AsyncSession, user: User | None, p: LrPath) -> bool:
    if can_edit_path(user, p):
        return True
    if not p.published or user is None:
        return False
    if p.visibility in ("alle", "login"):
        return True
    return bool(await member_cohorts(db, user.id, p.id))


async def catalog(db: AsyncSession, user: User | None) -> list[dict]:
    rows = (await db.execute(select(LrPath).order_by(LrPath.title))).scalars().all()
    out = []
    for p in rows:
        if not await can_view_path(db, user, p):
            continue
        out.append({"slug": p.slug, "title": p.title, "summary": p.summary, "level": p.level, "published": p.published, "certificate": p.certificate, "visibility": p.visibility})
    return out


# ---------------------------------------------------------------- punkternes status

async def _course_done(db: AsyncSession, user_id: int, course_id: int) -> tuple[bool, int]:
    """(alle lektioner gennemført, samlet score i procent)."""
    r = (await db.execute(text("""SELECT count(u.id) AS n, count(p.completed_at) AS done, coalesce(sum(p.best_score), 0) AS s, coalesce(sum(p.total), 0) AS t FROM lr_units u
        LEFT JOIN lr_unit_progress p ON p.unit_id = u.id AND p.user_id = :u WHERE u.course_id = :c"""), {"u": user_id, "c": course_id})).mappings().first()
    pct = round(100 * r["s"] / r["t"]) if r["t"] else 0
    return bool(r["n"]) and r["n"] == r["done"], pct


async def item_states(db: AsyncSession, user: User, p: LrPath, items: list[LrPathItem], cohort: dict | None, enrolled_at: datetime | None) -> dict[int, dict]:
    """Status pr. punkt: ``done``, ``locked`` (+ ``why``), ``pct`` og for feltopgaver ``venter``."""
    cid = cohort["id"] if cohort else 0
    marked = {r.path_item_id: r for r in (await db.execute(select(LrFieldDone).where(LrFieldDone.user_id == user.id, LrFieldDone.cohort_id == cid,
                                                                                    LrFieldDone.path_item_id.in_([i.id for i in items] or [0])))).scalars()}
    states: dict[int, dict] = {}
    for it in items:
        st = {"done": False, "pct": None, "venter": False}
        if it.kind == "kursus" and it.course_id:
            done, pct = await _course_done(db, user.id, it.course_id)
            st["pct"] = pct
            st["done"] = done and (it.min_score_pct is None or pct >= it.min_score_pct)
        elif it.kind == "ovelse" and it.course_id:
            slug = (await db.execute(text("SELECT slug FROM courses WHERE id = :c"), {"c": it.course_id})).scalar()
            best = (await db.execute(text("SELECT max(score * 100 / total) FROM quiz_results WHERE user_id = :u AND quiz_key = :k AND total > 0"), {"u": user.id, "k": f"ovelse:{slug}"})).scalar()
            st["pct"] = round(best) if best is not None else None
            st["done"] = best is not None and (it.min_score_pct is None or best >= it.min_score_pct)
        else:
            m = marked.get(it.id)
            st["done"] = bool(m and m.approved)
            st["venter"] = bool(m and not m.approved)
        states[it.id] = st
    now = datetime.now(timezone.utc)
    start = (cohort or {}).get("starts_on") or enrolled_at
    for idx, it in enumerate(items):
        rule = it.unlock or {}
        after, why = rule.get("after", "none"), None
        prev_req = next((x for x in reversed(items[:idx]) if x.required), None)  # valgfrie punkter blokerer ikke
        if after == "previous" and prev_req is not None and not states[prev_req.id]["done"]:
            why = "Klar det forrige punkt først"
        elif isinstance(after, list) and any(not states.get(int(x), {"done": True})["done"] for x in after):
            why = "Klar de krævede punkter først"
        days = rule.get("days_after_start")
        if days is not None and cohort and start and now < start + timedelta(days=int(days)):
            why = f"Åbner {(start + timedelta(days=int(days))).date().isoformat()}"
        states[it.id]["locked"] = why is not None
        states[it.id]["why"] = why
    return states


async def load_items(db: AsyncSession, path_id: int) -> list[LrPathItem]:
    return list((await db.execute(select(LrPathItem).where(LrPathItem.path_id == path_id).order_by(LrPathItem.position, LrPathItem.id))).scalars())


def item_out(it: LrPathItem, course: Course | None, st: dict | None = None) -> dict:
    return {"id": it.id, "section": it.section, "kind": it.kind, "title": it.title or (course.title if course else ""), "required": it.required, "min_score_pct": it.min_score_pct,
            "unlock": it.unlock or {}, "course": ({"slug": course.slug, "title": course.title, "kind": course.kind, "published": course.published} if course else None),
            "text_md": it.text_md, "training": it.training, "field_task": it.field_task, **(st or {})}


async def path_view(db: AsyncSession, user: User, p: LrPath, cohort_id: int | None = None) -> dict:
    items = await load_items(db, p.id)
    courses = {c.id: c for c in (await db.execute(select(Course).where(Course.id.in_([i.course_id for i in items if i.course_id] or [0])))).scalars()}
    cohorts = await member_cohorts(db, user.id, p.id)
    cohort = next((c for c in cohorts if c["id"] == cohort_id), None) if cohort_id else (next((c for c in cohorts if not c["archived"]), None))
    enr = (await db.execute(select(LrEnrollment).where(LrEnrollment.user_id == user.id, LrEnrollment.path_id == p.id).order_by(LrEnrollment.id.desc()))).scalars().first()
    states = await item_states(db, user, p, items, cohort, enr.started_at if enr else None)
    sections: list[dict] = []
    for it in items:
        if not sections or sections[-1]["navn"] != it.section:
            sections.append({"navn": it.section, "punkter": []})
        sections[-1]["punkter"].append(item_out(it, courses.get(it.course_id), states[it.id]))
    req = [i for i in items if i.required]
    return {"slug": p.slug, "title": p.title, "summary": p.summary, "intro_md": p.intro_md, "level": p.level, "audience": p.audience, "certificate": p.certificate,
            "published": p.published, "sections": sections, "tilmeldt": bool(enr), "faerdig": bool(enr and enr.completed_at), "bevis": enr.certificate_no if enr else None,
            "fremgang": {"done": sum(1 for i in req if states[i.id]["done"]), "total": len(req)}, "hold": cohort, "mine_hold": cohorts, "can_edit": can_edit_path(user, p)}


async def enroll(db: AsyncSession, user: User, p: LrPath, cohort_id: int | None = None) -> LrEnrollment:
    enr = (await db.execute(select(LrEnrollment).where(LrEnrollment.user_id == user.id, LrEnrollment.path_id == p.id))).scalars().first()
    if enr is None:
        enr = LrEnrollment(user_id=user.id, path_id=p.id, cohort_id=cohort_id)
        db.add(enr)
        await db.commit()
    return enr


async def check_completion(db: AsyncSession, user: User, p: LrPath) -> LrEnrollment | None:
    """Er alle krævede punkter klaret? Så markeres tilmeldingen færdig, og et bevis udstedes (hvis forløbet har et)."""
    enr = (await db.execute(select(LrEnrollment).where(LrEnrollment.user_id == user.id, LrEnrollment.path_id == p.id))).scalars().first()
    if enr is None or enr.completed_at:
        return enr
    items = await load_items(db, p.id)
    cohorts = await member_cohorts(db, user.id, p.id)
    cohort = next((c for c in cohorts if not c["archived"]), None)
    states = await item_states(db, user, p, items, cohort, enr.started_at)
    req = [i for i in items if i.required]
    if req and all(states[i.id]["done"] for i in req):
        enr.completed_at = datetime.now(timezone.utc)
        if p.certificate:
            for _ in range(5):
                no = "DT-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                if not (await db.execute(select(LrEnrollment.id).where(LrEnrollment.certificate_no == no))).first():
                    enr.certificate_no = no
                    break
        await db.commit()
    return enr


async def after_progress(db: AsyncSession, user: User, course_id: int) -> None:
    """Efter en gennemført lektion/øvelse: tjek de forløb, brugeren er tilmeldt, og som indeholder kurset."""
    rows = (await db.execute(text("""SELECT DISTINCT i.path_id FROM lr_path_items i JOIN lr_enrollments e ON e.path_id = i.path_id AND e.user_id = :u
        WHERE i.course_id = :c AND e.completed_at IS NULL"""), {"u": user.id, "c": course_id})).scalars().all()
    for pid in rows:
        p = await db.get(LrPath, pid)
        if p:
            await check_completion(db, user, p)


async def certificate(db: AsyncSession, number: str) -> dict | None:
    r = (await db.execute(text("""SELECT e.certificate_no, e.completed_at, u.navn, p.title, p.certificate_text,
        (SELECT h.title FROM lr_cohorts h WHERE h.id = e.cohort_id) AS hold,
        (SELECT string_agg(uu.navn, ', ') FROM lr_cohort_members m JOIN users uu ON uu.id = m.user_id WHERE m.cohort_id = e.cohort_id AND m.role = 'underviser') AS undervisere
        FROM lr_enrollments e JOIN users u ON u.id = e.user_id JOIN lr_paths p ON p.id = e.path_id WHERE e.certificate_no = :n AND e.completed_at IS NOT NULL"""), {"n": number.upper()})).mappings().first()
    return dict(r) if r else None


# ---------------------------------------------------------------- punkter, der markeres af brugeren selv

async def mark_self(db: AsyncSession, user: User, p: LrPath, item: LrPathItem, cohort_id: int | None, note: str | None = None) -> None:
    if item.kind not in ("traening", "tekst", "felt"):
        raise PathError(400, "Punktet afkrydses ikke manuelt")
    if item.kind == "felt" and (item.field_task or {}).get("godkendelse", "auto") != "selv":
        raise PathError(400, "Feltopgaven afkrydses ud fra dine observationer")
    cid = cohort_id or 0
    row = await db.get(LrFieldDone, (user.id, item.id, cid))
    if row is None:
        db.add(LrFieldDone(user_id=user.id, path_item_id=item.id, cohort_id=cid, how="selv", approved=True, note=(note or "")[:2000] or None))
    await db.commit()
    await check_completion(db, user, p)


# ---------------------------------------------------------------- feltopgaver (§10)

async def field_matches(user: User, rule: dict, since: date, db: AsyncSession) -> tuple[int, list[int], int | None]:
    """(antal forskellige arter, obsid'er, første obsid) for brugerens observationer, der opfylder reglen siden ``since``."""
    if not user.obserkode:
        return 0, [], None
    arter = [int(x) for x in rule.get("arter") or []]
    if rule.get("gruppe"):
        arter += await _group_species(db, rule["gruppe"])
    per = rule.get("periode")
    fra, til = since, date.today()
    if isinstance(per, dict):
        fra = date.fromisoformat(per["fra"]) if per.get("fra") else since
        til = date.fromisoformat(per["til"]) if per.get("til") else til
    async with ObsSession() as o:
        rows = (await dataset.execute(o, """SELECT o.obsid, s.list_artnr AS art FROM observations o JOIN species s ON s.artnr = o.artnr AND s.art_type = 'art'
            WHERE o.obserkode = :k AND o.dato >= :fra AND o.dato <= :til AND (cardinality(CAST(:a AS int[])) = 0 OR s.list_artnr = ANY(CAST(:a AS int[])))
              AND (CAST(:l AS int) IS NULL OR o.loknr = :l) AND (CAST(:af AS int) IS NULL OR :af = ANY(o.afdelinger)) ORDER BY o.obsid LIMIT 500""",
                                {"k": user.obserkode, "fra": fra, "til": til, "a": sorted(set(arter)), "l": rule.get("loknr"), "af": rule.get("afdeling_id")})).all()
    return len({r[1] for r in rows}), [r[0] for r in rows], (rows[0][0] if rows else None)


async def _group_species(db: AsyncSession, key: str) -> list[int]:
    from portal.services import species_quiz

    for g in await species_quiz.preset_groups(db):
        if g["key"] == key:
            return g["arter"]
    return []


async def check_field(db: AsyncSession, user: User, p: LrPath, item: LrPathItem, cohort: dict | None) -> dict:
    """Slå op, om brugerens observationer opfylder feltopgaven; gem resultatet (auto: afkrydset, underviser: venter på godkendelse)."""
    task = item.field_task or {}
    mode = task.get("godkendelse", "auto")
    if mode == "selv":
        return {"status": "selv"}
    if not user.obserkode:
        return {"status": "ingen_profil"}
    cid = cohort["id"] if cohort else 0
    row = await db.get(LrFieldDone, (user.id, item.id, cid))
    if row is not None:
        return {"status": "godkendt" if row.approved else "venter", "obsid": row.obsid}
    enr = (await db.execute(select(LrEnrollment).where(LrEnrollment.user_id == user.id, LrEnrollment.path_id == p.id))).scalars().first()
    start = (cohort or {}).get("starts_on") or (enr.started_at if enr else datetime.now(timezone.utc))
    n, obs, first = await field_matches(user, task.get("regel") or {}, start.date() if hasattr(start, "date") else start, db)
    need = int((task.get("regel") or {}).get("min_arter") or 1)
    if n < need:
        return {"status": "mangler", "fundet": n, "kraeves": need}
    db.add(LrFieldDone(user_id=user.id, path_item_id=item.id, cohort_id=cid, obsid=first, how="obs", approved=(mode == "auto")))
    await db.commit()
    if mode == "auto":
        await check_completion(db, user, p)
    return {"status": "godkendt" if mode == "auto" else "venter", "obsid": first}


async def check_all(db: AsyncSession) -> int:
    """Worker (hver time): feltopgaver for alle tilmeldte, der endnu ikke har afkrydset dem."""
    n = 0
    rows = (await db.execute(text("""SELECT e.user_id, e.path_id, i.id AS item FROM lr_enrollments e JOIN lr_path_items i ON i.path_id = e.path_id AND i.kind = 'felt'
        WHERE e.completed_at IS NULL AND coalesce(i.field_task->>'godkendelse', 'auto') <> 'selv'
          AND NOT EXISTS (SELECT 1 FROM lr_field_done d WHERE d.user_id = e.user_id AND d.path_item_id = i.id)"""))).mappings().all()
    for r in rows:
        user, p, it = await db.get(User, r["user_id"]), await db.get(LrPath, r["path_id"]), await db.get(LrPathItem, r["item"])
        if not (user and p and it) or not user.obserkode:
            continue
        cohorts = await member_cohorts(db, user.id, p.id)
        res = await check_field(db, user, p, it, next((c for c in cohorts if not c["archived"]), None))
        n += res["status"] in ("godkendt", "venter")
    return n


# ---------------------------------------------------------------- hold

async def new_code(db: AsyncSession) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not (await db.execute(select(LrCohort.id).where(LrCohort.join_code == code))).first():
            return code
    raise PathError(500, "Kunne ikke lave en holdkode")


async def create_cohort(db: AsyncSession, user: User, p: LrPath, title: str, starts_on: datetime | None, ends_on: datetime | None, afdeling_id: int | None, open_join: bool, max_members: int | None) -> LrCohort:
    if not (can(user, "laering") or can(user, "laering_undervis")):
        raise PathError(403, "Kun undervisere kan oprette hold")
    if not p.published and not can_edit_path(user, p):
        raise PathError(403, "Forløbet er ikke publiceret")
    c = LrCohort(path_id=p.id, title=title.strip()[:160], afdeling_id=afdeling_id, starts_on=starts_on or datetime.now(timezone.utc), ends_on=ends_on, join_code=await new_code(db),
                 open_join=open_join, max_members=max_members, created_by=user.id)
    db.add(c)
    await db.flush()
    db.add(LrCohortMember(cohort_id=c.id, user_id=user.id, role="underviser"))
    await db.commit()
    return c


async def is_teacher(db: AsyncSession, user: User, cohort_id: int) -> bool:
    if can(user, "laering"):
        return True
    return bool((await db.execute(text("SELECT 1 FROM lr_cohort_members WHERE cohort_id = :c AND user_id = :u AND role = 'underviser'"), {"c": cohort_id, "u": user.id})).first())


async def join(db: AsyncSession, user: User, code: str) -> LrCohort:
    c = (await db.execute(select(LrCohort).where(LrCohort.join_code == code.strip().upper(), LrCohort.archived.is_(False)))).scalar_one_or_none()
    if c is None:
        raise PathError(404, "Holdkoden findes ikke (eller holdet er arkiveret)")
    n = (await db.execute(text("SELECT count(*) FROM lr_cohort_members WHERE cohort_id = :c"), {"c": c.id})).scalar() or 0
    if c.max_members and n >= c.max_members and not await db.get(LrCohortMember, (c.id, user.id)):
        raise PathError(409, "Holdet er fuldt")
    if await db.get(LrCohortMember, (c.id, user.id)) is None:
        db.add(LrCohortMember(cohort_id=c.id, user_id=user.id, role="deltager"))
    await db.commit()
    p = await db.get(LrPath, c.path_id)
    await enroll(db, user, p, c.id)
    return c


async def cohort_view(db: AsyncSession, user: User, c: LrCohort) -> dict:
    teacher = await is_teacher(db, user, c.id)
    p = await db.get(LrPath, c.path_id)
    members = (await db.execute(text("""SELECT m.user_id, m.role, m.joined_at, u.navn, u.obserkode FROM lr_cohort_members m JOIN users u ON u.id = m.user_id WHERE m.cohort_id = :c ORDER BY m.role DESC, u.navn"""), {"c": c.id})).mappings().all()
    out = {"id": c.id, "title": c.title, "path": {"slug": p.slug, "title": p.title}, "starts_on": c.starts_on, "ends_on": c.ends_on, "archived": c.archived, "open_join": c.open_join,
           "teacher": teacher, "members": [{"user_id": m["user_id"], "role": m["role"], "navn": (m["navn"] or m["obserkode"] or "").split(" ")[0] if not teacher else (m["navn"] or m["obserkode"])} for m in members]}
    if teacher:
        out["join_code"] = c.join_code
        out["matrix"] = await matrix(db, c, p, [m for m in members if m["role"] == "deltager"])
    return out


async def matrix(db: AsyncSession, c: LrCohort, p: LrPath, members: list) -> dict:
    items = await load_items(db, p.id)
    cohort = {"id": c.id, "starts_on": c.starts_on}
    rows = []
    for m in members:
        u = await db.get(User, m["user_id"])
        st = await item_states(db, u, p, items, cohort, c.starts_on)
        rows.append({"user_id": u.id, "navn": u.navn or u.obserkode, "punkter": {str(i.id): {"done": st[i.id]["done"], "pct": st[i.id]["pct"], "venter": st[i.id]["venter"]} for i in items}})
    weak = await weakest(db, [m["user_id"] for m in members])
    return {"punkter": [{"id": i.id, "title": i.title or str(i.id), "kind": i.kind, "required": i.required} for i in items], "deltagere": rows, "svageste": weak}


async def weakest(db: AsyncSession, user_ids: list[int]) -> dict:
    if not user_ids:
        return {"arter": [], "aktiviteter": []}
    arter = (await db.execute(text("""SELECT a.artnr, s.navn, count(*) AS n, round(100.0 * avg((NOT a.correct)::int)) AS fejl FROM lr_answers a JOIN species s ON s.artnr = a.artnr
        WHERE a.user_id = ANY(:u) AND a.artnr IS NOT NULL AND a.created_at > now() - interval '90 days' GROUP BY a.artnr, s.navn HAVING count(*) >= 3 ORDER BY avg((NOT a.correct)::int) DESC, count(*) DESC LIMIT 8"""), {"u": user_ids})).mappings().all()
    akt = (await db.execute(text("""SELECT a.question_id, left(q.prompt, 90) AS prompt, count(*) AS n, round(100.0 * avg((NOT a.correct)::int)) AS fejl FROM lr_answers a JOIN questions q ON q.id = a.question_id
        WHERE a.user_id = ANY(:u) AND a.created_at > now() - interval '90 days' GROUP BY a.question_id, q.prompt HAVING count(*) >= 3 ORDER BY avg((NOT a.correct)::int) DESC, count(*) DESC LIMIT 8"""), {"u": user_ids})).mappings().all()
    return {"arter": [dict(r) for r in arter], "aktiviteter": [dict(r) for r in akt]}


def csv_matrix(m: dict) -> str:
    import csv
    import io

    from portal.services.sheets import csv_cell

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Deltager"] + [csv_cell(p["title"]) for p in m["punkter"]])
    for r in m["deltagere"]:
        # Navnet vælger deltageren selv (PATCH /api/me): csv_cell mod CSV injection
        w.writerow([csv_cell(r["navn"])] + [("ja" if r["punkter"][str(p["id"])]["done"] else ("venter" if r["punkter"][str(p["id"])]["venter"] else "")) + (f" ({r['punkter'][str(p['id'])]['pct']} %)" if r["punkter"][str(p["id"])]["pct"] is not None else "") for p in m["punkter"]])
    return buf.getvalue()


async def approve_field(db: AsyncSession, cohort: LrCohort, item_id: int, user_id: int, ok: bool) -> None:
    row = await db.get(LrFieldDone, (user_id, item_id, cohort.id))
    if row is None:
        raise PathError(404, "Ingen indsendelse")
    if ok:
        row.approved, row.how = True, "underviser"
        await db.commit()
        u, p = await db.get(User, user_id), await db.get(LrPath, cohort.path_id)
        await check_completion(db, u, p)
    else:
        await db.delete(row)
        await db.commit()


async def pending_field(db: AsyncSession, cohort: LrCohort) -> list[dict]:
    rows = (await db.execute(text("""SELECT d.user_id, d.path_item_id, d.obsid, d.done_at, u.navn, i.title FROM lr_field_done d JOIN lr_path_items i ON i.id = d.path_item_id JOIN users u ON u.id = d.user_id
        JOIN lr_cohort_members m ON m.user_id = d.user_id AND m.cohort_id = :c WHERE d.cohort_id = :c AND NOT d.approved ORDER BY d.done_at"""), {"c": cohort.id})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if r["obsid"]:
            async with ObsSession() as o:
                obs = (await dataset.execute(o, "SELECT o.dato, s.navn, l.navn AS sted FROM observations o JOIN species s ON s.artnr = o.artnr LEFT JOIN locations l ON l.loknr = o.loknr WHERE o.obsid = :i AND o.hemmelig = 0", {"i": r["obsid"]})).mappings().first()
            d["obs"] = dict(obs) if obs else {"skjult": True}  # hemmelige observationer vises ikke med sted for underviseren
        out.append(d)
    return out


# ---------------------------------------------------------------- bygger: forløb

def clean_unlock(u: dict | None) -> dict:
    u = u or {}
    after = u.get("after", "none")
    if isinstance(after, list):
        after = [int(x) for x in after if str(x).isdigit()][:20]
    elif after not in ("none", "previous"):
        after = "none"
    out: dict = {"after": after}
    if u.get("days_after_start") not in (None, ""):
        out["days_after_start"] = max(0, min(365, int(u["days_after_start"])))
    return out


def clean_field_task(t: dict | None) -> dict | None:
    if not t:
        return None
    r = t.get("regel") or {}
    return {"titel": str(t.get("titel", ""))[:160], "tekst_md": str(t.get("tekst_md", ""))[:5000], "godkendelse": t.get("godkendelse") if t.get("godkendelse") in ("auto", "underviser", "selv") else "auto",
            "regel": {"arter": [int(x) for x in r.get("arter") or []][:100], "gruppe": r.get("gruppe") or None, "min_arter": max(1, min(50, int(r.get("min_arter") or 1))),
                      "loknr": int(r["loknr"]) if r.get("loknr") else None, "afdeling_id": int(r["afdeling_id"]) if r.get("afdeling_id") else None,
                      "periode": r.get("periode") if isinstance(r.get("periode"), dict) else "efter_oplaasning"}}


async def path_warnings(db: AsyncSession, p: LrPath) -> list[dict]:
    items = await load_items(db, p.id)
    out = []
    if not items:
        out.append({"level": "fejl", "msg": "Forløbet har ingen punkter"})
    if items and not any(i.required for i in items):
        out.append({"level": "advarsel", "msg": "Ingen punkter er krævede – forløbet kan aldrig gennemføres"})
    ids = {i.id for i in items}
    for i in items:
        if i.kind in ("kursus", "ovelse"):
            c = await db.get(Course, i.course_id) if i.course_id else None
            if c is None:
                out.append({"level": "fejl", "msg": f"«{i.title or i.id}» peger ikke på et kursus", "item": i.id})
            elif not c.published:
                out.append({"level": "fejl", "msg": f"«{c.title}» er ikke publiceret", "item": i.id})
        if i.kind == "felt" and not (i.field_task or {}).get("titel"):
            out.append({"level": "fejl", "msg": "Feltopgaven mangler en titel", "item": i.id})
        if i.kind == "tekst" and not (i.text_md or "").strip():
            out.append({"level": "advarsel", "msg": "Tomt tekstpunkt", "item": i.id})
        after = (i.unlock or {}).get("after")
        if isinstance(after, list) and any(x not in ids or x == i.id for x in after):
            out.append({"level": "fejl", "msg": f"Oplåsningen af «{i.title or i.id}» peger på et ukendt punkt", "item": i.id})
    graph = {i.id: [x for x in ((i.unlock or {}).get("after") if isinstance((i.unlock or {}).get("after"), list) else []) if x in ids] for i in items}
    seen, stack = set(), set()

    def cyc(n):
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n), stack.add(n)
        r = any(cyc(m) for m in graph[n])
        stack.discard(n)
        return r

    if any(cyc(n) for n in graph):
        out.append({"level": "fejl", "msg": "Oplåsningsreglerne danner en løkke"})
    return out
