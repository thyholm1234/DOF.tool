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
    observer_note: str = Field("", max_length=4000)  # kun DKU: skriv til observatøren med det samme


async def _involve(db: AsyncSession, case: DkuCase, user: User, note: str) -> None:
    """Inddrag observatøren: sagen bliver synlig for vedkommende, og DKU's besked lægges i observatørens kanal."""
    case.status, case.observer_involved = "afventer", True
    case.assignee_id = case.assignee_id or user.id
    await _say(db, case, None, "system", f"{user.navn or user.obserkode} har inddraget observatøren.", "dku")
    await _say(db, case, user.id, "dku", note, "observer")


@router.post("/cases")
async def open_case(body: TipIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
                    db: AsyncSession = Depends(get_session)):
    """Et tip fra en bruger – eller, når brugeren er DKU for observationens afdeling, en sag DKU selv åbner."""
    obs = await dku.observation(db, body.source, body.id)
    own = obs is not None and (obs["obserkode"] == user.obserkode or obs["owner_user_id"] == user.id)
    if obs is None or (obs["hemmelig"] and not own):
        raise HTTPException(404, "Observationen findes ikke")
    if own:
        raise HTTPException(400, "Det er din egen observation – ret den selv i stedet")
    afd = obs["afdelinger"]
    if not afd and obs["loknr"]:
        afd = [a for a in [(await db.execute(text("SELECT afdeling_id FROM locations WHERE loknr = :l"),
                                              {"l": obs["loknr"]})).scalar()] if a]
    as_dku = await dku.is_dku_for(db, user, afd)
    note = body.observer_note.strip()
    if note and not as_dku:
        raise HTTPException(403, "Kun DKU kan skrive til observatøren")
    if not as_dku:
        key = f"dku:tips:{user.id}:{dk_today()}"
        n = await kv().incr(key)
        await kv().expire(key, 86400)
        if n > dku.TIPS_PER_DAY:
            raise HTTPException(429, f"Du kan højst sende {dku.TIPS_PER_DAY} tip om dagen")
    ident = "obsid" if body.source == "dofbasen" else "report_id"
    existing = (await db.execute(text(f"""SELECT id FROM dku_cases WHERE source = :s AND {ident} = :i AND status <> 'afgjort'
        ORDER BY id LIMIT 1"""), {"s": body.source, "i": body.id})).scalar()
    if existing:
        case, joined = await db.get(DkuCase, existing), True
    else:
        observer_id = obs["owner_user_id"] or (await db.execute(text(
            "SELECT id FROM users WHERE obserkode = :k AND NOT blocked"), {"k": obs["obserkode"]})).scalar()
        case = DkuCase(source=body.source, obsid=obs["obsid"], report_id=obs["report_id"], obserkode=obs["obserkode"],
                       observer_id=observer_id, artnr=obs["artnr"], dato=datetime.fromisoformat(obs["dato"]).date(),
                       loknr=obs["loknr"], afdelinger=afd, snapshot=obs, reason=body.reason, created_by=user.id,
                       origin="dku" if as_dku else "tip", status="behandles" if as_dku else "ny",
                       assignee_id=user.id if as_dku else None)
        db.add(case)
        await db.flush()
        if observer_id:
            db.add(DkuParticipant(case_id=case.id, user_id=observer_id, role="observer"))
        joined = False
    reason = dku.REASONS[body.reason]
    if as_dku:
        await _say(db, case, user.id, "dku", f"{reason}:\n{body.body.strip()}" if not joined else body.body.strip(), "dku")
        if note and case.status != "afgjort":
            if not case.observer_id:
                raise HTTPException(400, "Observatøren bruger ikke DOF.tool – kontakt vedkommende via DOFbasen")
            await _involve(db, case, user, note)
    else:
        await db.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, 'tipper', now())
            ON CONFLICT (case_id, user_id) DO NOTHING"""), {"c": case.id, "u": user.id})
        await _say(db, case, user.id, "tipper", (f"Endnu et tip ({reason.lower()}):\n" if joined else "") + body.body.strip(), "tipper")
    await db.commit()
    if as_dku and case.source == "dofbasen" and case.obsid:
        await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
    if note:
        tasks.add_task(dku.notify, case.id, to="observer", title="spørgsmål til din observation", body=note[:200])
    if not as_dku:
        f = obs["fields"]
        tasks.add_task(dku.notify, case.id, to="dku", title="nyt tip" if not joined else "endnu et tip", exclude=user.id,
                       body=f"{reason}: {f['antal']} {f['art']}, {f['lok']} {f['dato']}")
    return {"id": case.id, "joined": joined, "origin": "dku" if as_dku else "tip"}


@router.get("/cases")
async def cases(view: str = Query("mine", pattern="^(mine|observer|dku)$"), state: str = Query("open", pattern="^(open|closed|all)$"),
                c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    afd = set(c["member_of"])
    if view == "dku":
        if not (afd or c["admin"]):
            raise HTTPException(403, "Kun for DKU-ansvarlige")
        where, role = dku._dku_scope_sql(afd, c["admin"]), "dku"
    elif view == "mine":
        where, role = "EXISTS (SELECT 1 FROM dku_participants x WHERE x.case_id = c.id AND x.user_id = :u AND x.role = 'tipper')", "tipper"
    else:
        where, role = ("c.observer_involved AND EXISTS (SELECT 1 FROM dku_participants x WHERE x.case_id = c.id AND x.user_id = :u "
                       "AND x.role = 'observer')"), "observer"
    st = {"open": "c.status <> 'afgjort'", "closed": "c.status = 'afgjort'", "all": "TRUE"}[state]
    rows = (await db.execute(text(f"""
        SELECT c.id, c.source, c.origin, c.status, c.outcome, c.reason, c.dato, c.observer_involved, c.updated_at,
               c.snapshot->'fields' AS f, c.snapshot->>'observer' AS observer, c.afdelinger, a.navn AS assignee,
               (SELECT count(*) FROM dku_participants x WHERE x.case_id = c.id AND x.role = 'tipper') AS n_tippers,
               EXISTS (SELECT 1 FROM dku_messages m LEFT JOIN dku_participants p ON p.case_id = c.id AND p.user_id = :u
                       WHERE m.case_id = c.id AND m.user_id IS DISTINCT FROM :u AND {dku.visible_sql(role)}
                         AND m.created_at > coalesce(p.last_read_at, 'epoch')) AS unread
        FROM dku_cases c LEFT JOIN users a ON a.id = c.assignee_id
        WHERE {where} AND {st} ORDER BY c.updated_at DESC LIMIT 300"""), {"u": user.id, "afd": list(afd)})).mappings().all()
    names = dict((await db.execute(text("SELECT id, navn FROM afdelinger"))).all())
    out = []
    for r in rows:
        item = {"id": r["id"], "source": r["source"], "status": r["status"], "status_label": dku.STATUSES[r["status"]],
                "outcome": r["outcome"], "outcome_label": dku.OUTCOMES.get(r["outcome"] or ""),
                "art": r["f"].get("art"), "antal": r["f"].get("antal"), "lok": r["f"].get("lok"), "dato": r["dato"].isoformat(),
                "unread": r["unread"], "updated_at": r["updated_at"].isoformat()}
        if role != "observer":  # observatøren ser hverken grunden eller hvordan sagen startede
            item |= {"reason": dku.REASONS[r["reason"]], "observer": r["observer"]}
        if role == "dku":
            item |= {"origin": r["origin"], "afdelinger": [names.get(a, str(a)) for a in r["afdelinger"] or []],
                     "assignee": r["assignee"], "n_tippers": r["n_tippers"], "observer_involved": r["observer_involved"]}
        out.append(item)
    return out


@router.get("/candidates")
async def candidates(days: int = Query(7, ge=1, le=30), kriterier: str = Query("antal,ny,saeson,dok"),
                     min_kriterier: int = Query(1, ge=1, le=4, alias="min"), niveau: str = Query("lok", pattern="^(lok|kommune)$"),
                     hist_years: int = Query(10, ge=1, le=30), antal_min: int = Query(5, ge=1, le=100000),
                     faktor: float = Query(2.0, ge=1, le=100), ny_max: int = Query(0, ge=0, le=1000),
                     saeson_dage: int = Query(14, ge=1, le=90), afdeling: int | None = Query(None, ge=0),
                     kun_bemaerk: bool = Query(False), vis_behandlede: bool = Query(False), c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    """Kandidater til DKU-sager: nylige observationer, der afviger fra det lokale. Kun for DKU-ansvarlige og administratorer."""
    member = set(c["member_of"])
    if not (member or c["admin"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige")
    crit = [k for k in dict.fromkeys(x.strip() for x in kriterier.split(",")) if k in dku.CRITERIA]
    if not crit:
        raise HTTPException(400, "Vælg mindst ét kriterium")
    scope = None if (c["admin"] or 0 in member) else member
    if afdeling:
        if scope is not None and afdeling not in scope:
            raise HTTPException(403, "Du er ikke DKU-ansvarlig for den afdeling")
        scope = {afdeling}
    rows = await dku.candidates(db, afd=scope, days=days, criteria=crit, min_match=min(min_kriterier, len(crit)), level=niveau,
                                hist_years=hist_years, antal_min=antal_min, antal_factor=faktor, ny_max=ny_max,
                                season_days=saeson_dage, only_notable=kun_bemaerk, include_reviewed=vis_behandlede)
    names = dict((await db.execute(text("SELECT id, navn FROM afdelinger"))).all())
    for r in rows:
        r["afdelinger_navne"] = [names.get(a, str(a)) for a in r["afdelinger"]]
    return {"criteria": {k: dku.CRITERIA[k] for k in crit}, "rows": rows}


class ReviewIn(BaseModel):
    obsids: list[int] = Field(min_length=1, max_length=200)
    note: str = Field("", max_length=1000)


@router.post("/review")
async def approve(body: ReviewIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Godkend kandidater: observationen er set igennem af den ansvarlige og kræver ingen sag. Kun for DKU på observationens afdeling."""
    if not (c["member_of"] or c["admin"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige")
    done = 0
    for obsid in dict.fromkeys(body.obsids):
        obs = await dku.observation(db, "dofbasen", obsid)
        if obs is None or obs["hemmelig"] or not await dku.is_dku_for(db, user, obs["afdelinger"]):
            continue
        res = await db.execute(text("""INSERT INTO dku_reviews (obsid, decision, note, afdelinger, reviewed_by)
            VALUES (:o, 'godkendt', :n, CAST(:a AS smallint[]), :u) ON CONFLICT (obsid) DO NOTHING"""),
                               {"o": obsid, "n": body.note.strip(), "a": obs["afdelinger"], "u": user.id})
        done += res.rowcount
    await db.commit()
    return {"approved": done}


@router.delete("/review/{obsid}")
async def unapprove(obsid: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Fortryd en godkendelse, så observationen kommer tilbage i kandidatlisten."""
    obs = await dku.observation(db, "dofbasen", obsid)
    if obs is None or not await dku.is_dku_for(db, user, obs["afdelinger"]):
        raise HTTPException(404, "Observationen findes ikke")
    await db.execute(text("DELETE FROM dku_reviews WHERE obsid = :o"), {"o": obsid})
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- én sag

CHANNEL_LABEL = {"dku": "intern note", "tipper": "til tipperen", "observer": "til observatøren"}


def _author(m, role: str, names: dict[int, str]) -> str:
    if m["role"] == "system":
        return "DOF.tool"
    name = names.get(m["user_id"]) or "Slettet bruger"
    return {"dku": f"{name} (DKU)", "tipper": f"{name} (tipper)", "observer": f"{name} (observatør)"}.get(m["role"], name)


@router.get("/cases/{case_id}")
async def get_case(case_id: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    snap = case.snapshot
    ident = case.obsid if case.source == "dofbasen" else case.report_id
    current = await dku.observation(db, case.source, ident) if ident else None
    msgs = (await db.execute(text(f"""SELECT m.id, m.user_id, m.role, m.visibility, m.body, m.created_at FROM dku_messages m
        WHERE m.case_id = :c AND {dku.visible_sql(role)} ORDER BY m.created_at, m.id"""), {"c": case.id, "u": user.id})).mappings().all()
    ids = {m["user_id"] for m in msgs if m["user_id"]} | {case.assignee_id, case.observer_id} - {None}
    names = dict((await db.execute(text("SELECT id, coalesce(navn, obserkode) FROM users WHERE id = ANY(:i)"),
                                   {"i": list(ids)})).all()) if ids else {}
    tippers = [n for _, n in (await db.execute(text("""SELECT u.id, coalesce(u.navn, u.obserkode) FROM dku_participants p
        JOIN users u ON u.id = p.user_id WHERE p.case_id = :c AND p.role = 'tipper'"""), {"c": case.id})).all()]
    await db.execute(text("""INSERT INTO dku_participants (case_id, user_id, role, last_read_at) VALUES (:c, :u, :r, now())
        ON CONFLICT (case_id, user_id) DO UPDATE SET last_read_at = now()"""), {"c": case.id, "u": user.id, "r": role})
    await db.commit()
    is_owner = user.id == case.observer_id or (snap.get("obserkode") and snap.get("obserkode") == user.obserkode)
    closed = case.status == "afgjort"
    afd = dict((await db.execute(text("SELECT id, navn FROM afdelinger"))).all())
    out = {
        "id": case.id, "source": case.source, "role": role, "status": case.status, "status_label": dku.STATUSES[case.status],
        "outcome": case.outcome, "outcome_label": dku.OUTCOMES.get(case.outcome or ""), "outcome_note": case.outcome_note,
        "observer_involved": case.observer_involved, "created_at": case.created_at.isoformat(),
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "observer": {"name": snap.get("observer"), "obserkode": snap.get("obserkode"), "in_portal": bool(case.observer_id)},
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
                      "visibility": m["visibility"], "channel": CHANNEL_LABEL.get(m["visibility"]) if role == "dku" else None,
                      "body": m["body"], "created_at": m["created_at"].isoformat()} for m in msgs],
        "channels": dku.channels(role, case.observer_involved, bool(tippers)) if role == "dku" or not closed else [],
        "can": {"manage": role == "dku"},
    }
    if role != "observer":  # observatøren ser hverken grunden, tipperne eller hvordan sagen startede
        out["reason_label"] = dku.REASONS[case.reason]
    if role == "dku":
        out |= {"origin": case.origin, "tippers": tippers, "assignee": names.get(case.assignee_id),
                "afdelinger": [afd.get(a, str(a)) for a in case.afdelinger or []]}
    return out


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    channel: str | None = Field(None, pattern="^(dku|tipper|observer)$")


@router.post("/cases/{case_id}/messages")
async def post_message(case_id: int, body: MessageIn, tasks: BackgroundTasks, c: dict = Depends(ctx),
                       user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    case, role = await _case(db, case_id, user)
    if case.status == "afgjort" and role != "dku":
        raise HTTPException(400, "Sagen er afgjort – DKU kan genåbne den")
    has_tippers = bool(await dku.participants(db, case.id, "tipper"))
    allowed = dku.channels(role, case.observer_involved, has_tippers)
    channel = body.channel or allowed[0]
    if channel not in allowed:
        raise HTTPException(400, "Du kan ikke skrive i den kanal")
    if not await kv().set(f"dku:msg:{user.id}", "1", nx=True, ex=5):
        raise HTTPException(429, "Vent et øjeblik før næste besked")
    await _say(db, case, user.id, role, body.body.strip(), channel)
    await db.execute(text("UPDATE dku_participants SET last_read_at = now() WHERE case_id = :c AND user_id = :u"),
                     {"c": case.id, "u": user.id})
    await db.commit()
    preview = body.body.strip()[:200]
    if role != "dku":
        tasks.add_task(dku.notify, case.id, to="dku", title="ny besked", body=preview, exclude=user.id)
    elif channel == "dku":
        tasks.add_task(dku.notify, case.id, to="dku", title="intern note", body=preview, exclude=user.id)
    else:
        tasks.add_task(dku.notify, case.id, to=channel, title="ny besked fra DKU", body=preview)
    return {"ok": True}


class ActionIn(BaseModel):
    action: str = Field(pattern="^(take|involve|close|reopen)$")
    outcome: str | None = Field(None, pattern="^(godkendt|rettet|afvist|henlagt)$")
    note: str = Field("", max_length=4000)


@router.post("/cases/{case_id}/actions")
async def action(case_id: int, body: ActionIn, tasks: BackgroundTasks, c: dict = Depends(ctx), user: User = Depends(require_user),
                 db: AsyncSession = Depends(get_session)):
    """DKU's handlinger. Systembeskederne er interne; tipper og observatør ser status og afgørelse i sagens hoved."""
    case, role = await _case(db, case_id, user)
    if role != "dku":
        raise HTTPException(403, "Kun DKU kan behandle sagen")
    who = user.navn or user.obserkode
    note = body.note.strip()
    now = datetime.now(timezone.utc)
    has_tippers = bool(await dku.participants(db, case.id, "tipper"))
    if body.action in ("take", "involve") and case.status == "afgjort":
        raise HTTPException(400, "Sagen er afgjort")
    if body.action == "take":
        case.status, case.assignee_id = ("behandles" if case.status == "ny" else case.status), user.id
        await _say(db, case, None, "system", f"{who} har taget sagen.", "dku")
        if note:
            await _say(db, case, user.id, "dku", note, "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
        if has_tippers:
            tasks.add_task(dku.notify, case.id, to="tipper", title="DKU behandler dit tip", body="DKU har taget sagen.", key="take")
    elif body.action == "involve":
        if not case.observer_id:
            raise HTTPException(400, "Observatøren bruger ikke DOF.tool – kontakt vedkommende via DOFbasen")
        if len(note) < 10:
            raise HTTPException(400, "Skriv til observatøren, hvad DKU er i tvivl om (mindst 10 tegn)")
        await _involve(db, case, user, note)
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="observer", title="spørgsmål til din observation", body=note[:200])
    elif body.action == "close":
        if not body.outcome:
            raise HTTPException(400, "Vælg en afgørelse")
        case.status, case.outcome, case.outcome_note, case.closed_at = "afgjort", body.outcome, note or None, now
        case.assignee_id = case.assignee_id or user.id
        await _say(db, case, None, "system", f"Afgjort af {who}: {dku.OUTCOMES[body.outcome]}.", "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "dku" if body.outcome in ("godkendt", "rettet") else None, user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="parter", title="sagen er afgjort", body=dku.OUTCOMES[body.outcome], key="close")
    else:  # reopen
        if case.status != "afgjort":
            raise HTTPException(400, "Sagen er ikke afgjort")
        case.status, case.outcome, case.outcome_note, case.closed_at = ("afventer" if case.observer_involved else "behandles"), None, None, None
        await _say(db, case, None, "system", f"{who} har genåbnet sagen." + (f"\n{note}" if note else ""), "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await _dofbasen_status(case.obsid, "behandling", user.id, case.id)
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
