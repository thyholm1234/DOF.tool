"""FiNN (Fugle i Ny Natur): områderne og punkterne til FiNN-tællinger.

De ligger i portalens tabeller finn_sites/finn_points (models/finn.py), hvor de rettes og synkroniseres med DOFbasen i
Admin → Tællinger (services/finn_admin.py). Indtil den første synkronisering er anvendt, hentes de direkte fra DOFbasen:

api.dofbasen.dk/api/v1.0/public/gis/finn/sites (~170 områder, WKT-polygoner) og /public/finn/points (~250 punkter) er
offentlige og små. De hentes højst én gang i døgnet (TTL) og gemmes også som «sidst kendte», så tællinger kan laves,
selv om DOFbasen ikke svarer. Testområder (isTestSite) udelades.

Svaret til appen: {"sites": [{"id", "title", "loknr", "ring": [[lat, lon], …]}], "points": [{"id", "site", "lat", "lon",
"source": researcher|volunteer, "type": management|reference}]}. Et punkt uden område knyttes til det område, det ligger i."""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, cached, get_json, set_json, version

log = logging.getLogger("portal.finn")

BASE = "https://api.dofbasen.dk/api/v1.0/public"
KEY, LAST = "finn:data:v1", "finn:data:last"
TTL = 24 * 3600
TIMEOUT = 15
_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)")


def parse_ring(wkt: str | None) -> list[list[float]]:
    """Ydre ring af en WKT-POLYGON/MULTIPOLYGON som [[lat, lon], …] (WKT har lon før lat). Kun den første ring."""
    if not wkt:
        return []
    first = wkt.split("),")[0]
    return [[round(float(lat), 6), round(float(lon), 6)] for lon, lat in _PAIR.findall(first)]


def inside(pt: tuple[float, float], ring: list[list[float]]) -> bool:
    """Ligger punktet (lat, lon) i polygonen? (Strålemetoden – fint på disse afstande.)"""
    y, x = pt
    hit = False
    for i in range(len(ring)):
        y1, x1 = ring[i]
        y2, x2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def build(raw_sites: list[dict], raw_points: list[dict]) -> dict:
    sites = [{"id": s["id"], "title": (s.get("title") or s.get("locationName") or f"FiNN-område {s['id']}").strip(),
              "loknr": s.get("lokId"), "ring": parse_ring(s.get("border"))}
             for s in raw_sites if not s.get("isTestSite") and s.get("border")]
    sites = [s for s in sites if len(s["ring"]) >= 3]
    points = []
    for p in raw_points:
        if p.get("lat") is None or p.get("lon") is None:
            continue
        site = p.get("finnSiteId") or next((s["id"] for s in sites if inside((p["lat"], p["lon"]), s["ring"])), None)
        points.append({"id": p["id"], "site": site, "lat": round(p["lat"], 6), "lon": round(p["lon"], 6),
                       "source": p.get("source"), "type": p.get("type")})
    return {"sites": sorted(sites, key=lambda s: s["title"]), "points": points}


async def fetch() -> dict:
    """Hent og byg DOFbasens aktuelle områder og punkter (fejl kastes videre)."""
    return await _fetch()


async def _fetch() -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"Accept": "application/json", "User-Agent": "DOF.tool"}) as c:
        sites = (await c.get(f"{BASE}/gis/finn/sites")).raise_for_status().json()
        points = (await c.get(f"{BASE}/finn/points")).raise_for_status().json()
    return build(sites, points)


async def data(db: AsyncSession | None = None) -> dict:
    """FiNN-områder og -punkter til appen: portalens egne (aktive) fra databasen, når de findes; ellers DOFbasens."""
    if db is not None:
        local = await cached(f"finn:local:{await version('finn')}", 86400, lambda: _local(db))
        if local["sites"]:
            return local
    return await _remote()


async def _local(db: AsyncSession) -> dict:
    from portal.models import FinnPoint, FinnSite

    sites = (await db.execute(select(FinnSite).where(FinnSite.aktiv, FinnSite.removed_at.is_(None))
                              .order_by(FinnSite.title))).scalars().all()
    points = (await db.execute(select(FinnPoint).where(FinnPoint.aktiv, FinnPoint.removed_at.is_(None))
                               .order_by(FinnPoint.id))).scalars().all()
    return {"sites": [{"id": s.id, "title": s.title, "loknr": s.loknr, "ring": s.ring} for s in sites if len(s.ring or []) >= 3],
            "points": [{"id": p.id, "site": p.site_id, "lat": p.lat, "lon": p.lon, "source": p.source, "type": p.type} for p in points]}


async def changed() -> None:
    """Portalens FiNN-data er rettet: appen får den nye version ved næste hentning."""
    await bump("finn")


async def _remote() -> dict:
    """DOFbasens områder og punkter (cachet et døgn; ved fejl den sidst kendte kopi, ellers tomt)."""
    hit = await get_json(KEY)
    if hit is not None:
        return hit
    try:
        fresh = await _fetch()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("FiNN-data kunne ikke hentes fra DOFbasen: %s", exc)
        last = await get_json(LAST)
        if last is not None:
            await set_json(KEY, last, 3600)  # prøv igen om en time
            return last
        return {"sites": [], "points": []}
    await set_json(KEY, fresh, TTL)
    await set_json(LAST, fresh, 30 * 86400)
    return fresh


def site_at(d: dict, lat: float, lon: float) -> dict | None:
    return next((s for s in d["sites"] if inside((lat, lon), s["ring"])), None)
