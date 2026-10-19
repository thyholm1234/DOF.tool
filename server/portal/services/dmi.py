"""DMI's frie data: aktuelle målinger (metObs), prognose (HARMONIE via EDR) og klimadata.

Alle svar caches i Valkey; DMI's API svarer jævnligt 429, så kald sker sekventielt med backoff.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx

from portal.cache import get_json, set_json
from portal.config import get_settings

log = logging.getLogger("portal.dmi")

# Klassiske danske træksteder. 'sea' = også seawatch-lokalitet; 'coast' = den retning havet ligger (grader).
HOTSPOTS = [
    {"id": "skagen", "navn": "Skagen (Grenen)", "lat": 57.737, "lon": 10.62, "sea": True, "onshore": (270, 360)},
    {"id": "hanstholm", "navn": "Hanstholm", "lat": 57.122, "lon": 8.60, "sea": True, "onshore": (250, 360)},
    {"id": "blaavand", "navn": "Blåvandshuk", "lat": 55.558, "lon": 8.08, "sea": True, "onshore": (200, 320)},
    {"id": "rojensodde", "navn": "Røjensodde/Sjællands Odde", "lat": 55.97, "lon": 11.30, "sea": False},
    {"id": "stevns", "navn": "Stevns Fyr", "lat": 55.29, "lon": 12.45, "sea": False},
    {"id": "gedser", "navn": "Gedser Odde", "lat": 54.56, "lon": 11.97, "sea": True, "onshore": (60, 200)},
    {"id": "hyllekrog", "navn": "Hyllekrog", "lat": 54.58, "lon": 11.48, "sea": False},
    {"id": "christianso", "navn": "Christiansø", "lat": 55.32, "lon": 15.19, "sea": False},
]

UA = {"User-Agent": "DOF.tool/1.0 (feltornitolog-portal)"}


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    for attempt in range(4):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2 + attempt * 3)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.info("DMI-fejl %s: %s", url, exc)
            await asyncio.sleep(1 + attempt)
    return None


# Vindfelt til partikelanimationen: DMI's HARMONIE-model i et regulært gitter via Open-Meteo (frit, én forespørgsel
# med alle punkter). DMI's egen EDR-cube er for langsom/grov til formålet; stationerne er for spredte.
# Open-Meteo tæller hvert punkt som et kald (gratis: 600/min, 10.000/døgn), så gitteret er 0,5° (153 punkter), og
# timeværdierne hentes pr. døgn: afsluttede døgn én gang, i dag højst hvert 45. minut. "Nu" og middelvind deler data.
GRID = {"lat0": 54.0, "lon0": 7.5, "step": 0.5, "ny": 9, "nx": 17}
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
DAY_HOURS = range(6, 19)  # kl. 6–18: dagtimerne, hvor observationerne (og det synlige træk) kommer fra


def _grid_points() -> list[tuple[float, float]]:
    g = GRID
    return [(round(g["lat0"] + j * g["step"], 3), round(g["lon0"] + i * g["step"], 3)) for j in range(g["ny"]) for i in range(g["nx"])]


async def _days_hours(start, end) -> dict | None:
    """{dato: {"u": [[24 timer] pr. punkt], "v": [...]}} for alle døgn start..end (dansk tid). None ved fejl.
    Manglende døgn hentes i ét kald (Open-Meteo tæller pr. punkt, ikke pr. døgn, op til 2 uger)."""
    from portal.timeutil import dk_today
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    out = {}
    for d in days:
        hit = await get_json(f"wind:day:{d.isoformat()}")
        if hit is not None:
            out[d] = hit
    missing = [d for d in days if d not in out]
    if not missing:
        return out
    pts = _grid_points()
    body = {"latitude": ",".join(str(a) for a, _ in pts), "longitude": ",".join(str(o) for _, o in pts),
            "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms", "models": "dmi_seamless",
            "timezone": "Europe/Copenhagen", "start_date": missing[0].isoformat(), "end_date": missing[-1].isoformat()}
    try:
        async with httpx.AsyncClient(timeout=60, headers=UA) as client:
            r = await client.post(OPEN_METEO, data=body)
            r.raise_for_status()
            rows = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Vind for %s–%s fejlede: %s", missing[0], missing[-1], exc)
        return None
    rows = rows if isinstance(rows, list) else [rows]
    today = dk_today()
    for di, d in enumerate(d for d in days if missing[0] <= d <= missing[-1]):
        us, vs = [], []
        for row in rows:
            hr = row.get("hourly") or {}
            sp_all, dir_all = hr.get("wind_speed_10m", []), hr.get("wind_direction_10m", [])
            u, v = [], []
            for h in range(di * 24, di * 24 + 24):
                sp = sp_all[h] if h < len(sp_all) else None
                dd = dir_all[h] if h < len(dir_all) else None
                if sp is None or dd is None:
                    u.append(None); v.append(None)
                    continue
                rad = math.radians(dd)  # retning vinden kommer FRA → u/v mod
                u.append(round(-sp * math.sin(rad), 2)); v.append(round(-sp * math.cos(rad), 2))
            us.append(u); vs.append(v)
        out[d] = {"u": us, "v": vs}
        await set_json(f"wind:day:{d.isoformat()}", out[d], 45 * 60 if d >= today else 30 * 86400)
    return out


async def wind_grid() -> dict | None:
    """Aktuel vind: {lat0, lon0, step, nx, ny, u: [...], v: [...], time} – rækker fra syd mod nord, u/v i m/s."""
    from portal.timeutil import dk_now
    now = dk_now()
    days = await _days_hours(now.date(), now.date())
    if not days:
        return None
    data = days[now.date()]
    h = now.hour
    u = [(row[h] if h < len(row) and row[h] is not None else 0.0) for row in data["u"]]
    v = [(row[h] if h < len(row) and row[h] is not None else 0.0) for row in data["v"]]
    return {**GRID, "u": u, "v": v, "time": f"{now.date().isoformat()}T{h:02d}:00", "source": "DMI HARMONIE via Open-Meteo"}


async def wind_grid_period(start, end) -> dict | None:
    """Middelvind (vektormiddel af u/v) kl. 6–18 dansk tid fra `start` til og med `end` – til at summere flere døgn."""
    from portal.timeutil import dk_now
    now = dk_now()
    n_pts = GRID["nx"] * GRID["ny"]
    su, sv, n = [0.0] * n_pts, [0.0] * n_pts, [0] * n_pts
    days = await _days_hours(start, end)
    if days is None:
        return None
    for day, data in sorted(days.items()):
        last = now.hour if day == now.date() else 23  # i dag: kun timer der er gået
        for k in range(min(n_pts, len(data["u"]))):
            for hr in DAY_HOURS:
                if hr > last or hr >= len(data["u"][k]) or data["u"][k][hr] is None:
                    continue
                su[k] += data["u"][k][hr]; sv[k] += data["v"][k][hr]; n[k] += 1
    return {**GRID, "u": [round(a / c, 2) if c else 0.0 for a, c in zip(su, n)],
            "v": [round(b / c, 2) if c else 0.0 for b, c in zip(sv, n)],
            "start": start.isoformat(), "end": end.isoformat(), "hours": max(n, default=0),
            "source": "DMI HARMONIE via Open-Meteo", "mean": True}


async def current_weather() -> list[dict]:
    hit = await get_json("dmi:current")
    if hit is not None:
        return hit
    base = f"{get_settings().dmi_base}/v2/metObs/collections/observation/items"
    stations: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=30, headers=UA) as client:
        for param, key in (("wind_speed", "speed"), ("wind_dir", "dir"), ("temp_dry", "temp"), ("wind_max", "gust")):
            data = await _get(client, base, {"parameterId": param, "period": "latest-hour", "bbox": "7.5,54.4,15.3,57.9",
                                             "limit": "2000"})
            for f in (data or {}).get("features", []):
                p = f["properties"]
                sid = p.get("stationId")
                lon, lat = f["geometry"]["coordinates"][:2]
                st = stations.setdefault(sid, {"id": sid, "lat": lat, "lon": lon, "observed": p.get("observed")})
                if p.get("observed", "") >= (st.get(f"_{key}_t") or ""):
                    st[key] = p.get("value")
                    st[f"_{key}_t"] = p.get("observed")
    out = [{k: v for k, v in s.items() if not k.startswith("_")} for s in stations.values() if s.get("speed") is not None]
    await set_json("dmi:current", out, 600)
    return out


def _wind(u: float, v: float) -> tuple[float, float]:
    speed = math.hypot(u, v)
    direction = (math.degrees(math.atan2(-u, -v)) + 360) % 360  # meteorologisk: hvor vinden kommer FRA
    return speed, direction


def _in_sector(d: float, lo: float, hi: float) -> bool:
    return lo <= d <= hi if lo <= hi else d >= lo or d <= hi


def migration_score(month: int, speed: float, direction: float, rain: float, spot: dict) -> dict:
    """Tommelfingerregler for landfugletræk og seawatch (0–5). Forklares i UI'et."""
    land = 2.5
    reason = []
    if month in (3, 4, 5):
        if _in_sector(direction, 120, 240):
            land += 1.5
            reason.append("medvind mod nord")
        elif _in_sector(direction, 300, 60):
            land -= 1.0
            reason.append("modvind")
    elif month in (8, 9, 10, 11):
        if _in_sector(direction, 300, 80):
            land += 1.5
            reason.append("nordlig medvind")
        elif _in_sector(direction, 150, 250):
            land -= 1.0
            reason.append("sydlig modvind")
    else:
        land -= 1.0
    if speed < 2:
        land -= 0.3
    elif speed > 11:
        land -= 1.5
        reason.append("hård vind")
    if rain > 0.5:
        land -= 1.5
        reason.append("regn")
    sea = None
    if spot.get("sea"):
        lo, hi = spot["onshore"]
        sea = 1.0
        if _in_sector(direction, lo, hi):
            sea += 1.5 + min(speed, 18) / 6
        if month in (8, 9, 10, 11):
            sea += 0.5
    return {"land": round(max(0, min(5, land)), 1), "sea": round(max(0, min(5, sea)), 1) if sea is not None else None,
            "reason": ", ".join(reason)}


async def _point_hours(client: httpx.AsyncClient, spot: dict) -> list[dict] | None:
    """HARMONIE-prognose (timer) for et punkt med trækvurdering pr. time."""
    base = f"{get_settings().dmi_base}/v1/forecastedr/collections/harmonie_dini_sf/position"
    data = await _get(client, base, {
        "coords": f"POINT({spot['lon']} {spot['lat']})",
        "parameter-name": "wind-10m-u,wind-10m-v,temperature-2m,total-precipitation,fraction-of-cloud-cover",
        "crs": "crs84", "f": "CoverageJSON",
    })
    if not data:
        return None
    times = data["domain"]["axes"]["t"]["values"]
    rng = {k: v["values"] for k, v in data["ranges"].items()}
    clouds = rng.get("fraction-of-cloud-cover") or [None] * len(times)
    hours = []
    prev_precip = None
    for i, t in enumerate(times):
        u, v = rng["wind-10m-u"][i], rng["wind-10m-v"][i]
        if u is None or v is None:
            continue
        speed, direction = _wind(u, v)
        acc = rng.get("total-precipitation", [None] * len(times))[i] or 0.0
        rain = max(0.0, acc - prev_precip) if prev_precip is not None else 0.0
        prev_precip = acc
        ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
        hours.append({"t": t, "speed": round(speed, 1), "dir": round(direction),
                      "temp": round(rng["temperature-2m"][i] - 273.15, 1) if rng.get("temperature-2m") else None,
                      "rain": round(rain, 1), "cloud": round(clouds[i] * 100) if clouds[i] is not None else None,
                      **migration_score(ts.month, speed, direction, rain, spot)})
    return hours


async def forecast() -> list[dict]:
    hit = await get_json("dmi:forecast")
    if hit is not None:
        return hit
    out = []
    async with httpx.AsyncClient(timeout=60, headers=UA) as client:
        for spot in HOTSPOTS:
            hours = await _point_hours(client, spot)
            if hours is None:
                continue
            out.append({**{k: v for k, v in spot.items() if k != "onshore"}, "hours": hours})
            await asyncio.sleep(0.5)
    if out:
        await set_json("dmi:forecast", out, 3600)
    return out


async def point_forecast(key: str, lat: float, lon: float) -> list[dict]:
    """Prognose for et vilkårligt punkt (fx en kalenders lokalitet), cachet 1 time."""
    hit = await get_json(f"dmi:fc:{key}")
    if hit is not None:
        return hit
    async with httpx.AsyncClient(timeout=60, headers=UA) as client:
        hours = await _point_hours(client, {"lat": lat, "lon": lon, "sea": False})
    if hours:
        await set_json(f"dmi:fc:{key}", hours, 3600)
    return hours or []


async def climate_months(since_year: int = 2000) -> list[tuple[int, int, float]]:
    """Landsgennemsnit af månedsmiddeltemperatur (countryValue)."""
    base = f"{get_settings().dmi_base}/v2/climateData/collections/countryValue/items"
    start = datetime(since_year, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[tuple[int, int], float] = {}
    async with httpx.AsyncClient(timeout=60, headers=UA) as client:
        offset = 0
        while True:
            data = await _get(client, base, {"parameterId": "mean_temp", "timeResolution": "month",
                                             "datetime": f"{start}/{end}", "limit": "1000", "offset": str(offset)})
            feats = (data or {}).get("features", [])
            for f in feats:
                p = f["properties"]
                d = datetime.fromisoformat(p["from"]).astimezone(timezone(timedelta(hours=2)))
                if p.get("value") is not None:
                    out[(d.year, d.month)] = float(p["value"])
            if len(feats) < 1000:
                break
            offset += 1000
    return [(y, m, t) for (y, m), t in sorted(out.items())]
