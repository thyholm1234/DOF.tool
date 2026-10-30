"""DOF-ture, lokalitetskalendere (planlægning af indsats på trækposter) og ICS-feeds."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_perm, require_user
from portal.config import get_settings
from portal.db import SessionLocal, get_session
from portal.models import CalendarVisit, DofEvent, SiteCalendar, User
from portal.services import activity, calendars, dof_events
from portal.services.alerts import deliver
from portal.timeutil import dk_today

router = APIRouter(prefix="/api", tags=["calendar"])
perm_ture = require_perm("ture")
TIME = r"^([01]\d|2[0-3])[:.][0-5]\d$"



def _base_url(request: Request) -> str:
    return (get_settings().public_url or str(request.base_url)).rstrip("/")


# ---------------------------------------------------------------- DOF-ture


@router.get("/events")
async def events(past: bool = False, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    return await dof_events.list_events(db, past)


@router.get("/events/{event_id}")
async def event(event_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    e = await dof_events.get_event(db, event_id)
    if e is None:
        raise HTTPException(404, "Turen findes ikke")
    return e


@router.get("/events/{event_id}/ics")
async def event_ics(event_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    e = await event(event_id, user, db)
    body = calendars.ics(e["title"], [{
        "uid": f"dofevent-{event_id}@dof.tool", "start": e["dato"], "tid_fra": e["tid_fra"], "tid_til": e["tid_til"],
        "summary": e["title"], "description": "\n\n".join(x for x in (e["arrangoer"], e["beskrivelse"],
                                                                         e["moedested"] and f"Mødested: {e['moedested']}", e["url"]) if x),
        "location": e["moedested"] or e["lok"], "lat": e["lat"], "lon": e["lon"], "url": e["url"]}])
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="dof-tur-{event_id}.ics"'})


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    dato: date
    tid_fra: str | None = Field(None, pattern=TIME)
    tid_til: str | None = Field(None, pattern=TIME)
    types: list[str] = Field(default_factory=list, max_length=6)
    arrangoer: str | None = Field(None, max_length=300)
    afdeling_id: int | None = None
    loknr: int
    moedested: str | None = Field(None, max_length=1000)
    beskrivelse: str | None = Field(None, max_length=8000)
    tilmelding: str | None = Field(None, max_length=1000)
    turleder: str | None = Field(None, max_length=1000)


async def _save_event(db: AsyncSession, e: DofEvent, body: EventIn) -> None:
    try:
        await dof_events.save_portal_event(db, e, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _portal_event(db: AsyncSession, event_id: int) -> DofEvent:
    e = await db.get(DofEvent, event_id)
    if e is None:
        raise HTTPException(404, "Turen findes ikke")
    if e.source != "portal":
        raise HTTPException(403, "DOF's ture rettes på dof.dk")
    return e


@router.get("/event-types")
async def event_types(user: User = Depends(require_user)):
    return dof_events.KNOWN_TYPES


@router.post("/events")
async def create_event(body: EventIn, user: User = Depends(perm_ture), db: AsyncSession = Depends(get_session)):
    """Moderatorer kan oprette ture og begivenheder, der vises sammen med DOF's officielle ture."""
    new_id = await dof_events.next_portal_id(db)
    e = DofEvent(id=new_id, source="portal", created_by=user.id, title=body.title, dato=body.dato, types=[])
    db.add(e)
    await _save_event(db, e, body)
    await db.commit()
    await activity.track(user.id, "event", None, {"event": e.id})
    return {"id": e.id}


@router.put("/events/{event_id}")
async def update_event(event_id: int, body: EventIn, user: User = Depends(perm_ture), db: AsyncSession = Depends(get_session)):
    e = await _portal_event(db, event_id)
    await _save_event(db, e, body)
    await db.commit()
    return {"id": e.id}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, user: User = Depends(perm_ture), db: AsyncSession = Depends(get_session)):
    e = await _portal_event(db, event_id)
    await db.delete(e)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- kalendere


class CalendarIn(BaseModel):
    loknr: int
    title: str | None = Field(None, max_length=160)
    kind: str = Field("traekpost", pattern="^(traekpost|seawatch|ringmaerkning|optaelling|andet)$")
    description: str | None = Field(None, max_length=2000)


class CalendarPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=160)
    kind: str | None = Field(None, pattern="^(traekpost|seawatch|ringmaerkning|optaelling|andet)$")
    description: str | None = Field(None, max_length=2000)


class VisitIn(BaseModel):
    dato: date
    tid_fra: str = Field(pattern=TIME)
    tid_til: str | None = Field(None, pattern=TIME)
    visibility: str = Field("public", pattern="^(public|private)$")
    companions: str | None = Field(None, max_length=300)
    note: str | None = Field(None, max_length=500)
    show_phone: bool = False
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


@router.get("/calendars")
async def list_calendars(lat: float | None = None, lon: float | None = None, user: User = Depends(require_user),
                         db: AsyncSession = Depends(get_session)):
    mine = await calendars.count_by_creator(db, user.id)
    return {"calendars": await calendars.list_calendars(db, user, lat, lon), "created_by_me": mine,
            "max_per_user": calendars.MAX_PER_USER, "kinds": calendars.KINDS}


@router.post("/calendars")
async def create_calendar(body: CalendarIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if await calendars.by_location(db, body.loknr):
        raise HTTPException(409, "Lokaliteten har allerede en kalender")
    if await calendars.count_by_creator(db, user.id) >= calendars.MAX_PER_USER and user.role not in ("admin", "superadmin"):
        raise HTTPException(400, f"Du kan højst oprette {calendars.MAX_PER_USER} kalendere")
    lok = await calendars.location_name(db, body.loknr)
    if not lok:
        raise HTTPException(400, "Ukendt lokalitet")
    c = SiteCalendar(loknr=body.loknr, created_by=user.id, title=(body.title or lok).strip(), kind=body.kind,
                     description=(body.description or "").strip() or None)
    db.add(c)
    await db.commit()
    await activity.track(user.id, "calendar", None, {"calendar": c.id, "loknr": body.loknr})
    return {"id": c.id}


async def _calendar(db: AsyncSession, cid: int) -> SiteCalendar:
    c = await db.get(SiteCalendar, cid)
    if c is None:
        raise HTTPException(404, "Kalenderen findes ikke")
    return c


def _can_edit(c: SiteCalendar, user: User) -> bool:
    return c.created_by == user.id or user.role in ("admin", "superadmin")


@router.patch("/calendars/{cid}")
async def patch_calendar(cid: int, body: CalendarPatch, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    c = await _calendar(db, cid)
    if not _can_edit(c, user):
        raise HTTPException(403, "Kun den der oprettede kalenderen kan rette den")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v.strip() if isinstance(v, str) else v)
    await db.commit()
    return {"ok": True}


@router.delete("/calendars/{cid}")
async def delete_calendar(cid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    c = await _calendar(db, cid)
    if not _can_edit(c, user):
        raise HTTPException(403, "Kun den der oprettede kalenderen kan slette den")
    await db.delete(c)
    await db.commit()
    return {"ok": True}


@router.get("/calendars/{cid}")
async def get_calendar(cid: int, start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
                       user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    c = await _calendar(db, cid)
    meta = await calendars.calendar_meta(db, c)
    start = start or dk_today().replace(day=1)
    end = end or (start + timedelta(days=41))
    if (end - start).days > 120:
        raise HTTPException(400, "For stort interval")
    center = max(dk_today(), start) if start <= dk_today() <= end else start + timedelta(days=(end - start).days // 2)
    return {"id": c.id, "loknr": c.loknr, "title": c.title, "kind": c.kind, "description": c.description,
            "can_edit": _can_edit(c, user), **meta, "from": start.isoformat(), "to": end.isoformat(),
            "visits": await calendars.visits(db, user, c.id, start, end),
            "events": await calendars.events_at(db, c.loknr, start, end),
            "typical": await calendars.typical_species(db, c.loknr, center),
            "kinds": calendars.KINDS}


@router.get("/calendars/{cid}/forecast")
async def calendar_forecast(cid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Trækvejr for de nærmeste dage (hentes separat, så kalenderen ikke venter på DMI)."""
    c = await _calendar(db, cid)
    lat, lon = await calendars.location_position(db, c.loknr)
    try:
        return await calendars.forecast_days(c.loknr, lat, lon)
    except Exception:  # noqa: BLE001 – DMI nede må ikke vælte kalenderen
        return []


@router.post("/calendars/{cid}/visits")
async def save_visit(cid: int, body: VisitIn, tasks: BackgroundTasks, user: User = Depends(require_user),
                     db: AsyncSession = Depends(get_session)):
    c = await _calendar(db, cid)
    if body.dato < dk_today() - timedelta(days=1):
        raise HTTPException(400, "Datoen er passeret")
    if body.show_phone and not (user.prefs or {}).get("phone"):
        raise HTTPException(400, "Tilføj først dit telefonnummer under Profil")
    v = await calendars.own_visit(db, c.id, user.id, body.dato)
    new = v is None
    if new:
        v = CalendarVisit(calendar_id=c.id, user_id=user.id, dato=body.dato)
        db.add(v)
    v.tid_fra, v.tid_til = dof_events.hhmm(body.tid_fra), dof_events.hhmm(body.tid_til)
    v.visibility, v.show_phone = body.visibility, body.show_phone
    v.companions = (body.companions or "").strip() or None
    v.note = (body.note or "").strip() or None
    v.lat, v.lon = (None, None)
    if body.lat is not None and body.lon is not None:
        if await calendars.near_location(db, c.loknr, body.lat, body.lon) is False:
            raise HTTPException(400, "Positionen ligger mere end 25 km fra lokaliteten")
        v.lat, v.lon = round(body.lat, 6), round(body.lon, 6)
    v.updated_at = datetime.now(timezone.utc)
    await db.commit()
    if new and body.visibility == "public":
        tasks.add_task(_notify_same_day, v.id)
    tasks.add_task(activity.track, user.id, "visit", None, {"calendar": c.id, "dato": body.dato.isoformat()})
    return {"id": v.id}


async def _notify_same_day(visit_id: int) -> None:
    """Andre med offentlige planer samme dag og sted får besked – så man kan koordinere."""
    async with SessionLocal() as s:
        v = await s.get(CalendarVisit, visit_id)
        if v is None:
            return
        c = await s.get(SiteCalendar, v.calendar_id)
        who = await s.get(User, v.user_id)
        others = await calendars.same_day_planners(s, v)
        if not others or c is None or who is None:
            return
        tid = f"{v.tid_fra}–{v.tid_til}" if v.tid_til else f"fra {v.tid_fra}"
        msg = {"title": f"📅 {who.navn or who.obserkode} kommer også til {c.title}", "body": f"{v.dato:%d.%m.%Y} {tid}",
               "url": f"/#/kalender/{c.id}?d={v.dato.isoformat()}", "tag": f"cal-{c.id}-{v.dato}", "kategori": None,
               "rule_id": None, "push": True}
        await deliver(s, None, {(u, f"visit:{v.id}"): msg for u in others})


@router.delete("/visits/{visit_id}")
async def delete_visit(visit_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    v = await db.get(CalendarVisit, visit_id)
    if v is None or v.user_id != user.id:
        raise HTTPException(404)
    await db.delete(v)
    await db.commit()
    return {"ok": True}


@router.get("/me/visits")
async def my_visits(start: date = Query(alias="from"), end: date = Query(alias="to"), user: User = Depends(require_user),
                    db: AsyncSession = Depends(get_session)):
    if (end - start).days > 120:
        raise HTTPException(400, "For stort interval")
    return await calendars.my_visits(db, user.id, start, end)


# ---------------------------------------------------------------- ICS-abonnementer


@router.get("/me/ics")
async def my_ics(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    token = calendars.ensure_token(user)
    await db.commit()
    base = _base_url(request)
    return {"token": token, "mine": f"{base}/api/ics/{token}/mine.ics", "calendar": f"{base}/api/ics/{token}/kalender/{{id}}.ics"}


@router.post("/me/ics/reset")
async def reset_ics(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    prefs = dict(user.prefs or {})
    prefs.pop("ics_token", None)
    user.prefs = prefs
    return await my_ics(request, user, db)


async def _ics_user(db: AsyncSession, token: str) -> User:
    u = await calendars.user_by_token(db, token)
    if u is None:
        raise HTTPException(404)
    return u


@router.get("/ics/{token}/mine.ics")
async def ics_mine(token: str, db: AsyncSession = Depends(get_session)):
    """Egne planlagte besøg – til abonnement i Google/Outlook/Apple (kræver ikke login, kun token)."""
    u = await _ics_user(db, token)
    rows = await calendars.ics_my_visits(db, u.id)
    body = calendars.ics("Mine fugleplaner (DOF.tool)", [{
        "uid": f"visit-{r['id']}@dof.tool", "start": r["dato"], "tid_fra": r["tid_fra"], "tid_til": r["tid_til"],
        "summary": f"🔭 {r['title']}", "location": r["lok"], "lat": r["lat"], "lon": r["lon"],
        "description": "\n".join(x for x in (r["companions"] and f"Med: {r['companions']}", r["note"]) if x)} for r in rows])
    return Response(body, media_type="text/calendar; charset=utf-8")


@router.get("/ics/{token}/kalender/{cid}.ics")
async def ics_calendar(token: str, cid: int, db: AsyncSession = Depends(get_session)):
    """En lokalitets kalender: offentlige besøg (+ egne private) og DOF-ture på stedet."""
    u = await _ics_user(db, token)
    c = await _calendar(db, cid)
    rows = await calendars.ics_calendar_visits(db, c, u.id)
    events = [{"uid": f"visit-{r['id']}@dof.tool", "start": r["dato"], "tid_fra": r["tid_fra"], "tid_til": r["tid_til"],
               "summary": f"{r['navn']} @ {c.title}", "location": r["lok"], "lat": r["lat"], "lon": r["lon"],
               "description": r["companions"] and f"Med: {r['companions']}"} for r in rows]
    for e in await calendars.ics_events_at(db, c.loknr):
        events.append({"uid": f"dofevent-{e['id']}@dof.tool", "start": e["dato"], "tid_fra": e["tid_fra"], "tid_til": e["tid_til"],
                       "summary": f"DOF-tur: {e['title']}", "url": dof_events.EVENT_URL.format(e["id"])})
    return Response(calendars.ics(f"{c.title} (DOF.tool)", events), media_type="text/calendar; charset=utf-8")
