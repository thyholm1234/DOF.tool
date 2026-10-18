"""Gateway-handlers. Hver handler laver præcis ét HTTP-kald til DOFbasen og efterbehandler svaret."""

from __future__ import annotations

import calendar
import html
import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select, text

from portal.cache import bump, get_json, publish, set_json
from portal.config import get_settings
from portal.db import SessionLocal
from portal.dof import gateway, reference
from portal.dof.ingest import IngestResult, ingest
from portal.dof.parse import decode, parse_text
from portal.models import DaySync, Setting
from portal.services import alerts
from portal.timeutil import dk_today

log = logging.getLogger("portal.jobs")

EXCEL = "https://dofbasen.dk/excel/search_result1.php"
BASE_PARAMS = {"design": "excel", "soeg": "soeg", "obstype": "observationer", "species": "alle", "sortering": "dato"}


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> bytes:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.content


async def live_enabled() -> bool:
    async with SessionLocal() as s:
        row = await s.get(Setting, "live_enabled")
        return True if row is None else bool(row.value)


# ---------------------------------------------------------------- observationer


async def fetch_day(client: httpx.AsyncClient, day: date, notify: bool) -> dict:
    raw = await _get(client, EXCEL, {**BASE_PARAMS, "periode": "dato", "dato": day.strftime("%d-%m-%Y")})
    rows = parse_text(decode(raw))
    async with SessionLocal() as s:
        known = await s.get(DaySync, day)
        existing = (await s.execute(text("SELECT count(*) FROM observations WHERE dato = :d"), {"d": day})).scalar() or 0
        # Sikkerhedsnet: slet ikke data hvis DOFbasen pludselig leverer markant færre rækker
        safe_replace = len(rows) >= existing * 0.6 or existing < 50
        res = await ingest(s, rows, mode="live", replace_dates=(day, day) if safe_replace else None,
                           track_changes=notify)
        # Første hentning af en dag giver kun notifikationer, hvis systemet kørte live i går
        # (midnatsskifte). Ved ny installation/lang nedetid ville alt ellers være 'nyt'.
        prev = await s.get(DaySync, day - timedelta(days=1))
        running = known is not None or (
            prev is not None and prev.source == "live" and prev.fetched_at > datetime.now(timezone.utc) - timedelta(hours=3))
        if known is None:
            s.add(DaySync(dato=day, rows=len(rows), source="live"))
        else:
            known.rows = len(rows)
            known.fetched_at = datetime.now(timezone.utc)
        await s.commit()
        if notify and not running:
            await alerts.seed_threads(s, day)
    await after_ingest(res, notify=notify and running)
    return {"date": day.isoformat(), "rows": len(rows), "new": len(res.new_obsids), "changed": len(res.changed),
            "deleted": res.deleted}


async def after_ingest(res: IngestResult, notify: bool) -> None:
    if res.unknown_species:
        await gateway.enqueue("artdata", {"list": "art"}, priority=gateway.NORMAL, dedupe="artdata:art")
    if res.new_obsids or res.changed or res.deleted:
        await bump("feed")
        await publish("feed", {"type": "feed", "new": len(res.new_obsids), "changed": len(res.changed)})
    if res.observers:
        async with SessionLocal() as s:
            hit = (await s.execute(text("SELECT 1 FROM users WHERE obserkode = ANY(:k) LIMIT 1"),
                                   {"k": list(res.observers)})).scalar()
        if hit and (res.new_obsids or res.changed or res.deleted or not notify):
            await bump("lists")
    if notify and (res.new_obsids or res.changed):
        try:
            async with SessionLocal() as s:
                await alerts.process(s, res.new_obsids, res.changed)
        except Exception:  # noqa: BLE001 – en notifikationsfejl må ikke vælte dataindlæsningen
            log.exception("Notifikationer fejlede")


async def live(client: httpx.AsyncClient, _: dict) -> dict:
    return await fetch_day(client, dk_today(), notify=True)


async def job_day(client: httpx.AsyncClient, p: dict) -> dict:
    return await fetch_day(client, date.fromisoformat(p["date"]), notify=False)


async def job_month(client: httpx.AsyncClient, p: dict) -> dict:
    y, m = int(p["year"]), int(p["month"])
    raw = await _get(client, EXCEL, {**BASE_PARAMS, "periode": "maanedaar", "maaned": m, "aar_first": y, "aar_second": y})
    rows = parse_text(decode(raw))
    first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    async with SessionLocal() as s:
        existing = (await s.execute(text("SELECT count(*) FROM observations WHERE dato BETWEEN :a AND :b"),
                                    {"a": first, "b": last})).scalar() or 0
        safe = len(rows) >= existing * 0.6 or existing < 500
        res = await ingest(s, rows, mode="live", replace_dates=(first, last) if safe else None)
        today = dk_today()
        d = first
        while d <= min(last, today - timedelta(days=1)):
            await s.execute(text("""INSERT INTO day_sync (dato, rows, source) VALUES (:d, 0, 'month')
                                    ON CONFLICT (dato) DO UPDATE SET fetched_at = now(), source = 'month'"""), {"d": d})
            d += timedelta(days=1)
        from portal.services.datasync import record_month
        await record_month(s, y, m, "måned", len(rows))
        await s.commit()
    await after_ingest(res, notify=False)
    return {"year": y, "month": m, "rows": len(rows), "deleted": res.deleted}


async def job_history(client: httpx.AsyncClient, p: dict) -> dict:
    """En observatørs observationer i en årrække (bruges ved ny bruger, hvis arkivet mangler år)."""
    kode = p["obserkode"]
    raw = await _get(client, EXCEL, {**BASE_PARAMS, "periode": "maanedaar", "aar_first": p["year_from"],
                                     "aar_second": p["year_to"], "obserdata": kode})
    rows = parse_text(decode(raw))
    async with SessionLocal() as s:
        res = await ingest(s, rows, mode="archive")
        await s.execute(text("UPDATE users SET history_synced_at = now() WHERE obserkode = :k"), {"k": kode})
        await s.commit()
    res.observers.add(kode)
    await after_ingest(res, notify=False)
    return {"obserkode": kode, "rows": len(rows)}


# ---------------------------------------------------------------- login og medier


async def job_login(client: httpx.AsyncClient, p: dict) -> dict:
    password = (p.get("secret") or {}).get("password", "")
    resp = await client.post(get_settings().dof_login_url, json={"username": p["obserkode"], "password": password})
    valid = False
    if resp.status_code == 200:
        try:
            valid = bool(resp.json().get("token"))
        except ValueError:
            valid = False
    return {"valid": valid}


# Billeder: popobs linker til /image_proxy.php?mode=o&pic=<obsid>_<uploadtid>_<tilfældigt tal>.jpg (filnavnet
# kan ikke gættes). Originalen ligger statisk på service.dofbasen.dk, så brugernes browsere ikke belaster dofbasen.dk.
_IMG = re.compile(r"""image_proxy\.php\?[^'"]*?\bpic=([\w.-]+\.(?:jpe?g|png|gif|webp))""", re.IGNORECASE)
_SOUND = re.compile(r"""<a[^>]+href=['"]([^'"]*sound_proxy\.php[^'"]+)['"]""", re.IGNORECASE)
_STATUS = re.compile(r"""<acronym[^>]*class=["']behandl["'][^>]*title=["']([^"']+)["']""", re.IGNORECASE)
SERVICE_IMAGE = "https://service.dofbasen.dk/media/image/o/"


def parse_popobs(page: str) -> dict:
    page = html.unescape(page)
    images = list(dict.fromkeys(SERVICE_IMAGE + f for f in _IMG.findall(page)))
    sounds = [u if u.startswith("http") else "https://dofbasen.dk" + (u if u.startswith("/") else "/" + u)
              for u in dict.fromkeys(_SOUND.findall(page))]
    m = _STATUS.search(page)
    return {"status": m.group(1) if m else "", "images": images, "sounds": sounds}


async def job_media(client: httpx.AsyncClient, p: dict) -> dict:
    obsid = int(p["obsid"])
    raw = await _get(client, "https://dofbasen.dk/popobs.php", {"obsid": obsid, "summering": "tur", "obs": "obs"})
    data = {"obsid": obsid, **parse_popobs(decode(raw)), "fetched": datetime.now(timezone.utc).isoformat()}
    await set_json(f"media:{obsid}", data, 7 * 86400)
    return {"images": len(data["images"]), "sounds": len(data["sounds"])}


async def media_for(obsid: int) -> dict | None:
    return await get_json(f"media:{obsid}")


# ---------------------------------------------------------------- referencedata


async def job_artdata(client: httpx.AsyncClient, p: dict) -> dict:
    kind = p.get("list", "art")
    page = decode(await _get(client, "https://dofbasen.dk/opslag/artdata.php", {"list": kind}))
    rows = reference.parse_artdata(page, kind)
    async with SessionLocal() as s:
        n = await reference.apply_artdata(s, rows)
    return {"list": kind, "species": n}


async def job_status(client: httpx.AsyncClient, p: dict) -> dict:
    klass = p["klass"]
    url = "https://dofbasen.dk/opslag/sudata.php" if klass == "SU" else "https://dofbasen.dk/opslag/subdata.php"
    ids = reference.parse_status_list(decode(await _get(client, url)))
    async with SessionLocal() as s:
        n = await reference.apply_status(s, klass, ids)
    return {"klass": klass, "species": n}


async def job_bemaerk(client: httpx.AsyncClient, p: dict) -> dict:
    afd = int(p["afdeling_id"])
    code = reference.BEMAERK_LIST_BY_AFDELING[afd]
    thresholds = reference.parse_bemaerk(decode(await _get(client, "https://dofbasen.dk/opslag/bemaerk.php", {"list": code})))
    async with SessionLocal() as s:
        n = await reference.apply_bemaerk(s, afd, thresholds)
    return {"afdeling": afd, "rows": n}


async def job_wsdata(client: httpx.AsyncClient, p: dict) -> dict:
    season = p["season"]
    rows = reference.parse_wsdata(decode(await _get(client, "https://dofbasen.dk/opslag/wsdata.php", {"tid": season})))
    async with SessionLocal() as s:
        n = await reference.apply_windows(s, season, rows)
    return {"season": season, "rows": n}


HANDLERS = {
    "day": job_day,
    "month": job_month,
    "history": job_history,
    "login": job_login,
    "media": job_media,
    "artdata": job_artdata,
    "status": job_status,
    "bemaerk": job_bemaerk,
    "wsdata": job_wsdata,
}


async def enqueue_reference_refresh() -> int:
    n = 0
    for kind in ("art", "hybrid", "ub", "andre"):
        await gateway.enqueue("artdata", {"list": kind}, priority=gateway.NORMAL, dedupe=f"artdata:{kind}")
        n += 1
    for klass in ("SU", "SUB"):
        await gateway.enqueue("status", {"klass": klass}, priority=gateway.NORMAL, dedupe=f"status:{klass}")
        n += 1
    for afd in reference.BEMAERK_LIST_BY_AFDELING:
        await gateway.enqueue("bemaerk", {"afdeling_id": afd}, priority=gateway.NORMAL, dedupe=f"bemaerk:{afd}")
        n += 1
    for season in ("sommer", "vinter"):
        await gateway.enqueue("wsdata", {"season": season}, priority=gateway.NORMAL, dedupe=f"wsdata:{season}")
        n += 1
    return n


async def enqueue_day(d: date, priority: int = gateway.NORMAL) -> str:
    return await gateway.enqueue("day", {"date": d.isoformat()}, priority=priority, dedupe=f"day:{d.isoformat()}")


async def enqueue_month(y: int, m: int, priority: int = gateway.BULK) -> str:
    return await gateway.enqueue("month", {"year": int(y), "month": int(m)}, priority=priority,
                                 dedupe=f"month:{int(y)}-{int(m):02d}")


async def enqueue_gap_fill(max_days: int = 45) -> int:
    """Dage uden komplet hentning (hverken arkiv, måned eller live) lægges i kø – nyeste først."""
    from portal.cache import kv
    from portal.dof.archive import pending_files

    if await kv().exists("archive:lock") or await pending_files():
        return 0  # arkivimporten dækker sandsynligvis hullerne – vent til den er færdig
    today = dk_today()
    async with SessionLocal() as s:
        synced = {d for (d,) in (await s.execute(select(DaySync.dato).where(
            DaySync.dato >= today - timedelta(days=max_days)))).all()}
    n = 0
    for i in range(1, max_days + 1):
        d = today - timedelta(days=i)
        if d not in synced:
            await enqueue_day(d, gateway.BULK)
            n += 1
    return n
