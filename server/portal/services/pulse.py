"""Puls: data til kommandokortet – hvad der er kommet til ved hver live-sync, og hvor der står aktive birdere.

Ved hver live-hentning gemmes ændringen siden sidste sync pr. art og lokalitet: nye observationer tæller med
deres antal, ændrede med forskellen (50 → 56 bogfinker giver 6). Birdere er anonyme og vises på lokaliteten
for deres seneste ændring i op til 15 minutter – eller til turen er afsluttet (sluttid i DOFbasen). Hemmelige observationer er udeladt, og der bruges altid
lokalitetens position, aldrig fuglens præcise.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv
from portal.timeutil import TZ

BATCHES = "pulse:batches"  # JSON pr. sync, nyeste først
BIRDERS = "pulse:birders"  # anonymt id -> {lat, lon, lok, turid, at}
KEEP = 40
ACTIVE = timedelta(minutes=15)
RANK = {"SU": 3, "SUB": 2, "bemaerk": 1, "alm": 0}


async def _anon(obserkode: str) -> str:
    """Tilfældigt id pr. obserkode (gælder et døgn), så kortet ikke afslører hvem der er hvem."""
    key = f"pulse:anon:{obserkode}"
    fresh = secrets.token_hex(6)
    if await kv().set(key, fresh, nx=True, ex=86400):
        return fresh
    return await kv().get(key) or fresh


async def _store(items: dict, birders: dict, source: str) -> None:
    now = datetime.now(timezone.utc)
    if items:
        rows = sorted(items.values(), key=lambda i: (-RANK.get(i["kategori"], 0), -i["delta"]))
        batch = {"id": secrets.token_hex(4), "at": now.isoformat(), "source": source, "items": rows}
        await kv().lpush(BATCHES, json.dumps(batch))
        await kv().ltrim(BATCHES, 0, KEEP - 1)
    if birders:
        await kv().hset(BIRDERS, mapping={await _anon(k): json.dumps({**v, "at": now.isoformat()}) for k, v in birders.items()})


async def record_sync(session: AsyncSession, new_obsids: list[int], changed: list[tuple[int, int, int]]) -> None:
    """Gem ændringen fra én live-hentning."""
    delta = {o: None for o in new_obsids}
    delta.update({o: new - old for o, old, new in changed if new > old})
    if not delta:
        return
    rows = (await session.execute(text("""
        SELECT o.obsid, o.turid, o.artnr, o.antal, o.kategori, o.loknr, o.obserkode, s.navn AS art, l.navn AS lok, l.lat, l.lon
        FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
        WHERE o.obsid = ANY(:ids) AND o.hemmelig = 0 AND l.lat IS NOT NULL
    """), {"ids": list(delta)})).mappings().all()
    items: dict[tuple, dict] = {}
    birders: dict[str, dict] = {}
    for r in rows:
        d = delta[r["obsid"]]
        add = r["antal"] if d is None else d
        it = items.setdefault((r["loknr"], r["artnr"]), {
            "artnr": r["artnr"], "art": r["art"], "loknr": r["loknr"], "lok": r["lok"], "lat": r["lat"], "lon": r["lon"],
            "kategori": r["kategori"], "delta": 0, "new": 0})
        it["delta"] += max(add or 0, 0)
        it["new"] += d is None
        if RANK.get(r["kategori"], 0) > RANK.get(it["kategori"], 0):
            it["kategori"] = r["kategori"]
        if r["obserkode"]:
            birders[r["obserkode"]] = {"lat": r["lat"], "lon": r["lon"], "lok": r["lok"], "turid": r["turid"]}
    await _store(items, birders, "dof")


async def record_report(session: AsyncSession, report) -> None:
    """En feltmelding indtastet i portalen kommer med på kortet med det samme."""
    if report.hemmelig:
        return
    r = (await session.execute(text("""
        SELECT s.navn AS art, l.navn AS lok, l.lat, l.lon FROM species s, locations l WHERE s.artnr = :a AND l.loknr = :l
    """), {"a": report.artnr, "l": report.loknr})).mappings().first()
    if not r or r["lat"] is None:
        return
    item = {"artnr": report.artnr, "art": r["art"], "loknr": report.loknr, "lok": r["lok"], "lat": r["lat"], "lon": r["lon"],
            "kategori": report.kategori, "delta": report.antal or 0, "new": 1}
    await _store({0: item}, {report.obserkode: {"lat": r["lat"], "lon": r["lon"], "lok": r["lok"]}}, "portal")


async def _ended(session: AsyncSession, turids: list[int]) -> set[int]:
    """Ture der er afsluttet: sluttid angivet og passeret (eller turen er fra en tidligere dag)."""
    if not turids:
        return set()
    now = datetime.now(TZ)
    rows = (await session.execute(text("""
        SELECT turid FROM trips WHERE turid = ANY(:t) AND turtidtil IS NOT NULL
          AND (dato < :d OR replace(turtidtil, '.', ':') <= :hm)
    """), {"t": turids, "d": now.date(), "hm": now.strftime("%H:%M")})).scalars().all()
    return set(rows)


async def snapshot(session: AsyncSession, since: str | None) -> dict:
    """Syncs efter `since` (ældste først; uden `since` de seneste ti) og birdere aktive de seneste 15 minutter,
    hvis tur ikke er afsluttet."""
    raw = [json.loads(b) for b in await kv().lrange(BATCHES, 0, KEEP - 1)]
    batches = [b for b in raw if since and b["at"] > since] if since else raw[:10]
    now = datetime.now(timezone.utc)
    birders, stale = [], []
    for k, v in (await kv().hgetall(BIRDERS)).items():
        b = json.loads(v)
        if now - datetime.fromisoformat(b["at"]) > ACTIVE:
            stale.append(k)
        else:
            birders.append({"id": k, **b})
    ended = await _ended(session, [b["turid"] for b in birders if b.get("turid")])
    stale += [b["id"] for b in birders if b.get("turid") in ended]
    birders = [{k: v for k, v in b.items() if k != "turid"} for b in birders if b.get("turid") not in ended]
    if stale:
        await kv().hdel(BIRDERS, *stale)
    # Forventet tid til næste sync: medianen af de seneste intervaller mellem DOFbasen-hentninger
    times = sorted(datetime.fromisoformat(b["at"]) for b in raw if b["source"] == "dof")[-8:]
    gaps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]) if (b - a).total_seconds() < 900)
    interval = gaps[len(gaps) // 2] if gaps else 120
    return {"batches": list(reversed(batches)), "birders": birders, "interval": max(45, min(interval, 600)),
            "now": now.isoformat()}
