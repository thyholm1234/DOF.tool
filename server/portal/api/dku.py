"""DKU-modulet: tip om tvivlsomme observationer, sager, beskeder og DKU-ansvarlige pr. lokalafdeling (se services/dku.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import kv
from portal.db import SessionLocal, get_session
from portal.models import DkuCase, DkuMessage, DkuParticipant, User
from portal.services import dku
from portal.timeutil import dk_today

router = APIRouter(prefix="/api/dku", tags=["dku"])


async def ctx(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> dict:
    c = await dku.context(db, user)
    if not c["visible"]:
        raise HTTPException(404, "DKU-modulet er ikke slået til")
    return c


async def _case(db: AsyncSession, case_id: int, user: User) -> tuple[DkuCase, str]:
    case = await db.get(DkuCase, case_id)
    role = await dku.role_in(db, case, user) if case else None
    if case is None or role is None:
        raise HTTPException(404, "Sagen findes ikke")
    return case, role


async def _say(db: AsyncSession, case: DkuCase, user_id: int | None, role: str, body: str, visibility: str = "alle") -> None:
    db.add(DkuMessage(case_id=case.id, user_id=user_id, role=role, visibility=visibility, body=body))
    case.updated_at = datetime.now(timezone.utc)


async def _dofbasen_status(obsid: int, status: str | None, user_id: int, case_id: int) -> None:
    """DKU-status på en DOFbasen-observation (DOFbasen-modulets blå pil). Egen session: tabellen ligger i observationsgruppen.
    status None fjerner kun «til behandling» – en status sat af DKU eller SU i forvejen røres ikke."""
    async with SessionLocal() as s:
        if status is None:
            await s.execute(text("DELETE FROM dofb_obs_status WHERE obsid = :o AND status = 'behandling'"), {"o": obsid})
        elif status == "behandling":
            await s.execute(text("""INSERT INTO dofb_obs_status (obsid, status, note, set_by) VALUES (:o, 'behandling', :n, :u)
                ON CONFLICT (obsid) DO NOTHING"""), {"o": obsid, "n": f"DKU-sag #{case_id}", "u": user_id})
        else:
            await s.execute(text("""INSERT INTO dofb_obs_status (obsid, status, note, set_by) VALUES (:o, :s, :n, :u)
                ON CONFLICT (obsid) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, set_by = EXCLUDED.set_by,
                set_at = now() WHERE dofb_obs_status.status IN ('behandling', 'dku')"""),
                {"o": obsid, "s": status, "n": f"DKU-sag #{case_id}", "u": user_id})
        await s.commit()


# ---------------------------------------------------------------- status og liste

@router.get("/status")
async def status(c: dict = Depends(ctx)):
    return c


class TipIn(BaseModel):
    source: str = Field(pattern="^(dofbasen|portal)$")
    id: int
    reason: str = Field(pattern="^(dokumentation|usandsynlig|fejl|andet)$")
    body: str = Field(min_length=10, max_length=4000)
    visibility: str = Field("alle", pattern="^(alle|dku)$")


@router.post("/cases")
async def tip(body: TipIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
              db: AsyncSession = Depends(get_session)):
    obs = await dku.observation(db, body.source, body.id)
    own = obs is not None and (obs["obserkode"] == user.obserkode or obs["owner_user_id"] == user.id)
    if obs is None or (obs["hemmelig"] and not own):
        raise HTTPException(404, "Observationen findes ikke")
    if own:
        raise HTTPException(400, "Det er din egen observation – ret den selv i stedet")
    key = f"dku:tips:{user.id}:{dk_today()}"
    n = await kv().incr(key)
    await kv().expire(key, 86400)
    if n > dku.TIPS_PER_DAY:
        raise HTTPException(429, f"Du kan højst sende {dku.TIPS_PER_DAY} tip om dagen")
    ident = "obsid" if body.source == "dofbasen" else "report_id"
    existing = (await db.execute(text(f"""SELECT id FROM dku_cases WHERE source = :s AND {ident} = :i AND status <> 'afgjort'
        ORDER BY id LIMIT 1"""), {"s": body.source, "i": body.id})).scalar()
    if existing:
        case = await db.get(DkuCase, existing)
        joined = True
    else:
        afd = obs["afdelinger"]
        if not afd and obs["loknr"]:
            afd = [a for a in [(await db.execute(text("SELECT afdeling_id FROM locations WHERE loknr = :l"),
                                                  {"l": obs["loknr"]})).scalar()] if a]
        observer_id = obs["owner_user_id"] or (await db.execute(text(
            "SELECT id FROM users WHERE obserkode = :k AND NOT blocked"), {"k": obs["obserkode"]})).scalar()
        case = DkuCase(source=body.source, obsid=obs["obsid"], report_id=obs["report_id"], obserkode=obs["obserkode"],
                       observer_id=observer_id, artnr=obs["artnr"], dato=datetime.fromisoformat(obs["dato"]).date(),
                       loknr=obs["loknr"], afdelinger=afd, snapshot=obs, reason=body.reason, created_by=user.id)
        db.add(case)
        await db.flush()
        if observer_id:
            db.add(DkuParticipant(case_id=case.id, user_id=observer_id, role="observer"))
        joined = False
    await db.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, 'tipper', now())
        ON CONFLICT (case_id, user_id) DO NOTHING"""), {"c": case.id, "u": user.id})
    prefix = "" if not joined else f"Endnu et tip ({dku.REASONS[body.reason].lower()}):\n"
    await _say(db, case, user.id, "tipper", prefix + body.body.strip(), body.visibility)
    await db.commit()
    fields = obs["fields"]
    tasks.add_task(dku.notify, case.id, to="dku", title="nyt tip" if not joined else "endnu et tip", exclude=user.id,
                   body=f"{dku.REASONS[body.reason]}: {fields['antal']} {fields['art']}, {fields['lok']} {fields['dato']}")
    return {"id": case.id, "joined": joined}


@router.get("/cases")
async def cases(view: str = Query("mine", pattern="^(mine|observer|dku)$"), state: str = Query("open", pattern="^(open|closed|all)$"),
                c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    afd = set(c["member_of"])
    if view == "dku":
        if not (afd or c["admin"]):
            raise HTTPException(403, "Kun for DKU-ansvarlige")
        where = dku._dku_scope_sql(afd, c["admin"])
    elif view == "mine":
        where = "EXISTS (SELECT 1 FROM dku_participants x WHERE x.case_id = c.id AND x.user_id = :u AND x.role = 'tipper')"
    else:
        where = ("c.observer_involved AND EXISTS (SELECT 1 FROM dku_participants x WHERE x.case_id = c.id AND x.user_id = :u "
                 "AND x.role = 'observer')")
    st = {"open": "c.status <> 'afgjort'", "closed": "c.status = 'afgjort'", "all": "TRUE"}[state]
    # Ulæst: beskeder efter seneste læsning, som brugeren må se (DKU ser alt, andre kun beskeder til alle)
    vis = "TRUE" if view == "dku" else "m.visibility = 'alle'"
    rows = (await db.execute(text(f"""
        SELECT c.id, c.source, c.status, c.outcome, c.reason, c.dato, c.observer_involved, c.updated_at, c.created_at,
               c.snapshot->'fields' AS f, c.snapshot->>'observer' AS observer, c.afdelinger, a.navn AS assignee,
               (SELECT count(*) FROM dku_participants x WHERE x.case_id = c.id AND x.role = 'tipper') AS n_tippers,
               EXISTS (SELECT 1 FROM dku_messages m LEFT JOIN dku_participants p ON p.case_id = c.id AND p.user_id = :u
                       WHERE m.case_id = c.id AND m.user_id IS DISTINCT FROM :u AND {vis}
                         AND m.created_at > coalesce(p.last_read_at, 'epoch')) AS unread
        FROM dku_cases c LEFT JOIN users a ON a.id = c.assignee_id
        WHERE {where} AND {st} ORDER BY c.updated_at DESC LIMIT 300"""), {"u": user.id, "afd": list(afd)})).mappings().all()
    names = dict((await db.execute(text("SELECT id, navn FROM afdelinger"))).all())
    return [{"id": r["id"], "source": r["source"], "status": r["status"], "status_label": dku.STATUSES[r["status"]],
             "outcome": r["outcome"], "outcome_label": dku.OUTCOMES.get(r["outcome"] or ""), "reason": dku.REASONS[r["reason"]],
             "art": r["f"].get("art"), "antal": r["f"].get("antal"), "lok": r["f"].get("lok"), "dato": r["dato"].isoformat(),
             "observer": r["observer"], "afdelinger": [names.get(a, str(a)) for a in r["afdelinger"] or []],
             "assignee": r["assignee"], "n_tippers": r["n_tippers"], "observer_involved": r["observer_involved"],
             "unread": r["unread"], "updated_at": r["updated_at"].isoformat()} for r in rows]


# ---------------------------------------------------------------- én sag

def _author(m, role: str, names: dict[int, str]) -> str:
    if m["role"] == "system":
        return "DOF.tool"
    name = names.get(m["user_id"]) or "Slettet bruger"
    if m["role"] == "tipper" and role == "observer":
        return "Tipper"  # tipperen er anonym for observatøren
    return {"dku": f"{name} (DKU)", "tipper": f"{name} (tipper)", "observer": f"{name} (observatør)"}.get(m["role"], name)


@router.get("/cases/{case_id}")
async def get_case(case_id: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    snap = case.snapshot
    ident = case.obsid if case.source == "dofbasen" else case.report_id
    current = await dku.observation(db, case.source, ident) if ident else None
    msgs = (await db.execute(text("""SELECT id, user_id, role, visibility, body, created_at FROM dku_messages
        WHERE case_id = :c AND (:dku OR visibility = 'alle' OR user_id = :u) ORDER BY created_at, id"""),
        {"c": case.id, "dku": role == "dku", "u": user.id})).mappings().all()
    ids = {m["user_id"] for m in msgs if m["user_id"]} | {case.assignee_id, case.observer_id} - {None}
    names = dict((await db.execute(text("SELECT id, coalesce(navn, obserkode) FROM users WHERE id = ANY(:i)"),
                                   {"i": list(ids)})).all()) if ids else {}
    tippers = (await db.execute(text("""SELECT u.id, coalesce(u.navn, u.obserkode) FROM dku_participants p JOIN users u ON u.id = p.user_id
        WHERE p.case_id = :c AND p.role = 'tipper'"""), {"c": case.id})).all() if role == "dku" else []
    # Læst: DKU-ansvarlige, der ikke er part i sagen, får en række med rollen dku
    await db.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, :r, now())
        ON CONFLICT (case_id, user_id) DO UPDATE SET last_read_at = now()"""), {"c": case.id, "u": user.id, "r": role})
    await db.commit()
    is_owner = user.id == case.observer_id or (snap.get("obserkode") and snap.get("obserkode") == user.obserkode)
    closed = case.status == "afgjort"
    afd = dict((await db.execute(text("SELECT id, navn FROM afdelinger"))).all())
    return {
        "id": case.id, "source": case.source, "role": role, "status": case.status, "status_label": dku.STATUSES[case.status],
        "outcome": case.outcome, "outcome_label": dku.OUTCOMES.get(case.outcome or ""), "outcome_note": case.outcome_note,
        "reason": case.reason, "reason_label": dku.REASONS[case.reason], "observer_involved": case.observer_involved,
        "created_at": case.created_at.isoformat(), "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "assignee": names.get(case.assignee_id), "afdelinger": [afd.get(a, str(a)) for a in case.afdelinger or []],
        "observer": {"name": snap.get("observer"), "obserkode": snap.get("obserkode"), "in_portal": bool(case.observer_id)},
        "tippers": [n for _, n in tippers],
        "fields": [{"key": k, "label": label, "value": snap["fields"].get(k, "")} for k, label in dku.FIELDS if snap["fields"].get(k)],
        "current": [{"key": k, "label": label, "value": current["fields"].get(k, "")} for k, label in dku.FIELDS
                    if current["fields"].get(k)] if current else None,
        "changes": dku.changes(snap["fields"], current["fields"]) if current else [],
        "deleted": current is None,
        "media": (current or snap).get("media") or [],
        "links": {"dofbasen": snap.get("url"), "thread": snap.get("thread"),
                  "edit": (current or snap).get("edit") if is_owner and case.source == "portal" and not closed else None,
                  "edit_dofbasen": snap.get("url") if is_owner and case.source == "dofbasen" and not closed else None},
        "messages": [{"id": m["id"], "role": m["role"], "author": _author(m, role, names), "mine": m["user_id"] == user.id,
                      "visibility": m["visibility"], "body": m["body"], "created_at": m["created_at"].isoformat()} for m in msgs],
        "can": {"manage": role == "dku", "message": role == "dku" or not closed},
    }


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    visibility: str = Field("alle", pattern="^(alle|dku)$")


@router.post("/cases/{case_id}/messages")
async def post_message(case_id: int, body: MessageIn, tasks: BackgroundTasks, c: dict = Depends(ctx),
                       user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    if case.status == "afgjort" and role != "dku":
        raise HTTPException(400, "Sagen er afgjort – DKU kan genåbne den")
    key = f"dku:msg:{user.id}"
    if not await kv().set(key, "1", nx=True, ex=5):
        raise HTTPException(429, "Vent et øjeblik før næste besked")
    await _say(db, case, user.id, role, body.body.strip(), body.visibility)
    await db.execute(text("UPDATE dku_participants SET last_read_at = now() WHERE case_id = :c AND user_id = :u"),
                     {"c": case.id, "u": user.id})
    await db.commit()
    preview = body.body.strip()[:200]
    if body.visibility == "dku":
        tasks.add_task(dku.notify, case.id, to="dku", title="ny besked (kun DKU)", body=preview, exclude=user.id)
    elif role == "dku":
        tasks.add_task(dku.notify, case.id, to="parter", title="ny besked fra DKU", body=preview, exclude=user.id)
    else:
        tasks.add_task(dku.notify, case.id, to="alle", title="ny besked", body=preview, exclude=user.id)
    return {"ok": True}


class ActionIn(BaseModel):
    action: str = Field(pattern="^(take|involve|close|reopen)$")
    outcome: str | None = Field(None, pattern="^(godkendt|rettet|afvist|henlagt)$")
    note: str = Field("", max_length=4000)


@router.post("/cases/{case_id}/actions")
async def action(case_id: int, body: ActionIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
                 db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    if role != "dku":
        raise HTTPException(403, "Kun DKU kan behandle sagen")
    who = user.navn or user.obserkode
    note = body.note.strip()
    now = datetime.now(timezone.utc)
    if body.action == "take":
        if case.status == "afgjort":
            raise HTTPException(400, "Sagen er afgjort")
        case.status, case.assignee_id = ("behandles" if case.status == "ny" else case.status), user.id
        await _say(db, case, None, "system", f"{who} har taget sagen.", "dku")
        if note:
            await _say(db, case, user.id, "dku", note, "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="tipper", title="DKU behandler dit tip", body=f"{who} har taget sagen.")
    elif body.action == "involve":
        if not case.observer_id:
            raise HTTPException(400, "Observatøren bruger ikke DOF.tool – kontakt vedkommende via DOFbasen")
        if case.status == "afgjort":
            raise HTTPException(400, "Sagen er afgjort")
        if len(note) < 10:
            raise HTTPException(400, "Skriv til observatøren, hvad DKU er i tvivl om (mindst 10 tegn)")
        case.status, case.observer_involved = "afventer", True
        case.assignee_id = case.assignee_id or user.id
        await _say(db, case, None, "system", "DKU har inddraget observatøren.", "alle")
        await _say(db, case, user.id, "dku", note, "alle")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="observer", title="spørgsmål til din observation", body=note[:200])
        tasks.add_task(dku.notify, case.id, to="tipper", title="observatøren er inddraget", body="DKU har kontaktet observatøren.",
                       key="involve")
    elif body.action == "close":
        if not body.outcome:
            raise HTTPException(400, "Vælg en afgørelse")
        case.status, case.outcome, case.outcome_note, case.closed_at = "afgjort", body.outcome, note or None, now
        case.assignee_id = case.assignee_id or user.id
        await _say(db, case, None, "system", f"Afgjort af {who}: {dku.OUTCOMES[body.outcome]}." + (f"\n{note}" if note else ""), "alle")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "dku" if body.outcome in ("godkendt", "rettet") else None, user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="parter", title="sagen er afgjort", body=dku.OUTCOMES[body.outcome], key="close")
    else:  # reopen
        if case.status != "afgjort":
            raise HTTPException(400, "Sagen er ikke afgjort")
        case.status, case.outcome, case.outcome_note, case.closed_at = ("afventer" if case.observer_involved else "behandles"), None, None, None
        await _say(db, case, None, "system", f"{who} har genåbnet sagen.", "alle")
        if note:
            await _say(db, case, user.id, "dku", note, "alle")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="parter", title="sagen er genåbnet", body=note[:200] or "DKU har genåbnet sagen.")
    return {"ok": True, "status": case.status}


# ---------------------------------------------------------------- DKU-ansvarlige pr. lokalafdeling

@router.get("/members")
async def members(c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    if not (c["admin"] or c["member_of"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige og administratorer")
    afd = [{"id": 0, "navn": "Landsdækkende (alle afdelinger)"}] + [
        {"id": i, "navn": n} for i, n in (await db.execute(text("SELECT id, navn FROM afdelinger ORDER BY navn"))).all()]
    rows = (await db.execute(text("""SELECT m.afdeling_id, u.id, coalesce(u.navn, u.obserkode) AS navn, u.obserkode
        FROM dku_members m JOIN users u ON u.id = m.user_id ORDER BY navn"""))).mappings().all()
    by = {}
    for r in rows:
        by.setdefault(r["afdeling_id"], []).append({"user_id": r["id"], "navn": r["navn"], "obserkode": r["obserkode"]})
    return {"can_edit": c["admin"], "afdelinger": [{**a, "members": by.get(a["id"], [])} for a in afd]}


class MemberIn(BaseModel):
    afdeling_id: int = Field(ge=0)
    obserkode: str = Field(min_length=2, max_length=16)


@router.post("/members")
async def add_member(body: MemberIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not c["admin"]:
        raise HTTPException(403, "Kræver admin-modulet DKU")
    if body.afdeling_id and not (await db.execute(text("SELECT 1 FROM afdelinger WHERE id = :a"), {"a": body.afdeling_id})).scalar():
        raise HTTPException(404, "Ukendt lokalafdeling")
    target = (await db.execute(text("SELECT id FROM users WHERE obserkode = :k AND approved AND NOT blocked"),
                               {"k": body.obserkode.strip().upper()})).scalar()
    if not target:
        raise HTTPException(404, "Ingen godkendt bruger med den obserkode i DOF.tool")
    await db.execute(text("""INSERT INTO dku_members (afdeling_id, user_id, added_by) VALUES (:a, :u, :by)
        ON CONFLICT DO NOTHING"""), {"a": body.afdeling_id, "u": target, "by": user.id})
    await db.commit()
    return {"ok": True}


@router.delete("/members/{afdeling_id}/{user_id}")
async def remove_member(afdeling_id: int, user_id: int, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    if not c["admin"]:
        raise HTTPException(403, "Kræver admin-modulet DKU")
    await db.execute(text("DELETE FROM dku_members WHERE afdeling_id = :a AND user_id = :u"), {"a": afdeling_id, "u": user_id})
    await db.commit()
    return {"ok": True}
