"""Vejrarkiv: DMI's 10 km-grid (Det Danske Kvadratnet) som dagsvejr pr. kvadrat.

Hentning og parsing følger DML-Bird-Populations (dkdml/dmi_bulk.py):
- Kilde: https://opendataapi.dmi.dk/v2/climateData/bulk/10kmgridValue/<år>/<år>-<mm>.zip (ingen nøgle).
  Én zip pr. måned (≈0,5 GB) med én JSON-Lines-fil pr. UTC-døgn – én GeoJSON-feature pr. linje.
- Én måned ad gangen; filen parses direkte fra zip (ingen udpakning) i flere processer og slettes bagefter.
- Kun timeværdier for de ønskede parametre afkodes (hurtigt bytefilter før JSON); dubletter for samme
  (kvadrat, parameter, time) afgøres af nyeste `calculatedAt`.

Portalen gemmer pr. kvadrat og dansk kalenderdøgn: middel/min/maks-temperatur, middelvind, maks. vindstød,
nedbør, skydække og solskin – og morgenen kl. 4–10 (temperatur, vind, vektormiddel af vindretning, nedbør),
som er det vejr, der betyder mest for dagens træk. Døgnet ved månedsskift mangler de 1–2 nattetimer, der ligger
i forrige måneds fil (påvirker ikke morgenværdierne).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from multiprocessing import get_context
from pathlib import Path

import httpx
from sqlalchemy import text

from portal.cache import kv
from portal.config import get_settings
from portal.db import SessionLocal
from portal.timeutil import dk_today

log = logging.getLogger("portal.weather")

BASE = "https://opendataapi.dmi.dk/v2/climateData/bulk/10kmgridValue/"
UA = "DOF.tool-weather/1.0 (bird portal; one monthly file at a time)"
QUEUE = "weather:q"
QUEUED = "weather:queued"
LIVE = "weather:live"
COOLDOWN_S = 5
WANTED = {"mean_temp", "mean_wind_speed", "max_wind_speed_10min", "mean_wind_dir", "acc_precip",
          "bright_sunshine", "mean_cloud_cover"}
MORNING = range(4, 10)  # lokal tid, timer der starter kl. 4–9
COLS = ["temp", "temp_min", "temp_max", "wind", "gust", "precip", "cloud", "sun", "m_temp", "m_wind", "m_dir", "m_precip"]


def month_key(y: int, m: int) -> str:
    return f"{y}-{m:02d}"


def url(y: int, m: int) -> str:
    return f"{BASE}{y}/{month_key(y, m)}.zip"


def raw_dir() -> Path:
    p = Path(get_settings().weather_dir) / "raw"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cell_code(cell_id: str) -> int:
    """'10km_617_57' → 617057 (N*1000+E, samme kode som locations.cell10)."""
    _, n, e = cell_id.split("_")
    return int(n) * 1000 + int(e)


# ---------------------------------------------------------------- parsing (kører i underprocesser)

# Akkumulator pr. (kvadrat, lokal dato):
# 0 t_sum 1 t_n 2 t_min 3 t_max 4 w_sum 5 w_n 6 g_max 7 p_sum 8 c_sum 9 c_n 10 s_sum
# 11 mt_sum 12 mt_n 13 mw_sum 14 mw_n 15 mu 16 mv 17 mp_sum 18 p_n
_NACC = 19


def _new_acc() -> list:
    a = [0.0] * _NACC
    a[2], a[3], a[6] = math.inf, -math.inf, -math.inf
    return a


def parse_member(zip_path: str, member: str) -> dict:
    """Én døgnfil → {(kvadrat, 'YYYY-MM-DD'): akkumulator}. Kaldes i en underproces."""
    import orjson
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Copenhagen")
    latest: dict[tuple, tuple] = {}
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as fh:
        for line in fh:
            if b'"hour"' not in line:
                continue
            # Hurtigt filter før JSON-afkodning: læs parameterId direkte fra bytes (tåler mellemrum efter kolon)
            i = line.find(b'"parameterId"')
            if i < 0:
                continue
            a = line.find(b'"', i + 13)
            b = line.find(b'"', a + 1)
            if line[a + 1:b].decode() not in WANTED:
                continue
            p = orjson.loads(line)["properties"]
            val = p.get("value")
            if val is None:
                continue
            key = (p["cellId"], p["parameterId"], p["from"])
            prev = latest.get(key)
            calc = p.get("calculatedAt") or ""
            if prev is None or calc > prev[0]:
                latest[key] = (calc, val)
    hours: dict[tuple, dict] = {}
    for (cell, pid, frm), (_, val) in latest.items():
        hours.setdefault((cell, frm), {})[pid] = val
    local: dict[str, tuple[str, int]] = {}
    acc: dict[tuple, list] = {}
    for (cell, frm), v in hours.items():
        lt = local.get(frm)
        if lt is None:
            dt = datetime.fromisoformat(frm.replace("Z", "+00:00")).astimezone(tz)
            lt = local[frm] = (dt.date().isoformat(), dt.hour)
        d, hr = lt
        k = (cell_code(cell), d)
        a = acc.get(k)
        if a is None:
            a = acc[k] = _new_acc()
        t = v.get("mean_temp")
        if t is not None:
            a[0] += t; a[1] += 1; a[2] = min(a[2], t); a[3] = max(a[3], t)  # noqa: E702
        w = v.get("mean_wind_speed")
        if w is not None:
            a[4] += w; a[5] += 1  # noqa: E702
        g = v.get("max_wind_speed_10min")
        if g is not None:
            a[6] = max(a[6], g)
        pr = v.get("acc_precip")
        if pr is not None:
            a[7] += pr; a[18] += 1  # noqa: E702
        c = v.get("mean_cloud_cover")
        if c is not None:
            a[8] += c; a[9] += 1  # noqa: E702
        s = v.get("bright_sunshine")
        if s is not None:
            a[10] += s
        if hr in MORNING:
            if t is not None:
                a[11] += t; a[12] += 1  # noqa: E702
            if w is not None:
                a[13] += w; a[14] += 1  # noqa: E702
                wd = v.get("mean_wind_dir")
                if wd is not None:
                    a[15] += w * math.sin(math.radians(wd)); a[16] += w * math.cos(math.radians(wd))  # noqa: E702
            if pr is not None:
                a[17] += pr
    return acc


def merge(into: dict, part: dict) -> None:
    for k, b in part.items():
        a = into.get(k)
        if a is None:
            into[k] = b
            continue
        for i in range(_NACC):
            if i in (2,):
                a[i] = min(a[i], b[i])
            elif i in (3, 6):
                a[i] = max(a[i], b[i])
            else:
                a[i] += b[i]


def finalize(acc: list) -> dict:
    def mean(s, n):
        return round(s / n, 2) if n else None

    m_dir = None
    if acc[14] and (acc[15] or acc[16]):
        m_dir = round(math.degrees(math.atan2(acc[15], acc[16])) % 360, 0)
    return {
        "temp": mean(acc[0], acc[1]),
        "temp_min": round(acc[2], 2) if acc[1] else None,
        "temp_max": round(acc[3], 2) if acc[1] else None,
        "wind": mean(acc[4], acc[5]),
        "gust": round(acc[6], 2) if acc[6] > -math.inf else None,
        "precip": round(acc[7], 2) if acc[18] else None,
        "cloud": mean(acc[8], acc[9]),
        "sun": round(acc[10], 1) if acc[1] else None,
        "m_temp": mean(acc[11], acc[12]),
        "m_wind": mean(acc[13], acc[14]),
        "m_dir": m_dir,
        "m_precip": round(acc[17], 2) if acc[12] or acc[14] else None,
    }


def parse_month_file(zip_path: str, y: int, m: int, workers: int, progress=None) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        members = sorted(n for n in zf.namelist() if n.endswith(".txt"))
    total: dict[tuple, list] = {}
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(members) or 1)), mp_context=get_context("spawn")) as ex:
        futs = [ex.submit(parse_member, zip_path, mb) for mb in members]
        for i, f in enumerate(futs, start=1):
            merge(total, f.result())
            if progress:
                progress(i, len(members))
    prefix = month_key(y, m)
    return [{"cell": cell, "dato": date.fromisoformat(d), **finalize(a)}
            for (cell, d), a in total.items() if d.startswith(prefix)]


# ---------------------------------------------------------------- kø og kørsel


async def enqueue(months: list[tuple[int, int]], only_missing: bool = False) -> int:
    """Læg måneder i vejrkøen (nyeste først). only_missing: spring færdige, afsluttede måneder over."""
    done: set[str] = set()
    if only_missing:
        async with SessionLocal() as s:
            done = {f"{y}-{m:02d}" for y, m in (await s.execute(text("""
                SELECT year, month FROM weather_months
                WHERE status IN ('done', 'unavailable')
                  AND updated_at > make_date(year, month, 1) + interval '2 months'
            """))).all()}
    r = kv()
    n = 0
    for y, m in sorted(months, reverse=True):
        k = month_key(y, m)
        if k in done or (y, m) > (dk_today().year, dk_today().month):
            continue
        if await r.sadd(QUEUED, k):
            await r.rpush(QUEUE, k)
            n += 1
    return n


async def queue_list() -> list[str]:
    return await kv().lrange(QUEUE, 0, -1)


async def clear_queue() -> int:
    r = kv()
    n = await r.llen(QUEUE)
    await r.delete(QUEUE, QUEUED)
    return n


async def _live(**fields) -> None:
    await kv().hset(LIVE, mapping={k: str(v) for k, v in fields.items()})


async def download(y: int, m: int) -> tuple[Path, dict] | None:
    dst = raw_dir() / f"{month_key(y, m)}.zip"
    part = dst.with_suffix(".zip.part")
    h = hashlib.sha256()
    n = 0
    t0 = time.time()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30), follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        async with client.stream("GET", url(y, m)) as r:
            if r.status_code == 404:
                return None
            r.raise_for_status()
            meta = {"etag": r.headers.get("etag"), "last_modified": r.headers.get("last-modified")}
            with open(part, "wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    fh.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
                    if n % (16 << 20) < (1 << 20):
                        await _live(state="downloading", mb=round(n / 1e6), mbps=round(n / 1e6 / max(time.time() - t0, 1e-3), 1))
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise RuntimeError("Download er ikke en gyldig zip-fil")
    part.replace(dst)
    return dst, {**meta, "bytes": n, "sha256": h.hexdigest()}


async def _save(y: int, m: int, **fields) -> None:
    async with SessionLocal() as s:
        await s.execute(text("""
            INSERT INTO weather_months (year, month, status, rows, cells, bytes, seconds, etag, last_modified, error, updated_at)
            VALUES (:y, :m, :status, :rows, :cells, :bytes, :seconds, :etag, :lm, :error, now())
            ON CONFLICT (year, month) DO UPDATE SET status = :status, rows = :rows, cells = :cells, bytes = :bytes,
              seconds = :seconds, etag = :etag, last_modified = :lm, error = :error, updated_at = now()
        """), {"y": y, "m": m, "status": fields.get("status"), "rows": fields.get("rows", 0), "cells": fields.get("cells", 0),
               "bytes": fields.get("bytes", 0), "seconds": fields.get("seconds"), "etag": fields.get("etag"),
               "lm": fields.get("last_modified"), "error": fields.get("error")})
        await s.commit()


async def _workers() -> int:
    from portal.services.datasync import get_plan
    async with SessionLocal() as s:
        w = (await get_plan(s, "weather")).get("workers") or 0
    return w or max(2, min((os.cpu_count() or 4) - 2, 8))


async def process_month(y: int, m: int) -> dict:
    k = month_key(y, m)
    t0 = time.time()
    await _live(month=k, state="downloading", mb=0, day=0, days=0, started=datetime.now(timezone.utc).isoformat())
    try:
        got = await download(y, m)
        if got is None:
            await _save(y, m, status="unavailable", error="DMI har ingen fil for måneden (404)")
            return {"month": k, "status": "unavailable"}
        zp, meta = got
        loop = asyncio.get_running_loop()

        def progress(i, n):
            asyncio.run_coroutine_threadsafe(_live(state="parsing", day=i, days=n), loop)

        workers = await _workers()
        await _live(state="parsing", day=0, days=0)
        rows = await asyncio.to_thread(parse_month_file, str(zp), y, m, workers, progress)
        await _live(state="saving", rows=len(rows))
        async with SessionLocal() as s:
            for i in range(0, len(rows), 5000):
                await s.execute(text(f"""
                    INSERT INTO weather_cell_day (cell, dato, {', '.join(COLS)})
                    VALUES (:cell, :dato, {', '.join(':' + c for c in COLS)})
                    ON CONFLICT (cell, dato) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in COLS)}
                """), rows[i:i + 5000])
            await s.commit()
        zp.unlink(missing_ok=True)
        cells = len({r["cell"] for r in rows})
        await _save(y, m, status="done" if rows else "empty", rows=len(rows), cells=cells, bytes=meta["bytes"],
                    seconds=round(time.time() - t0, 1), etag=meta["etag"], last_modified=meta["last_modified"])
        log.info("Vejr %s: %s dagsrækker, %s kvadrater, %.0f MB, %.0f s", k, len(rows), cells, meta["bytes"] / 1e6, time.time() - t0)
        return {"month": k, "rows": len(rows), "cells": cells}
    except Exception as exc:  # noqa: BLE001
        log.exception("Vejr %s fejlede", k)
        await _save(y, m, status="error", error=f"{type(exc).__name__}: {exc}"[:1000], seconds=round(time.time() - t0, 1))
        for p in raw_dir().glob(f"{k}.zip*"):
            p.unlink(missing_ok=True)
        return {"month": k, "error": str(exc)}
    finally:
        await kv().delete(LIVE)


async def loop() -> None:
    """Workerens vejrkø: én månedsfil ad gangen med en kort pause imellem."""
    r = kv()
    for p in raw_dir().glob("*.part"):
        p.unlink(missing_ok=True)
    await r.delete(LIVE)
    while True:
        try:
            item = await r.blpop([QUEUE], timeout=5)
            if not item:
                continue
            k = item[1]
            await r.srem(QUEUED, k)
            y, m = int(k[:4]), int(k[5:])
            await process_month(y, m)
            await asyncio.sleep(COOLDOWN_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – løkken må aldrig dø
            log.exception("Vejrkø-fejl")
            await asyncio.sleep(10)


# ---------------------------------------------------------------- status og opslag


async def delete_month(session, y: int, m: int) -> int:
    """Slet en måneds vejrdata (døgnværdier + status), så måneden står som ikke hentet."""
    live = await kv().hgetall(LIVE)
    if live and live.get("month") == month_key(y, m):
        raise ValueError("Måneden er ved at blive hentet – vent til den er færdig")
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    n = (await session.execute(text("DELETE FROM weather_cell_day WHERE dato >= :a AND dato < :b"), {"a": start, "b": end})).rowcount
    await session.execute(text("DELETE FROM weather_months WHERE year = :y AND month = :m"), {"y": y, "m": m})
    k = month_key(y, m)
    await kv().lrem(QUEUE, 0, k)
    await kv().srem(QUEUED, k)
    await session.commit()
    return n or 0


async def status(session) -> dict:
    months = {}
    for y, m, st, rows, cells, b, sec, err, at in (await session.execute(text("""
        SELECT year, month, status, rows, cells, bytes, seconds, error, updated_at FROM weather_months
    """))).all():
        months[f"{y}-{m:02d}"] = {"status": st, "rows": rows, "cells": cells, "mb": round((b or 0) / 1e6),
                                  "seconds": sec, "error": err, "updated": at.isoformat()}
    total = (await session.execute(text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'weather_cell_day'"))).scalar()
    live = await kv().hgetall(LIVE)
    return {"months": months, "queue": await queue_list(), "live": live or None, "rows_est": max(int(total or 0), 0)}


async def day_for_location(session, loknr: int, day: date) -> dict | None:
    row = (await session.execute(text("""
        SELECT w.* FROM locations l JOIN weather_cell_day w ON w.cell = l.cell10 AND w.dato = :d WHERE l.loknr = :l
    """), {"l": loknr, "d": day})).mappings().first()
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()} if row else None
