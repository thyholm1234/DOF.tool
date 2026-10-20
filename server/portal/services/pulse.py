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
GONE = "pulse:gone"  # slettede/nedjusterede punkter: {key, delta, total} (delta None = punktet er væk)
BIRDERS = "pulse:birders"  # anonymt id -> {lat, lon, loknr, lok, turid, kategori, at}
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
        ids = {k: await _anon(k) for k in birders}
        prev = dict(zip(ids.values(), await kv().hmget(BIRDERS, list(ids.values()))))
        mapping = {}
        for k, v in birders.items():
            # Figuren beholder sin mest bemærkelsesværdige kategori, så længe den står på kortet
            old = json.loads(prev[ids[k]]) if prev.get(ids[k]) else None
            if old and now - datetime.fromisoformat(old["at"]) <= ACTIVE and RANK.get(old.get("kategori"), 0) > RANK.get(v["kategori"], 0):
                v = {**v, "kategori": old["kategori"]}
            mapping[ids[k]] = json.dumps({**v, "at": now.isoformat()})
        await kv().hset(BIRDERS, mapping=mapping)


async def record_sync(session: AsyncSession, new_obsids: list[int], changed: list[tuple[int, int, int]]) -> None:
    """Gem ændringen fra én live-hentning."""
    delta = {o: None for o in new_obsids}
    delta.update({o: new - old for o, old, new in changed if new > old})
    if not delta:
        return
    rows = (await session.execute(text("""
        SELECT o.obsid, o.turid, o.dato, o.artnr, o.antal, o.kategori, o.loknr, o.obserkode, s.navn AS art, l.navn AS lok, l.lat, l.lon
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
            "kategori": r["kategori"], "delta": 0, "new": 0, "obs": {}})
        it["obs"][str(r["obsid"])] = [max(add or 0, 0), r["antal"] or 0]
        it["delta"] += max(add or 0, 0)
        it["new"] += d is None
        if RANK.get(r["kategori"], 0) > RANK.get(it["kategori"], 0):
            it["kategori"] = r["kategori"]
        if r["obserkode"]:
            kat = birders.get(r["obserkode"], {}).get("kategori", "alm")
            if RANK.get(r["kategori"], 0) > RANK.get(kat, 0):
                kat = r["kategori"]
            birders[r["obserkode"]] = {"lat": r["lat"], "lon": r["lon"], "loknr": r["loknr"], "lok": r["lok"], "turid": r["turid"],
                                       "kategori": kat}
    if items:
        await _totals(session, items, rows[0]["dato"])
    await _store(items, birders, "dof")


async def _totals(session: AsyncSession, items: dict, day) -> None:
    """Dagens samlede antal af arten på lokaliteten (DOFbasen + portalens feltmeldinger, uden hemmelige)."""
    keys = list(items)
    rows = (await session.execute(text("""
        SELECT k.loknr, k.artnr,
               coalesce((SELECT sum(antal) FROM observations o WHERE o.dato = :d AND o.loknr = k.loknr AND o.artnr = k.artnr
                         AND o.hemmelig = 0), 0)
             + coalesce((SELECT sum(antal) FROM field_reports r WHERE r.dato = :d AND r.loknr = k.loknr AND r.artnr = k.artnr
                         AND NOT r.hemmelig), 0) AS total
        FROM unnest(CAST(:l AS int[]), CAST(:a AS int[])) AS k(loknr, artnr)
    """), {"d": day, "l": [k[0] for k in keys], "a": [k[1] for k in keys]})).all()
    for loknr, artnr, total in rows:
        items[(loknr, artnr)]["total"] = int(total)


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
            "kategori": report.kategori, "delta": report.antal or 0, "new": 1,
            "obs": {str(-report.id): [report.antal or 0, report.antal or 0]}}
    items = {(report.loknr, report.artnr): item}
    await _totals(session, items, report.dato)
    await _store(items, {report.obserkode: {"lat": r["lat"], "lon": r["lon"], "loknr": report.loknr, "lok": r["lok"],
                                                    "kategori": report.kategori}}, "portal")


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


async def record_deleted(obsids: list[int]) -> None:
    """Slettede observationer fjernes fra loggen: deres bidrag trækkes fra, og tomme punkter forsvinder."""
    gone = {str(o) for o in obsids}
    raw = await kv().lrange(BATCHES, 0, KEEP - 1)
    for idx, text_ in enumerate(raw):
        b = json.loads(text_)
        keep, changed = [], False
        for it in b["items"]:
            hit = gone & set(it.get("obs", {}))
            if not hit:
                keep.append(it)
                continue
            changed = True
            for o in hit:
                d, antal = it["obs"].pop(o)
                it["delta"] -= d
                if it.get("total") is not None:
                    it["total"] = max(it["total"] - antal, 0)
            key = f"{b['id']}/{it['loknr']}/{it['artnr']}"
            if it["obs"] and it["delta"] > 0:
                keep.append(it)
                await _tombstone(key, it["delta"], it.get("total"))
            else:
                await _tombstone(key, None, None)
        if changed:
            b["items"] = keep
            await kv().lset(BATCHES, idx, json.dumps(b))


async def _tombstone(key: str, delta: int | None, total: int | None) -> None:
    await kv().lpush(GONE, json.dumps({"key": key, "delta": delta, "total": total}))
    await kv().ltrim(GONE, 0, 499)
    await kv().expire(GONE, 3600)


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
    for b in batches:  # punkterne får en nøgle, så slettelser kan henvise til dem; obsid'er sendes ikke videre
        for it in b["items"]:
            it["key"] = f"{b['id']}/{it['loknr']}/{it['artnr']}"
            it.pop("obs", None)
    gone = [json.loads(g) for g in await kv().lrange(GONE, 0, 499)]
    return {"batches": list(reversed(batches)), "gone": list(reversed(gone)), "birders": birders, "interval": max(45, min(interval, 600)),
            "now": now.isoformat()}
