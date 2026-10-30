"""DKU-modulet: tip om tvivlsomme observationer, sager, beskeder og DKU-ansvarlige pr. lokalafdeling (se services/dku.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import kv
from portal.db import get_session
from portal.models import DkuCase, DkuMessage, DkuParticipant, User
from portal.services import dku, dku_candidates, dku_cases
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
        afd = [a for a in [await dku_cases.location_afdeling(db, obs["loknr"])] if a]
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
    existing = await dku_cases.open_case_id(db, body.source, body.id)
    if existing:
        case, joined = await db.get(DkuCase, existing), True
    else:
        observer_id = obs["owner_user_id"] or await dku_cases.observer_user_id(db, obs["obserkode"])
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
        await dku_cases.add_tipper(db, case.id, user.id)
        await _say(db, case, user.id, "tipper", (f"Endnu et tip ({reason.lower()}):\n" if joined else "") + body.body.strip(), "tipper")
    await db.commit()
    if as_dku and case.source == "dofbasen" and case.obsid:
        await dku_cases.dofbasen_status(case.obsid, "behandling", user.id, case.id)
    if note:
        tasks.add_task(dku.notify, case.id, to="observer", title="spørgsmål til din observation", body=note[:200])
    if not as_dku:
        f = obs["fields"]
        tasks.add_task(dku.notify, case.id, to="dku", title="nyt tip" if not joined else "endnu et tip", exclude=user.id,
                       body=f"{reason}: {f['antal']} {f['art']}, {f['lok']} {f['dato']}")
    return {"id": case.id, "joined": joined, "origin": "dku" if as_dku else "tip"}


@router.get("/cases")
async def cases(view: str = Query("observer", pattern="^(observer|dku)$"), state: str = Query("open", pattern="^(open|closed|all)$"),
                c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    afd = set(c["member_of"])
    if view == "dku" and not (afd or c["admin"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige")
    return await dku_cases.case_list(db, user.id, view, state, afd, c["admin"])


@router.get("/candidates")
async def candidates(days: int = Query(7, ge=1, le=30), kriterier: str = Query("antal,ny,saeson,dok"),
                     min_kriterier: int = Query(1, ge=1, le=4, alias="min"), niveau: str = Query("lok", pattern="^(lok|kommune)$"),
                     hist_years: int = Query(10, ge=1, le=30), antal_min: int = Query(5, ge=1, le=100000),
                     faktor: float = Query(2.0, ge=1, le=100), ny_max: int = Query(0, ge=0, le=1000),
                     saeson_dage: int = Query(14, ge=1, le=90), afdeling: int | None = Query(None, ge=0),
                     kun_bemaerk: bool = Query(False), vis_behandlede: bool = Query(False), c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    """Kandidater til DKU-sager: nylige observationer, der afviger fra det lokale. Kun for DKU-ansvarlige og administratorer."""
    scope = _candidate_scope(c, afdeling)
    crit = [k for k in dict.fromkeys(x.strip() for x in kriterier.split(",")) if k in dku.CRITERIA]
    if not crit:
        raise HTTPException(400, "Vælg mindst ét kriterium")
    rows = await dku.candidates(db, afd=scope, days=days, criteria=crit, min_match=min(min_kriterier, len(crit)), level=niveau,
                                hist_years=hist_years, antal_min=antal_min, antal_factor=faktor, ny_max=ny_max,
                                season_days=saeson_dage, only_notable=kun_bemaerk)
    return await _candidate_rows(db, await dku.annotate(db, rows, vis_behandlede))


def _candidate_scope(c: dict, afdeling: int | None) -> set[int] | None:
    """Afdelingerne en kandidatsøgning må dække (None = alle)."""
    member = set(c["member_of"])
    if not (member or c["admin"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige")
    scope = None if (c["admin"] or 0 in member) else member
    if afdeling:
        if scope is not None and afdeling not in scope:
            raise HTTPException(403, "Du er ikke DKU-ansvarlig for den afdeling")
        scope = {afdeling}
    return scope


async def _candidate_rows(db: AsyncSession, rows: list[dict]) -> dict:
    names = await dku_cases.afdeling_names(db)
    for r in rows:
        r["afdelinger_navne"] = [names.get(a, str(a)) for a in r["afdelinger"]]
    return {"criteria": dku.CRITERIA, "rows": rows}


class FilterIn(BaseModel):
    """Et kandidatfilter, som det sendes til workeren eller gemmes som det løbende filter (services/dku_candidates.py)."""
    kriterier: list[str] = Field(min_length=1, max_length=4)
    days: int = Field(7, ge=1, le=30)
    min: int = Field(1, ge=1, le=4)
    niveau: str = Field("lok", pattern="^(lok|kommune)$")
    hist_years: int = Field(10, ge=1, le=30)
    antal_min: int = Field(5, ge=1, le=100000)
    faktor: float = Field(2.0, ge=1, le=100)
    ny_max: int = Field(0, ge=0, le=1000)
    saeson_dage: int = Field(14, ge=1, le=90)
    kun_bemaerk: bool = False

    def clean(self) -> dict:
        crit = [k for k in dict.fromkeys(self.kriterier) if k in dku.CRITERIA]
        if not crit:
            raise HTTPException(400, "Vælg mindst ét kriterium")
        return {**self.model_dump(), "kriterier": crit, "min": min(self.min, len(crit))}


class StandingIn(FilterIn):
    on: bool = True


@router.get("/candidates/standing")
async def standing(afdeling: int | None = Query(None, ge=0), vis_behandlede: bool = Query(False), c: dict = Depends(ctx),
                   db: AsyncSession = Depends(get_session)):
    """Kandidaterne fra det løbende filter, som workeren holder opdateret efterhånden som observationerne kommer ind."""
    scope = _candidate_scope(c, afdeling)
    f = await dku_candidates.load_filter(db)
    out = await _candidate_rows(db, await dku_candidates.standing(db, f, scope, vis_behandlede) if f["on"] else [])
    return {**out, "filter": f, "status": await dku_candidates.status(), "can_edit": c["admin"] or 0 in c["member_of"]}


@router.put("/candidates/standing")
async def save_standing(body: StandingIn, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    if not (c["admin"] or 0 in c["member_of"]):
        raise HTTPException(403, "Kun landsdækkende DKU-ansvarlige og administratorer kan ændre det løbende filter")
    return await dku_candidates.save_filter(db, {**body.clean(), "on": body.on})


class AnalysisIn(FilterIn):
    afdeling: int | None = Field(None, ge=0)


@router.post("/candidates/analyses")
async def start_analysis(body: AnalysisIn, c: dict = Depends(ctx), user: User = Depends(require_user),
                         db: AsyncSession = Depends(get_session)):
    """Send en kandidatsøgning til workeren – resultatet hentes bagefter under analyserne."""
    scope = _candidate_scope(c, body.afdeling)
    f = {k: v for k, v in body.clean().items() if k != "afdeling"}
    afd = await dku_cases.afdeling_name(db, body.afdeling) if body.afdeling else None
    label = f"{afd or 'Alle mine afdelinger'}, {f['days']} dage, {', '.join(dku.CRITERIA[k].lower() for k in f['kriterier'])}"
    return {"id": await dku_candidates.start_analysis(user.id, f, scope, label)}


@router.get("/candidates/analyses")
async def list_analyses(c: dict = Depends(ctx), user: User = Depends(require_user)):
    _candidate_scope(c, None)
    return await dku_candidates.analyses(user.id)


@router.get("/candidates/analyses/{aid}")
async def get_analysis(aid: str, vis_behandlede: bool = Query(False), c: dict = Depends(ctx), user: User = Depends(require_user),
                       db: AsyncSession = Depends(get_session)):
    _candidate_scope(c, None)
    job = await dku_candidates.analysis(aid)
    if job is None or job["user_id"] != user.id:
        raise HTTPException(404, "Analysen findes ikke (den gemmes en uge)")
    rows = await dku.annotate(db, job.pop("rows", None) or [], vis_behandlede)
    return {**job, **await _candidate_rows(db, rows)}


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
        done += await dku_cases.approve(db, obsid, body.note.strip(), obs["afdelinger"], user.id)
    await db.commit()
    return {"approved": done}


@router.delete("/review/{obsid}")
async def unapprove(obsid: int, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Fortryd en godkendelse, så observationen kommer tilbage i kandidatlisten."""
    obs = await dku.observation(db, "dofbasen", obsid)
    if obs is None or not await dku.is_dku_for(db, user, obs["afdelinger"]):
        raise HTTPException(404, "Observationen findes ikke")
    await dku_cases.unapprove(db, obsid)
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
    msgs = await dku_cases.messages(db, case.id, role, user.id)
    ids = {m["user_id"] for m in msgs if m["user_id"]} | {case.assignee_id, case.observer_id} - {None}
    names = await dku_cases.user_names(db, ids)
    tippers = await dku_cases.tipper_names(db, case.id)
    await dku_cases.mark_read(db, case.id, user.id, role)
    await db.commit()
    is_owner = user.id == case.observer_id or (snap.get("obserkode") and snap.get("obserkode") == user.obserkode)
    closed = case.status == "afgjort"
    afd = await dku_cases.afdeling_names(db)
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
    await dku_cases.touch_read(db, case.id, user.id)
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
            await dku_cases.dofbasen_status(case.obsid, "behandling", user.id, case.id)
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
            await dku_cases.dofbasen_status(case.obsid, "behandling", user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="observer", title="spørgsmål til din observation", body=note[:200])
    elif body.action == "close":
        if not body.outcome:
            raise HTTPException(400, "Vælg en afgørelse")
        case.status, case.outcome, case.outcome_note, case.closed_at = "afgjort", body.outcome, note or None, now
        case.assignee_id = case.assignee_id or user.id
        await _say(db, case, None, "system", f"Afgjort af {who}: {dku.OUTCOMES[body.outcome]}.", "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await dku_cases.dofbasen_status(case.obsid, "dku" if body.outcome in ("godkendt", "rettet") else None, user.id, case.id)
        tasks.add_task(dku.notify, case.id, to="parter", title="sagen er afgjort", body=dku.OUTCOMES[body.outcome], key="close")
    else:  # reopen
        if case.status != "afgjort":
            raise HTTPException(400, "Sagen er ikke afgjort")
        case.status, case.outcome, case.outcome_note, case.closed_at = ("afventer" if case.observer_involved else "behandles"), None, None, None
        await _say(db, case, None, "system", f"{who} har genåbnet sagen." + (f"\n{note}" if note else ""), "dku")
        await db.commit()
        if case.source == "dofbasen" and case.obsid:
            await dku_cases.dofbasen_status(case.obsid, "behandling", user.id, case.id)
    return {"ok": True, "status": case.status}


# ---------------------------------------------------------------- DKU-ansvarlige pr. lokalafdeling

@router.get("/members")
async def members(c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    if not (c["admin"] or c["member_of"]):
        raise HTTPException(403, "Kun for DKU-ansvarlige og administratorer")
    return {"can_edit": c["admin"], "afdelinger": await dku_cases.members_overview(db)}


class MemberIn(BaseModel):
    afdeling_id: int = Field(ge=0)
    obserkode: str = Field(min_length=2, max_length=16)


@router.post("/members")
async def add_member(body: MemberIn, c: dict = Depends(ctx), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if not c["admin"]:
        raise HTTPException(403, "Kræver admin-modulet DKU")
    if body.afdeling_id and not await dku_cases.afdeling_exists(db, body.afdeling_id):
        raise HTTPException(404, "Ukendt lokalafdeling")
    target = await dku_cases.approved_user_id(db, body.obserkode)
    if not target:
        raise HTTPException(404, "Ingen godkendt bruger med den obserkode i DOF.tool")
    await dku_cases.add_member(db, body.afdeling_id, target, user.id)
    await db.commit()
    return {"ok": True}


@router.delete("/members/{afdeling_id}/{user_id}")
async def remove_member(afdeling_id: int, user_id: int, c: dict = Depends(ctx), db: AsyncSession = Depends(get_session)):
    if not c["admin"]:
        raise HTTPException(403, "Kræver admin-modulet DKU")
    await dku_cases.remove_member(db, afdeling_id, user_id)
    await db.commit()
    return {"ok": True}
