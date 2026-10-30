"""Dagskort (Kort → Dag): en dags tråde for hele Danmark med SU, SUB, Bemærk, lokale hits og fænologi på ét kort.

Én forespørgsel giver alle punkter; hvert punkt har flag for de filtre, det hører til (kategori, "local", "pheno"), så
kortet kan slå lagene til og fra uden at hente igen. Tre kald til feed._threads (bemaerk, local, pheno) slås sammen
pr. tråd, og portalens feltmeldinger flettes ind som i feedet (kun offentlige – kortet er det samme for alle).

Cache: i dag følger feedets version (ny sync = nyt kort). De seneste DAYS dage caches et døgn uafhængigt af versionen og
varmes op af workeren hver nat, så det er hurtigt at bladre i ugen; ældre dage beregnes ved behov og caches kort.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, set_json, version
from portal.domain import kategori
from portal.services import feed, reports
from portal.services.areas import Scope
from portal.timeutil import dk_today

DAYS = 7
LIMIT = 5000
LOCAL_MAX = 10
_DK = Scope("dk")


def _key(day: date, local_max: int) -> str:
    return f"daymap:{day.isoformat()}:{local_max}"


async def _produce(session: AsyncSession, day: date, local_max: int) -> dict:
    points: dict[str, dict] = {}
    for level in ("bemaerk", "local", "pheno"):
        rows = await feed._threads(session, day, _DK, level, local_max, "lokalitet", LIMIT)
        rows = await reports.merge_feed(session, [dict(r) for r in rows], day, _DK, level, local_max, None)
        for t in rows:
            if t.get("lat") is None:
                continue
            p = points.get(t["id"])
            if p is None:
                p = points[t["id"]] = {
                    "artnr": t["artnr"], "loknr": t["loknr"], "art": t["art"], "lok": t["lok"], "kommune": t.get("kommune"),
                    "lat": round(t["lat"], 5), "lon": round(t["lon"], 5), "kategori": t["kategori"],
                    "antal": t["max_antal"],
                    "last_seen": t["last_seen"], "local": False, "pheno": None}
            if level == "local":
                p["local"] = True
            if level == "pheno" and t.get("pheno"):
                p["pheno"] = t["pheno"]
            p["kategori"] = kategori.higher(p["kategori"], t["kategori"])
            p["antal"] = max(p["antal"], t["max_antal"])
    out = sorted(points.values(), key=lambda p: p["last_seen"], reverse=True)
    counts = {k: sum(p["kategori"] == k for p in out) for k in ("SU", "SUB", "bemaerk")}
    counts |= {"local": sum(p["local"] for p in out), "pheno": sum(p["pheno"] is not None for p in out)}
    return {"date": day.isoformat(), "local_max": local_max, "points": out, "counts": counts}


async def day_map(session: AsyncSession, day: date, local_max: int = LOCAL_MAX) -> dict:
    today = dk_today()
    if day >= today:
        key, ttl = f"{_key(day, local_max)}:{await version('feed')}", 120
    elif day >= today - timedelta(days=DAYS):
        key, ttl = _key(day, local_max), 86400
    else:
        key, ttl = _key(day, local_max), 1800
    return await cached(key, ttl, lambda: _produce(session, day, local_max))


async def warm(session: AsyncSession) -> int:
    """Beregn ugens dagskort igen (standardgrænsen for lokale hits), så de også rummer sene indtastninger."""
    today = dk_today()
    for n in range(1, DAYS + 1):
        day = today - timedelta(days=n)
        await set_json(_key(day, LOCAL_MAX), await _produce(session, day, LOCAL_MAX), 86400)
    return DAYS
