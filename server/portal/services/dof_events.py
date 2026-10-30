"""DOF's ture og aktiviteter (dof.dk/oplev-fuglene/ture-og-aktiviter).

Oversigtssiden indeholder `window.DOFKommendeTure`: alle kommende ture med id, titel, dato, GPS og kommune.
Detaljesiden for hver tur (?event_id=) giver arrangør (lokalafdeling), tidspunkt, turtype, beskrivelse,
tilmelding, turleder og mødested. Detaljer hentes kun for nye ture og ture, der ikke er set i 3 døgn,
med pause mellem kaldene for at skåne dof.dk.

Kodning: lokalafdeling ud fra arrangørteksten, kommune ud fra GPS (punkt i kommunepolygon, ellers navnet),
lokalitet = nærmeste DOFbasen-lokalitet inden for 2 km.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from portal.db import SessionLocal
from portal.timeutil import dk_today

log = logging.getLogger("portal.events")

BASE = "https://www.dof.dk/oplev-fuglene/ture-og-aktiviter"
UA = {"User-Agent": "DOF.tool/1.0 (feltornitolog-portal; henter turoversigten nogle gange i dognet)"}
PAUSE_S = 5
DETAIL_MAX_AGE = timedelta(days=3)
LOC_RADIUS_M = 2000
# DOF's turtyper (fra filteret på dof.dk) – nogle indeholder selv "/"
KNOWN_TYPES = ["Foredrag/møder", "Børn og unge", "Offentlig tur", "Heldagstur", "Reservattur", "Begyndertur", "Fugletur",
               "Kursus", "Workshop", "Event", "Turrapport"]


def split_types(raw: str | None) -> list[str]:
    rest, out = (raw or "").strip(), []
    while rest:
        hit = next((t for t in KNOWN_TYPES if rest.lower().startswith(t.lower())), None)
        part = hit or rest.split("/", 1)[0]
        out.append(part.strip())
        rest = rest[len(part):].lstrip("/ ").strip()
    return [t for t in out if t]


def _clean(fragment: str | None) -> str | None:
    if not fragment:
        return None
    t = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    t = re.sub(r"</p>\s*<p[^>]*>", "\n\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"\s*\(\s*\)", "", t)  # tomme telefonfelter fra dof.dk
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip(" \n,.")
    return t or None


def parse_list(page: str) -> list[dict]:
    m = re.search(r"window\.DOFKommendeTure\s*=\s*(\[.*?\]);", page, re.S)
    if not m:
        raise ValueError("Turoversigten indeholder ikke DOFKommendeTure")
    out = []
    for e in json.loads(m.group(1)):
        d, mth, y = e["date"].split(".")
        out.append({"id": int(e["id"]), "title": html.unescape(e["title"]).strip(), "dato": date(int(y), int(mth), int(d)),
                    "lat": e.get("lat"), "lon": e.get("lng"), "commune": e.get("commune")})
    return out


def _section(page: str, label: str) -> str | None:
    """Indhold efter <span class="event-show-title">LABEL</span> frem til næste <br><br>."""
    m = re.search(rf'event-show-title">\s*{label}\s*</span>(.*?)<br\s*/?>\s*<br', page, re.S | re.I)
    return _clean(m.group(1)) if m else None


def parse_detail(page: str) -> dict:
    i = page.find('class="eventvisning"')
    if i < 0:
        raise ValueError("Turside uden eventvisning")
    body = page[i:i + 40000]
    out: dict = {}
    m = re.search(r'eventvisning-division>(.*?)</div>', body, re.S)
    if m:
        parts = [p.strip() for p in _clean(m.group(1)).split(",")]
        communes = [p for p in parts if p.lower().endswith("kommune")]
        if communes:
            out["commune"] = communes[-1]
        out["arrangoer"] = ", ".join(p for p in parts if not p.lower().endswith("kommune")) or None
    m = re.search(r'eventshow-tid">(.*?)</div>', body, re.S)
    if m:
        times = re.findall(r"(\d{1,2})[:.](\d{2})", m.group(1))
        out["tid_fra"] = f"{int(times[0][0]):02d}:{times[0][1]}" if times else None
        out["tid_til"] = f"{int(times[1][0]):02d}:{times[1][1]}" if len(times) > 1 else None
    m = re.search(r'eventshow-triptype">(.*?)</div>', body, re.S)
    if m:
        out["types"] = split_types(_clean(m.group(1)))
    m = re.search(r'eventshow-triptype">.*?</div>\s*<div[^>]*></div>(.*?)<span class="event-show-title"', body, re.S)
    out["beskrivelse"] = _clean(m.group(1)) if m else None
    out["tilmelding"] = _section(body, "TILMELDING")
    out["turleder"] = _section(body, "TURLEDER")
    out["moedested"] = _section(body, "MØDESTED") or _section(body, "M&Oslash;DESTED")
    return out


async def _afdelinger(s) -> list[tuple[int, str]]:
    return [(i, n.casefold()) for i, n in (await s.execute(text("SELECT id, navn FROM afdelinger"))).all()]


def match_afdeling(arrangoer: str | None, afd: list[tuple[int, str]]) -> int | None:
    if not arrangoer:
        return None
    a = arrangoer.casefold()
    hits = [i for i, n in afd if n in a]
    return hits[0] if hits else None


def _words(t: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-zæøåéü]+", (t or "").casefold()) if len(w) >= 4}


def pick_location(title: str, candidates: list[tuple[int, str, int]]) -> tuple[int, int] | None:
    """Vælg lokalitet: navn der deler et ord med turens titel (inden for 5 km) før nærmeste (inden for 2 km)."""
    tw = _words(title)
    named = [(d, ln) for ln, navn, d in candidates if tw & _words(navn)]
    if named:
        d, ln = min(named)
        return ln, d
    near = [(d, ln) for ln, _, d in candidates if d <= LOC_RADIUS_M]
    if near:
        d, ln = min(near)
        return ln, d
    return None


async def _code_location(s, ev_id: int, keep_loknr: bool = False) -> None:
    ev = (await s.execute(text("SELECT title, lat, lon, commune FROM dof_events WHERE id = :id"), {"id": ev_id})).mappings().first()
    if not ev or ev["lat"] is None:
        return
    pt = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)"
    kode = (await s.execute(text(f"""
        SELECT coalesce(
          (SELECT k.kode FROM kommuner k WHERE ST_Contains(k.geom, {pt}) LIMIT 1),
          (SELECT k.kode FROM kommuner k WHERE lower(k.navn) = lower(regexp_replace(coalesce(:commune, ''), 's? Kommune$', '')) LIMIT 1))
    """), {"lat": ev["lat"], "lon": ev["lon"], "commune": ev["commune"]})).scalar()
    cands = (await s.execute(text(f"""
        SELECT loknr, navn, round(ST_Distance(geom::geography, {pt}::geography))::int AS d FROM (
            SELECT loknr, navn, geom FROM locations WHERE geom IS NOT NULL ORDER BY geom <-> {pt} LIMIT 60) x
        WHERE ST_DWithin(geom::geography, {pt}::geography, 5000)
    """), {"lat": ev["lat"], "lon": ev["lon"]})).all()
    if keep_loknr:  # valgt af den der oprettede turen
        await s.execute(text("UPDATE dof_events SET kommune_kode = :k WHERE id = :id"), {"id": ev_id, "k": kode})
        return
    best = pick_location(ev["title"], [(r[0], r[1], r[2]) for r in cands])
    await s.execute(text("UPDATE dof_events SET kommune_kode = :k, loknr = :l, lok_dist_m = :d WHERE id = :id"),
                    {"id": ev_id, "k": kode, "l": best[0] if best else None, "d": best[1] if best else None})


async def recode_all() -> int:
    """Kod alle ture igen (fx efter forbedret matchning) – uden at hente noget fra dof.dk."""
    async with SessionLocal() as s:
        ids = (await s.execute(text("SELECT id FROM dof_events WHERE lat IS NOT NULL AND source = 'dof'"))).scalars().all()
        for i in ids:
            await _code_location(s, i)
        await s.commit()
    return len(ids)


async def sync() -> dict:
    """Hent oversigten og detaljer for nye/forældede ture."""
    async with httpx.AsyncClient(timeout=60, headers=UA, follow_redirects=True) as client:
        resp = await client.get(BASE)
        resp.raise_for_status()
        listed = parse_list(resp.text)
        ids = [e["id"] for e in listed]
        async with SessionLocal() as s:
            for e in listed:
                await s.execute(text("""
                    INSERT INTO dof_events (id, title, dato, lat, lon, commune, types, listed, fetched_at)
                    VALUES (:id, :title, :dato, :lat, :lon, :commune, '{}', true, now())
                    ON CONFLICT (id) DO UPDATE SET title = :title, dato = :dato, lat = :lat, lon = :lon,
                      commune = coalesce(:commune, dof_events.commune), listed = true, fetched_at = now()
                """), e)
            await s.execute(text("UPDATE dof_events SET listed = false WHERE source = 'dof' AND NOT (id = ANY(:ids))"), {"ids": ids})
            await s.commit()
            stale = (await s.execute(text("""
                SELECT id FROM dof_events WHERE source = 'dof' AND listed AND dato >= :today AND (detail_at IS NULL OR detail_at < :old)
                ORDER BY detail_at NULLS FIRST, dato
            """), {"today": dk_today(), "old": datetime.now(timezone.utc) - DETAIL_MAX_AGE})).scalars().all()
            afd = await _afdelinger(s)
        n_detail = 0
        for ev_id in stale:
            await asyncio.sleep(PAUSE_S)
            try:
                r = await client.get(BASE, params={"event_id": ev_id})
                r.raise_for_status()
                d = parse_detail(r.text)
            except Exception as exc:  # noqa: BLE001 – én tur må ikke stoppe resten
                log.info("Tur %s kunne ikke hentes: %s", ev_id, exc)
                continue
            async with SessionLocal() as s:
                await s.execute(text("""
                    UPDATE dof_events SET arrangoer = :arrangoer, afdeling_id = :afd, tid_fra = :tid_fra, tid_til = :tid_til,
                      types = :types, beskrivelse = :beskrivelse, tilmelding = :tilmelding, turleder = :turleder,
                      moedested = :moedested, commune = coalesce(:commune, commune), detail_at = now()
                    WHERE id = :id
                """), {"id": ev_id, "afd": match_afdeling(d.get("arrangoer"), afd), "arrangoer": d.get("arrangoer"),
                       "tid_fra": d.get("tid_fra"), "tid_til": d.get("tid_til"), "types": d.get("types") or [],
                       "beskrivelse": d.get("beskrivelse"), "tilmelding": d.get("tilmelding"), "turleder": d.get("turleder"),
                       "moedested": d.get("moedested"), "commune": d.get("commune")})
                await _code_location(s, ev_id)
                await s.commit()
            n_detail += 1
    log.info("DOF-ture: %s på listen, %s detaljer hentet", len(listed), n_detail)
    return {"listed": len(listed), "details": n_detail}


# ---------------------------------------------------------------- turoversigten i portalen (api/calendar.py)

EVENT_URL = BASE + "?event_id={}"


def event_url(row) -> str | None:
    """Link til turen på dof.dk – kun for DOF's egne ture (portalens ture har ingen side dér)."""
    return EVENT_URL.format(row["id"]) if row["source"] == "dof" else None


async def list_events(session, past: bool) -> list[dict]:
    """Kommende ture (eller alle med past=True) med afdeling, kommune, lokalitet og evt. lokalitetskalender."""
    rows = (await session.execute(text("""
        SELECT e.id, e.title, e.dato, e.tid_fra, e.tid_til, e.types, e.arrangoer, e.afdeling_id, a.navn AS afdeling,
               e.kommune_kode, coalesce(k.navn, regexp_replace(coalesce(e.commune, ''), ' Kommune$', '')) AS kommune,
               e.lat, e.lon, e.loknr, l.navn AS lok, e.lok_dist_m, e.tilmelding, e.moedested, c.id AS calendar_id,
               e.source, coalesce(u.navn, u.obserkode) AS created_by_navn
        FROM dof_events e LEFT JOIN afdelinger a ON a.id = e.afdeling_id LEFT JOIN kommuner k ON k.kode = e.kommune_kode
        LEFT JOIN locations l ON l.loknr = e.loknr LEFT JOIN site_calendars c ON c.loknr = e.loknr
        LEFT JOIN users u ON u.id = e.created_by
        WHERE (:past OR (e.listed AND e.dato >= :today)) ORDER BY e.dato, e.tid_fra NULLS LAST, e.title
    """), {"past": past, "today": dk_today()})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat(), "url": event_url(r)} for r in rows]


async def get_event(session, event_id: int) -> dict | None:
    row = (await session.execute(text("""
        SELECT e.*, a.navn AS afdeling, k.navn AS kommune, l.navn AS lok, c.id AS calendar_id,
               coalesce(u.navn, u.obserkode) AS created_by_navn FROM dof_events e
        LEFT JOIN afdelinger a ON a.id = e.afdeling_id LEFT JOIN kommuner k ON k.kode = e.kommune_kode
        LEFT JOIN locations l ON l.loknr = e.loknr LEFT JOIN site_calendars c ON c.loknr = e.loknr
        LEFT JOIN users u ON u.id = e.created_by WHERE e.id = :id
    """), {"id": event_id})).mappings().first()
    return {**dict(row), "url": event_url(row)} if row else None


async def next_portal_id(session) -> int:
    """Id til en tur oprettet i portalen (egen sekvens, så den ikke rammer dof.dk's id'er)."""
    return (await session.execute(text("SELECT nextval('portal_event_seq')"))).scalar()


def hhmm(v: str | None) -> str | None:
    """Klokkeslæt med kolon (formularen tillader også 07.30)."""
    return v.replace(".", ":") if v else None


async def save_portal_event(session, e, fields: dict) -> None:
    """Udfyld en portaltur (DofEvent) fra formularens felter. Lokaliteten giver position, afdeling og kommune.
    ValueError, hvis lokaliteten ikke har koordinater."""
    loc = (await session.execute(text("SELECT lat, lon, afdeling_id FROM locations WHERE loknr = :l"), {"l": fields["loknr"]})).first()
    if not loc or loc[0] is None:
        raise ValueError("Vælg en lokalitet med koordinater")
    clean = lambda v: (v or "").strip() or None  # noqa: E731
    e.title, e.dato = fields["title"].strip(), fields["dato"]
    e.tid_fra, e.tid_til = hhmm(fields.get("tid_fra")), hhmm(fields.get("tid_til"))
    e.types = [t.strip() for t in fields.get("types") or [] if t.strip()][:6]
    e.afdeling_id = fields.get("afdeling_id") or loc[2]
    e.arrangoer = clean(fields.get("arrangoer")) or (await session.execute(
        text("SELECT navn FROM afdelinger WHERE id = :a"), {"a": e.afdeling_id})).scalar()
    e.loknr, e.lok_dist_m, e.lat, e.lon = fields["loknr"], 0, loc[0], loc[1]
    e.moedested, e.beskrivelse = clean(fields.get("moedested")), clean(fields.get("beskrivelse"))
    e.tilmelding, e.turleder = clean(fields.get("tilmelding")), clean(fields.get("turleder"))
    e.listed, e.detail_at = True, datetime.now(timezone.utc)
    await session.flush()
    await _code_location(session, e.id, keep_loknr=True)  # kommune ud fra punktet
