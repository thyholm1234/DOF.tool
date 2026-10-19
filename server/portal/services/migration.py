"""Trækkort: hvilken vej trækker fuglegrupperne, og hvordan blæser vinden (kl. 12) – i dag og op til 2 dage tilbage.

Retning kommer fra adfærdskoden (N, NØ, Ø, SØ, S, SV, V, NV = trækkende mod), vægtet med antal individer.
Vinden er DMI-stationernes 10-minutters middelvind kl. 12 dansk tid (i dag: seneste time, hvis klokken ikke er 12).

Systematik: arterne grupperes efter slægt (første ord i det latinske navn). Grupperne er praktiske trækgrupper,
ikke taksonomiske – rovfugle deles i svæveflyvere (termik, undgår åbent vand) og aktive flyvere. Spurvefugle er
alle slægter fra Pirol og frem i DOF's systematiske rækkefølge (undtagen drosler). Åbne kilder som IOC World Bird
List (orden/familie) og AVONET (træk- og flyveegenskaber) kan bruges, hvis inddelingen skal automatiseres.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, get_json, set_json
from portal.config import get_settings
from portal.services.dmi import UA, _get
from portal.timeutil import TZ, dk_now, dk_today

GROUPS = {
    "rov_svaev": "Rovfugle – svævere",
    "rov_aktiv": "Rovfugle – aktive",
    "vade": "Vadefugle",
    "gaes": "Gæs",
    "hav": "Havfugle",
    "spurv": "Spurvefugle",
    "drossel": "Drosler",
}
GENERA = {
    "rov_svaev": {"Buteo", "Pernis", "Milvus", "Aquila", "Clanga", "Hieraaetus", "Haliaeetus", "Circaetus", "Gyps",
                  "Aegypius", "Neophron"},
    "rov_aktiv": {"Accipiter", "Astur", "Tachyspiza", "Falco", "Circus", "Pandion", "Elanus"},
    "vade": {"Haematopus", "Recurvirostra", "Himantopus", "Burhinus", "Glareola", "Cursorius", "Charadrius", "Anarhynchus",
             "Eudromias", "Pluvialis", "Vanellus", "Calidris", "Philomachus", "Limicola", "Tryngites", "Limnodromus",
             "Limosa", "Numenius", "Bartramia", "Tringa", "Actitis", "Xenus", "Arenaria", "Gallinago", "Lymnocryptes",
             "Scolopax", "Phalaropus", "Steganopus", "Charadrii", "Scolopacidae"},
    "gaes": {"Anser", "Branta", "Alopochen"},
    "hav": {"Gavia", "Fulmarus", "Puffinus", "Ardenna", "Calonectris", "Hydrobates", "Oceanodroma", "Oceanites",
            "Morus", "Stercorarius", "Alca", "Uria", "Cepphus", "Fratercula", "Alle", "Rissa", "Xema", "Hydrocoloeus",
            "Sterna", "Thalasseus", "Sternula", "Chlidonias", "Gelochelidon", "Hydroprogne", "Melanitta", "Somateria",
            "Polysticta", "Clangula", "Stercorariini", "Alcinae", "Alcidae", "Gaviidae", "Procellariidae", "Sternidae"},
    "drossel": {"Turdus"},
}
PASSERINE_FROM = 5330  # DOF-sortering: Pirol (5360) er første spurvefugl, papegøjer (5320) står før
DIRS = {"N": 0, "NØ": 45, "Ø": 90, "SØ": 135, "S": 180, "SV": 225, "V": 270, "NV": 315}
BBOX = "7.5,54.4,15.3,57.9"


async def species_groups(session: AsyncSession) -> dict[int, str]:
    """artnr → gruppe for alle arter (underarter følger slægten)."""
    async def produce():
        rows = (await session.execute(text("SELECT artnr, latin, sortering FROM species WHERE latin IS NOT NULL"))).all()
        passer_genera = {lat.split()[0] for _, lat, srt in rows if srt and srt >= PASSERINE_FROM}
        out = {}
        for artnr, latin, _ in rows:
            genus = latin.split()[0].strip("[]")
            group = next((g for g, gen in GENERA.items() if genus in gen), None)
            if group is None and genus in passer_genera:
                group = "spurv"
            if group:
                out[str(artnr)] = group
        return out
    return {int(k): v for k, v in (await cached("mig:groups", 86400, produce)).items()}


def _vec(sums: dict[str, int]) -> tuple[float | None, float]:
    """Vægtet middelretning (grader, mod) og konsistens R (0 = spredt, 1 = samme retning)."""
    x = sum(n * math.sin(math.radians(DIRS[d])) for d, n in sums.items())
    y = sum(n * math.cos(math.radians(DIRS[d])) for d, n in sums.items())
    total = sum(sums.values())
    if not total or (abs(x) < 1e-9 and abs(y) < 1e-9):
        return None, 0.0
    return round((math.degrees(math.atan2(x, y)) + 360) % 360), round(math.hypot(x, y) / total, 2)


async def migration_day(session: AsyncSession, day: date) -> dict:
    groups = await species_groups(session)
    rows = (await session.execute(text("""
        SELECT o.artnr, s.list_artnr, ls.navn, split_part(o.adfkode, ' ', 1) AS d, o.antal, o.loknr, l.navn AS lok,
               coalesce(o.obs_lat, l.lat) AS lat, coalesce(o.obs_lon, l.lon) AS lon
        FROM observations o JOIN species s ON s.artnr = o.artnr JOIN species ls ON ls.artnr = s.list_artnr
        JOIN locations l ON l.loknr = o.loknr
        WHERE o.dato = :d AND o.hemmelig = 0 AND o.antal > 0 AND l.lat IS NOT NULL
          AND split_part(o.adfkode, ' ', 1) = ANY(:dirs)
    """), {"d": day, "dirs": list(DIRS)})).all()
    sites: dict[tuple[int, str], dict] = {}
    nation: dict[str, dict] = defaultdict(lambda: {"dirs": defaultdict(int), "species": defaultdict(int)})
    for r in rows:
        g = groups.get(r.list_artnr) or groups.get(r.artnr)
        if not g:
            continue
        site = sites.setdefault((r.loknr, g), {"loknr": r.loknr, "lok": r.lok, "lat": r.lat, "lon": r.lon, "group": g,
                                                "dirs": defaultdict(int), "species": defaultdict(int)})
        site["dirs"][r.d] += r.antal
        site["species"][r.navn] += r.antal
        nation[g]["dirs"][r.d] += r.antal
        nation[g]["species"][r.navn] += r.antal
    out_sites = []
    for s in sites.values():
        direction, consistency = _vec(s["dirs"])
        if direction is None:
            continue
        top = sorted(s["species"].items(), key=lambda kv: -kv[1])[:4]
        out_sites.append({**{k: s[k] for k in ("loknr", "lok", "lat", "lon", "group")}, "n": sum(s["dirs"].values()),
                          "dir": direction, "r": consistency, "species": top})
    summary = []
    for g, label in GROUPS.items():
        nat = nation.get(g)
        if not nat:
            summary.append({"group": g, "label": label, "n": 0, "dir": None, "r": 0, "dirs": {}, "species": []})
            continue
        direction, consistency = _vec(nat["dirs"])
        summary.append({"group": g, "label": label, "n": sum(nat["dirs"].values()), "dir": direction, "r": consistency,
                        "dirs": dict(nat["dirs"]), "species": sorted(nat["species"].items(), key=lambda kv: -kv[1])[:6]})
    return {"date": day.isoformat(), "groups": summary, "sites": sorted(out_sites, key=lambda s: -s["n"])}


def _noon_utc(day: date) -> datetime:
    return datetime.combine(day, time(12), TZ).astimezone(timezone.utc)


async def wind_noon(day: date) -> dict:
    """Stationsvind kl. 12 dansk tid: [{lat, lon, speed (m/s), dir (fra, grader)}]."""
    now = dk_now()
    when = _noon_utc(day)
    live = day == dk_today() and now.hour < 12

    async def produce():
        base = f"{get_settings().dmi_base}/v2/metObs/collections/observation/items"
        stations: dict[str, dict] = {}
        period = {"period": "latest-hour"} if live else {"datetime": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
        async with httpx.AsyncClient(timeout=30, headers=UA) as client:
            for param, key in (("wind_speed", "speed"), ("wind_dir", "dir")):
                data = await _get(client, base, {"parameterId": param, "bbox": BBOX, "limit": "2000", **period})
                for f in (data or {}).get("features", []):
                    p = f["properties"]
                    lon, lat = f["geometry"]["coordinates"][:2]
                    st = stations.setdefault(p["stationId"], {"lat": lat, "lon": lon})
                    if p.get("observed", "") >= st.get(f"_{key}", ""):
                        st[key], st[f"_{key}"] = p.get("value"), p.get("observed", "")
        pts = [{"lat": s["lat"], "lon": s["lon"], "speed": s["speed"], "dir": s["dir"]}
               for s in stations.values() if s.get("speed") is not None and s.get("dir") is not None]
        return {"time": "seneste time" if live else "kl. 12", "stations": pts}

    key = f"mig:wind:{day.isoformat()}:{'live' if live else 'noon'}"
    hit = await get_json(key)
    if hit is not None:
        return hit
    data = await produce()
    # Tom liste (DMI nede) caches kort; afsluttede dage ændrer sig ikke
    await set_json(key, data, 7 * 86400 if data["stations"] and day < dk_today() else 600)
    return data
