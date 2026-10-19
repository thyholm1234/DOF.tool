"""Lokalitetskalendere: planlæg indsats på trækposter.

- Én fælles kalender pr. lokalitet; enhver bruger kan oprette kalendere (højst 5), alle kan se og bruge dem.
- Man melder sig til en dag med forventet tidsrum, offentligt eller privat, med evt. medobservatører og
  telefonnummer fra profilen. Private besøg ses kun af én selv – andre ser blot, at der kommer nogen.
- Kalenderen viser trækvejr for de nærmeste dage (DMI HARMONIE for punktet) og hvilke arter der historisk
  er set på lokaliteten omkring datoen.
- ICS-feeds (abonnement i Google/Outlook/Apple) via personligt token: egne planer og enkelte kalendere.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached
from portal.models import User
from portal.services import dmi
from portal.timeutil import TZ, dk_today

MAX_PER_USER = 5
KINDS = {"traekpost": "Trækpost", "seawatch": "Seawatch", "ringmaerkning": "Ringmærkning", "optaelling": "Optælling", "andet": "Andet"}


def ensure_token(user: User) -> str:
    prefs = dict(user.prefs or {})
    if not prefs.get("ics_token"):
        prefs["ics_token"] = secrets.token_urlsafe(18)
        user.prefs = prefs
    return prefs["ics_token"]


async def user_by_token(session: AsyncSession, token: str) -> User | None:
    from sqlalchemy import select
    return (await session.execute(select(User).where(User.prefs["ics_token"].astext == token, User.blocked.is_(False)))).scalar_one_or_none()


async def list_calendars(session: AsyncSession, user: User, lat: float | None, lon: float | None) -> list[dict]:
    dist = ("round((ST_Distance(l.geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000)::numeric, 1)"
            if lat is not None and lon is not None else "NULL")
    rows = (await session.execute(text(f"""
        SELECT c.id, c.title, c.kind, c.description, c.loknr, c.created_by, l.navn AS lok, l.lat, l.lon, k.navn AS kommune,
               a.navn AS afdeling, coalesce(u.navn, u.obserkode) AS creator, {dist} AS km,
               (SELECT min(v.dato) FROM calendar_visits v WHERE v.calendar_id = c.id AND v.dato >= :today) AS next_date,
               (SELECT count(*) FROM calendar_visits v WHERE v.calendar_id = c.id AND v.dato = (
                    SELECT min(v2.dato) FROM calendar_visits v2 WHERE v2.calendar_id = c.id AND v2.dato >= :today)) AS next_n,
               (SELECT count(*) FROM calendar_visits v WHERE v.calendar_id = c.id AND v.dato BETWEEN :today AND :week) AS week_n,
               (SELECT min(v.dato) FROM calendar_visits v WHERE v.calendar_id = c.id AND v.user_id = :me AND v.dato >= :today) AS my_next
        FROM site_calendars c JOIN locations l ON l.loknr = c.loknr
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id
        LEFT JOIN users u ON u.id = c.created_by
        ORDER BY c.title
    """), {"today": dk_today(), "week": dk_today() + timedelta(days=6), "me": user.id, "lat": lat, "lon": lon})).mappings().all()
    return [{**dict(r), "km": float(r["km"]) if r["km"] is not None else None, "mine": r["created_by"] == user.id,
             "next_date": r["next_date"].isoformat() if r["next_date"] else None,
             "my_next": r["my_next"].isoformat() if r["my_next"] else None} for r in rows]


def _visit_json(r, viewer: User) -> dict:
    own = r["user_id"] == viewer.id
    if r["visibility"] == "private" and not own:
        return {"id": None, "private": True, "dato": r["dato"].isoformat()}
    return {"id": r["id"], "private": r["visibility"] == "private", "own": own, "dato": r["dato"].isoformat(),
            "tid_fra": r["tid_fra"], "tid_til": r["tid_til"], "navn": r["navn"] or r["obserkode"], "obserkode": r["obserkode"],
            "companions": r["companions"], "note": r["note"], "visibility": r["visibility"], "show_phone": r["show_phone"],
            "phone": (r["prefs"] or {}).get("phone") if r["show_phone"] else None, "lat": r["lat"], "lon": r["lon"]}


async def visits(session: AsyncSession, viewer: User, calendar_id: int, start: date, end: date) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT v.*, u.navn, u.obserkode, u.prefs FROM calendar_visits v JOIN users u ON u.id = v.user_id
        WHERE v.calendar_id = :c AND v.dato BETWEEN :a AND :b ORDER BY v.dato, v.tid_fra
    """), {"c": calendar_id, "a": start, "b": end})).mappings().all()
    return [_visit_json(r, viewer) for r in rows]


async def typical_species(session: AsyncSession, loknr: int, around: date) -> dict:
    """Arter set på lokaliteten ±10 dage omkring datoen de seneste 15 år – hvad kan man forvente?"""
    doy = around.timetuple().tm_yday

    async def produce():
        rows = (await session.execute(text("""
            WITH o AS (
                SELECT o.dato, o.antal, coalesce(s.list_artnr, s.artnr) AS artnr, o.adfkode
                FROM observations o JOIN species s ON s.artnr = o.artnr
                WHERE o.loknr = :l AND o.dato >= current_date - interval '15 years' AND s.art_type IN ('art', 'underart')
                  AND abs(extract(doy FROM o.dato)::int - :doy) <= 10
            )
            SELECT s.navn, s.klass, count(DISTINCT o.dato) AS dage, sum(o.antal) AS ind, max(o.antal) AS maks,
                   bool_or(split_part(coalesce(o.adfkode, ''), ' ', 1) IN ('T','TF','N','NØ','Ø','SØ','S','SV','V','NV')) AS traek
            FROM o JOIN species s ON s.artnr = o.artnr GROUP BY s.navn, s.klass ORDER BY dage DESC, ind DESC LIMIT 25
        """), {"l": loknr, "doy": doy})).mappings().all()
        days = (await session.execute(text("""
            SELECT count(DISTINCT dato) FROM observations WHERE loknr = :l AND dato >= current_date - interval '15 years'
              AND abs(extract(doy FROM dato)::int - :doy) <= 10
        """), {"l": loknr, "doy": doy})).scalar() or 0
        return {"days_covered": days, "species": [dict(r) for r in rows]}

    return await cached(f"cal:typ:{loknr}:{doy // 7}", 86400, produce)


async def forecast_days(loknr: int, lat: float | None, lon: float | None) -> list[dict]:
    """Trækvejr pr. dag (morgen kl. 5–12 lokal tid) for de dage prognosen dækker."""
    if lat is None or lon is None:
        return []
    hours = await dmi.point_forecast(f"lok:{loknr}", lat, lon)
    days: dict[str, list] = {}
    for hr in hours:
        t = datetime.fromisoformat(hr["t"].replace("Z", "+00:00")).astimezone(TZ)
        if 5 <= t.hour <= 12:
            days.setdefault(t.date().isoformat(), []).append(hr)
    out = []
    for d, hrs in sorted(days.items()):
        if len(hrs) < 3:
            continue
        land = sum(h.get("land", 0) for h in hrs) / len(hrs)
        speed = sum(h["speed"] for h in hrs) / len(hrs)
        rain = sum(h.get("rain") or 0 for h in hrs)
        mid = hrs[len(hrs) // 2]
        out.append({"dato": d, "land": round(land, 1), "speed": round(speed, 1), "dir": mid["dir"], "rain": round(rain, 1),
                    "temp": mid.get("temp"), "reason": mid.get("reason")})
    return out


# ---------------------------------------------------------------- ICS


def _ics_escape(s: str | None) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, b = [], line.encode()
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:  # klip ikke midt i et UTF-8-tegn
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def ics(name: str, events: list[dict]) -> str:
    """events: {uid, start (date), tid_fra, tid_til, summary, description, location, lat, lon, url}"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DOF.tool//Kalender//DA", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_ics_escape(name)}", "X-WR-TIMEZONE:Europe/Copenhagen", "REFRESH-INTERVAL;VALUE=DURATION:PT2H",
             "X-PUBLISHED-TTL:PT2H"]
    for e in events:
        d: date = e["start"]
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{stamp}"]
        if e.get("tid_fra"):
            h1, m1 = map(int, e["tid_fra"].split(":"))
            start = datetime(d.year, d.month, d.day, h1, m1, tzinfo=TZ)
            if e.get("tid_til"):
                h2, m2 = map(int, e["tid_til"].split(":"))
                end = datetime(d.year, d.month, d.day, h2, m2, tzinfo=TZ)
                if end <= start:
                    end = start + timedelta(hours=2)
            else:
                end = start + timedelta(hours=2)
            lines += [f"DTSTART:{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}", f"DTEND:{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        lines.append(f"SUMMARY:{_ics_escape(e['summary'])}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{_ics_escape(e['description'])}")
        if e.get("location"):
            lines.append(f"LOCATION:{_ics_escape(e['location'])}")
        if e.get("lat") is not None and e.get("lon") is not None:
            lines.append(f"GEO:{e['lat']:.5f};{e['lon']:.5f}")
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
