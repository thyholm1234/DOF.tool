"""DOF-ture, lokalitetskalendere (planlægning af indsats på trækposter) og ICS-feeds."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_role, require_user
from portal.config import get_settings
from portal.db import SessionLocal, get_session
from portal.models import CalendarVisit, DofEvent, SiteCalendar, User
from portal.services import activity, calendars, dof_events
from portal.services.alerts import deliver
from portal.timeutil import dk_today

router = APIRouter(prefix="/api", tags=["calendar"])
moderator = require_role("moderator")
DOF_URL = "https://www.dof.dk/oplev-fuglene/ture-og-aktiviter?event_id={}"


def _event_url(row) -> str | None:
    return DOF_URL.format(row["id"]) if row["source"] == "dof" else None
TIME = r"^([01]\d|2[0-3])[:.][0-5]\d$"


def _hhmm(v: str | None) -> str | None:
    return v.replace(".", ":") if v else None


def _base_url(request: Request) -> str:
    return (get_settings().public_url or str(request.base_url)).rstrip("/")


# ---------------------------------------------------------------- DOF-ture


@router.get("/events")
async def events(past: bool = False, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT e.id, e.title, e.dato, e.tid_fra, e.tid_til, e.types, e.arrangoer, e.afdeling_id, a.navn AS afdeling,
               e.kommune_kode, coalesce(k.navn, regexp_replace(coalesce(e.commune, ''), ' Kommune$', '')) AS kommune,
               e.lat, e.lon, e.loknr, l.navn AS lok, e.lok_dist_m, e.tilmelding, e.moedested, c.id AS calendar_id,
               e.source, coalesce(u.navn, u.obserkode) AS created_by_navn
        FROM dof_events e LEFT JOIN afdelinger a ON a.id = e.afdeling_id LEFT JOIN kommuner k ON k.kode = e.kommune_kode
        LEFT JOIN locations l ON l.loknr = e.loknr LEFT JOIN site_calendars c ON c.loknr = e.loknr
        LEFT JOIN users u ON u.id = e.created_by
        WHERE (:past OR (e.listed AND e.dato >= :today)) ORDER BY e.dato, e.tid_fra NULLS LAST, e.title
    """), {"past": past, "today": dk_today()})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat(), "url": _event_url(r)} for r in rows]


@router.get("/events/{event_id}")
async def event(event_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    row = (await db.execute(text("""
        SELECT e.*, a.navn AS afdeling, k.navn AS kommune, l.navn AS lok, c.id AS calendar_id,
               coalesce(u.navn, u.obserkode) AS created_by_navn FROM dof_events e
        LEFT JOIN afdelinger a ON a.id = e.afdeling_id LEFT JOIN kommuner k ON k.kode = e.kommune_kode
        LEFT JOIN locations l ON l.loknr = e.loknr LEFT JOIN site_calendars c ON c.loknr = e.loknr
        LEFT JOIN users u ON u.id = e.created_by WHERE e.id = :id
    """), {"id": event_id})).mappings().first()
    if not row:
        raise HTTPException(404, "Turen findes ikke")
    return {**dict(row), "url": _event_url(row)}


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
    loc = (await db.execute(text("SELECT lat, lon, afdeling_id FROM locations WHERE loknr = :l"), {"l": body.loknr})).first()
    if not loc or loc[0] is None:
        raise HTTPException(400, "Vælg en lokalitet med koordinater")
    clean = lambda v: (v or "").strip() or None  # noqa: E731
    e.title, e.dato = body.title.strip(), body.dato
    e.tid_fra, e.tid_til = _hhmm(body.tid_fra), _hhmm(body.tid_til)
    e.types = [t.strip() for t in body.types if t.strip()][:6]
    e.afdeling_id = body.afdeling_id or loc[2]
    e.arrangoer = clean(body.arrangoer) or (await db.execute(text("SELECT navn FROM afdelinger WHERE id = :a"), {"a": e.afdeling_id})).scalar()
    e.loknr, e.lok_dist_m, e.lat, e.lon = body.loknr, 0, loc[0], loc[1]
    e.moedested, e.beskrivelse = clean(body.moedested), clean(body.beskrivelse)
    e.tilmelding, e.turleder = clean(body.tilmelding), clean(body.turleder)
    e.listed, e.detail_at = True, datetime.now(timezone.utc)
    await db.flush()
    await dof_events._code_location(db, e.id, keep_loknr=True)  # kommune ud fra punktet


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
async def create_event(body: EventIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    """Moderatorer kan oprette ture og begivenheder, der vises sammen med DOF's officielle ture."""
    new_id = (await db.execute(text("SELECT nextval('portal_event_seq')"))).scalar()
    e = DofEvent(id=new_id, source="portal", created_by=user.id, title=body.title, dato=body.dato, types=[])
    db.add(e)
    await _save_event(db, e, body)
    await db.commit()
    await activity.track(user.id, "event", None, {"event": e.id})
    return {"id": e.id}


@router.put("/events/{event_id}")
async def update_event(event_id: int, body: EventIn, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
    e = await _portal_event(db, event_id)
    await _save_event(db, e, body)
    await db.commit()
    return {"id": e.id}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int, user: User = Depends(moderator), db: AsyncSession = Depends(get_session)):
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
    mine = (await db.execute(select(func.count()).select_from(SiteCalendar).where(SiteCalendar.created_by == user.id))).scalar()
    return {"calendars": await calendars.list_calendars(db, user, lat, lon), "created_by_me": mine,
            "max_per_user": calendars.MAX_PER_USER, "kinds": calendars.KINDS}


@router.post("/calendars")
async def create_calendar(body: CalendarIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    existing = (await db.execute(select(SiteCalendar).where(SiteCalendar.loknr == body.loknr))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Lokaliteten har allerede en kalender")
    n = (await db.execute(select(func.count()).select_from(SiteCalendar).where(SiteCalendar.created_by == user.id))).scalar()
    if n >= calendars.MAX_PER_USER and user.role not in ("admin", "superadmin"):
        raise HTTPException(400, f"Du kan højst oprette {calendars.MAX_PER_USER} kalendere")
    lok = (await db.execute(text("SELECT navn FROM locations WHERE loknr = :l"), {"l": body.loknr})).scalar()
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
    meta = (await db.execute(text("""
        SELECT l.navn AS lok, l.lat, l.lon, k.navn AS kommune, a.navn AS afdeling, coalesce(u.navn, u.obserkode) AS creator
        FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id
        LEFT JOIN users u ON u.id = :creator WHERE l.loknr = :l
    """), {"l": c.loknr, "creator": c.created_by})).mappings().first()
    start = start or dk_today().replace(day=1)
    end = end or (start + timedelta(days=41))
    if (end - start).days > 120:
        raise HTTPException(400, "For stort interval")
    events = (await db.execute(text("""
        SELECT id, title, dato, tid_fra, tid_til FROM dof_events WHERE loknr = :l AND dato BETWEEN :a AND :b ORDER BY dato
    """), {"l": c.loknr, "a": start, "b": end})).mappings().all()
    center = max(dk_today(), start) if start <= dk_today() <= end else start + timedelta(days=(end - start).days // 2)
    return {"id": c.id, "loknr": c.loknr, "title": c.title, "kind": c.kind, "description": c.description,
            "can_edit": _can_edit(c, user), **dict(meta), "from": start.isoformat(), "to": end.isoformat(),
            "visits": await calendars.visits(db, user, c.id, start, end),
            "events": [{**dict(e), "dato": e["dato"].isoformat()} for e in events],
            "typical": await calendars.typical_species(db, c.loknr, center),
            "kinds": calendars.KINDS}


@router.get("/calendars/{cid}/forecast")
async def calendar_forecast(cid: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Trækvejr for de nærmeste dage (hentes separat, så kalenderen ikke venter på DMI)."""
    c = await _calendar(db, cid)
    pos = (await db.execute(text("SELECT lat, lon FROM locations WHERE loknr = :l"), {"l": c.loknr})).first()
    try:
        return await calendars.forecast_days(c.loknr, pos[0] if pos else None, pos[1] if pos else None)
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
    v = (await db.execute(select(CalendarVisit).where(CalendarVisit.calendar_id == c.id, CalendarVisit.user_id == user.id,
                                                      CalendarVisit.dato == body.dato))).scalar_one_or_none()
    new = v is None
    if new:
        v = CalendarVisit(calendar_id=c.id, user_id=user.id, dato=body.dato)
        db.add(v)
    v.tid_fra, v.tid_til = _hhmm(body.tid_fra), _hhmm(body.tid_til)
    v.visibility, v.show_phone = body.visibility, body.show_phone
    v.companions = (body.companions or "").strip() or None
    v.note = (body.note or "").strip() or None
    v.lat, v.lon = (None, None)
    if body.lat is not None and body.lon is not None:
        far = (await db.execute(text("""
            SELECT ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, 25000) FROM locations WHERE loknr = :l
        """), {"lat": body.lat, "lon": body.lon, "l": c.loknr})).scalar()
        if far is False:
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
        others = (await s.execute(text("""
            SELECT DISTINCT user_id FROM calendar_visits WHERE calendar_id = :c AND dato = :d AND user_id <> :u AND visibility = 'public'
        """), {"c": v.calendar_id, "d": v.dato, "u": v.user_id})).scalars().all()
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
    rows = (await db.execute(text("""
        SELECT v.id, v.calendar_id, v.dato, v.tid_fra, v.tid_til, v.visibility, v.companions, v.note, v.lat, v.lon, c.title, c.kind, c.loknr,
               l.navn AS lok, k.navn AS kommune,
               (SELECT count(*) FROM calendar_visits o WHERE o.calendar_id = v.calendar_id AND o.dato = v.dato AND o.user_id <> v.user_id) AS others
        FROM calendar_visits v JOIN site_calendars c ON c.id = v.calendar_id JOIN locations l ON l.loknr = c.loknr
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE v.user_id = :u AND v.dato BETWEEN :a AND :b ORDER BY v.dato, v.tid_fra
    """), {"u": user.id, "a": start, "b": end})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat()} for r in rows]


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
    rows = (await db.execute(text("""
        SELECT v.id, v.dato, v.tid_fra, v.tid_til, v.companions, v.note, c.id AS cid, c.title, l.navn AS lok,
               coalesce(v.lat, l.lat) AS lat, coalesce(v.lon, l.lon) AS lon
        FROM calendar_visits v JOIN site_calendars c ON c.id = v.calendar_id JOIN locations l ON l.loknr = c.loknr
        WHERE v.user_id = :u AND v.dato >= current_date - 60 ORDER BY v.dato
    """), {"u": u.id})).mappings().all()
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
    rows = (await db.execute(text("""
        SELECT v.id, v.dato, v.tid_fra, v.tid_til, v.companions, v.visibility, v.user_id, coalesce(us.navn, us.obserkode) AS navn,
               l.navn AS lok, coalesce(v.lat, l.lat) AS lat, coalesce(v.lon, l.lon) AS lon
        FROM calendar_visits v JOIN users us ON us.id = v.user_id JOIN locations l ON l.loknr = :l
        WHERE v.calendar_id = :c AND v.dato >= current_date - 60 AND (v.visibility = 'public' OR v.user_id = :u) ORDER BY v.dato
    """), {"c": c.id, "u": u.id, "l": c.loknr})).mappings().all()
    events = [{"uid": f"visit-{r['id']}@dof.tool", "start": r["dato"], "tid_fra": r["tid_fra"], "tid_til": r["tid_til"],
               "summary": f"{r['navn']} @ {c.title}", "location": r["lok"], "lat": r["lat"], "lon": r["lon"],
               "description": r["companions"] and f"Med: {r['companions']}"} for r in rows]
    for e in (await db.execute(text("SELECT id, title, dato, tid_fra, tid_til FROM dof_events WHERE loknr = :l AND dato >= current_date - 60"),
                               {"l": c.loknr})).mappings().all():
        events.append({"uid": f"dofevent-{e['id']}@dof.tool", "start": e["dato"], "tid_fra": e["tid_fra"], "tid_til": e["tid_til"],
                       "summary": f"DOF-tur: {e['title']}", "url": f"https://www.dof.dk/oplev-fuglene/ture-og-aktiviter?event_id={e['id']}"})
    return Response(calendars.ics(f"{c.title} (DOF.tool)", events), media_type="text/calendar; charset=utf-8")
