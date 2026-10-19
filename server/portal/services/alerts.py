"""Notifikationer: matcher nye/ændrede observationer mod brugernes alarmregler og fulgte tråde.

Regler (som i DOF.not, men generaliseret til vilkårlige områder):
- En tråd (art + lokalitet + dag) udløser regel-notifikationer, når den er ny, eller når det
  maksimale antal individer stiger.
- Nye observationer i en eksisterende tråd sendes kun til brugere, der følger tråden.
- Man får aldrig besked om egne observationer eller tråde, man selv har observeret i.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import kv, publish
from portal.services.push import send_to_user
from portal.timeutil import dk_today, in_quiet_hours

log = logging.getLogger("portal.alerts")

LEVEL_KATS = {"SU": {"SU"}, "SUB": {"SU", "SUB"}, "bemaerk": {"SU", "SUB", "bemaerk"}}
KAT_LABEL = {"SU": "SU", "SUB": "SUB", "bemaerk": "Bemærk", "alm": ""}


@dataclass
class Event:
    obsid: int
    dato: object
    artnr: int
    loknr: int | None
    antal: int
    kategori: str
    art: str
    art_type: str
    lok: str | None
    kommune_kode: str | None
    afdelinger: list[int]
    obserkode: str | None
    observer: str
    adf: str | None
    tid: str | None
    source: str = "dofbasen"  # dofbasen / portal (feltmelding indtastet i portalen)
    sikkerhed: str = "sikker"

    @property
    def thread(self) -> tuple:
        return (self.dato, self.artnr, self.loknr)


async def process(session: AsyncSession, new_obsids: list[int], changed: list[tuple[int, int, int]],
                  extra: list[Event] | None = None) -> int:
    """extra: feltmeldinger fra portalen (negative obsid'er), som behandles som nye observationer."""
    ids = list({*new_obsids, *(c[0] for c in changed)})
    if not ids and not extra:
        return 0
    today = dk_today()
    events = (await _load_events(session, ids) if ids else []) + list(extra or [])
    events = [e for e in events if e.dato == today and e.loknr is not None]
    if not events:
        return 0
    new_ids = {*new_obsids, *(e.obsid for e in extra or [])}

    threads = {e.thread for e in events}
    thread_max, thread_observers = await _thread_state(session, threads)
    for e in extra or []:
        thread_max[e.thread] = max(thread_max.get(e.thread, 0), e.antal)
        if e.obserkode:
            thread_observers.setdefault(e.thread, set()).add(e.obserkode)

    # Tråde hvor max-antal er steget siden sidste notifikation
    r = kv()
    hkey = f"alert:threadmax:{today}"
    changed_threads: dict[tuple, int] = {}
    for t in threads:
        field = f"{t[1]}:{t[2]}"
        prev = await r.hget(hkey, field)
        cur = thread_max.get(t, 0)
        if prev is None or cur > int(prev):
            changed_threads[t] = cur
            await r.hset(hkey, field, cur)
    await r.expire(hkey, 3 * 86400)

    users = await _users(session)
    rules = await _rules(session)
    area_members = await _area_members(session, rules, {e.loknr for e in events})
    counts = await _local_counts(session, rules, events)

    outbox: dict[tuple[int, str], dict] = {}
    # 1) Regler – én repræsentativ (seneste) observation pr. ændret tråd
    rep: dict[tuple, Event] = {}
    for e in events:
        if e.thread in changed_threads:
            cur = rep.get(e.thread)
            if cur is None or (e.kategori != "alm" and cur.kategori == "alm") or (e.tid or "") > (cur.tid or ""):
                rep[e.thread] = e
    for rule in rules:
        user = users.get(rule["user_id"])
        if not user:
            continue
        for t, e in rep.items():
            if not _rule_matches(rule, e, area_members, counts):
                continue
            if user["obserkode"] and user["obserkode"] in thread_observers.get(t, set()):
                continue
            dedup = f"t:{t[0]}:{t[1]}:{t[2]}:{changed_threads[t]}"
            key = (rule["user_id"], dedup)
            if key not in outbox:
                outbox[key] = _message(e, thread_max.get(t, e.antal), rule)

    # 2) Fulgte tråde – alle nye observationer
    follows = await _follows(session, threads)
    for e in events:
        if e.obsid not in new_ids:
            continue
        for user_id in follows.get(e.thread, ()):
            user = users.get(user_id)
            if not user or (user["obserkode"] and user["obserkode"] == e.obserkode):
                continue
            key = (user_id, f"o:{e.obsid}")
            if not any(k[0] == user_id and k[1].startswith(f"t:{e.dato}:{e.artnr}:{e.loknr}:") for k in outbox):
                outbox[key] = _message(e, thread_max.get(e.thread, e.antal), None, followed=True)

    sent = await deliver(session, users, outbox)
    if outbox:
        log.info("Notifikationer: %s kandidater, %s push", len(outbox), sent)
    return sent


async def deliver(session: AsyncSession, users: dict[int, dict] | None, outbox: dict[tuple[int, str], dict]) -> int:
    """Gem notifikationer (dedupliceret pr. bruger), send dem live via SSE og som push."""
    users = users if users is not None else await _users(session)
    sent = 0
    for (user_id, dedup), msg in outbox.items():
        if user_id not in users:
            continue
        inserted = (await session.execute(text("""
            INSERT INTO notifications (user_id, dedup_key, rule_id, title, body, url, kategori)
            VALUES (:u, :d, :r, :t, :b, :url, :k) ON CONFLICT (user_id, dedup_key) DO NOTHING RETURNING id
        """), {"u": user_id, "d": dedup, "r": msg["rule_id"], "t": msg["title"], "b": msg["body"],
               "url": msg["url"], "k": msg["kategori"]})).scalar()
        if not inserted:
            continue
        await session.commit()
        await publish(f"user:{user_id}", {"type": "notification", "id": inserted, **msg})
        user = users[user_id]
        if msg["push"] and user["push_enabled"] and not in_quiet_hours(user["quiet"]):
            delivered = await send_to_user(session, user_id, {
                "title": msg["title"], "body": msg["body"], "url": msg["url"], "tag": msg["tag"],
            })
            if delivered:
                await session.execute(text("UPDATE notifications SET pushed = true WHERE id = :id"), {"id": inserted})
                sent += 1
        await session.commit()
    return sent


async def seed_threads(session: AsyncSession, day) -> None:
    """Gem dagens trådmaksima uden at sende noget (efter opstart midt på dagen)."""
    rows = (await session.execute(text("""
        SELECT artnr, loknr, max(n) FROM (SELECT artnr, loknr, turid, sum(antal) AS n FROM observations
        WHERE dato = :d AND loknr IS NOT NULL GROUP BY artnr, loknr, turid) x GROUP BY artnr, loknr
    """), {"d": day})).all()
    if rows:
        key = f"alert:threadmax:{day}"
        await kv().hset(key, mapping={f"{a}:{l}": int(m or 0) for a, l, m in rows})
        await kv().expire(key, 3 * 86400)


def _rule_matches(rule: dict, e: Event, members: dict[int, set[int]], counts: dict[tuple[str, int], int]) -> bool:
    if rule["species_include"] and e.artnr not in rule["species_include"]:
        return False
    if e.artnr in (rule["species_exclude"] or ()):
        return False
    if e.antal < (rule["min_antal"] or 1):
        return False
    if rule["kommuner"] or rule["afdelinger"]:
        if e.kommune_kode not in rule["kommuner"] and not set(e.afdelinger) & set(rule["afdelinger"]):
            return False
    kind, ref = rule["area_kind"], rule["area_ref"]
    if kind == "afdeling" and int(ref) not in e.afdelinger:
        return False
    if kind == "kommune" and e.kommune_kode != ref:
        return False
    if kind == "lokalitet" and str(e.loknr) != str(ref):
        return False
    if kind in ("polygon", "radius") and e.loknr not in members.get(rule["area_id"], set()):
        return False
    level = rule["level"]
    if level in LEVEL_KATS:
        return e.kategori in LEVEL_KATS[level]
    if level == "local":
        if e.kategori in ("SU", "SUB"):
            return True
        if e.art_type not in ("art", "underart"):
            return False
        if rule["kommuner"] or rule["afdelinger"]:
            # Flere regioner: lokalt hit hvis arten er sjælden i mindst én af de valgte regioner fundet ligger i
            keys = [f"kom:{e.kommune_kode}"] if e.kommune_kode in rule["kommuner"] else []
            keys += [f"afd:{a}" for a in e.afdelinger if a in rule["afdelinger"]]
            return min(counts.get((k, e.artnr), 0) for k in keys) < rule["local_max"]
        return counts.get((rule["count_key"], e.artnr), 0) < rule["local_max"]
    return level == "all"


def _message(e: Event, max_antal: int, rule: dict | None, followed: bool = False) -> dict:
    kat = KAT_LABEL.get(e.kategori, "")
    if rule and rule["level"] == "local" and e.kategori in ("alm", "bemaerk"):
        kat = "Lokalt hit"
    title = f"{max_antal or e.antal} {e.art}, {e.lok or 'ukendt lokalitet'}"
    certainty = e.sikkerhed if e.sikkerhed != "sikker" else None
    bits = [b for b in (kat, "Portal" if e.source == "portal" else None, certainty, e.adf, e.observer, e.tid) if b]
    return {
        "title": title[:240],
        "body": (" · ".join(bits) + (" (fulgt tråd)" if followed else ""))[:480],
        "url": f"/#/traad/{e.dato}/{e.artnr}/{e.loknr}",
        "tag": f"{e.dato}-{e.artnr}-{e.loknr}",
        "kategori": e.kategori,
        "rule_id": rule["id"] if rule else None,
        "push": rule["push"] if rule else True,
    }


async def _load_events(session: AsyncSession, ids: list[int]) -> list[Event]:
    rows = (await session.execute(text("""
        SELECT o.obsid, o.dato, o.artnr, o.loknr, o.antal, o.kategori, s.navn AS art, s.art_type, l.navn AS lok,
               l.kommune_kode, o.afdelinger, o.obserkode,
               trim(coalesce(ob.fornavn,'') || ' ' || coalesce(ob.efternavn,'')) AS observer,
               ct.text AS adf, coalesce(o.obstidfra, t.turtidfra) AS tid
        FROM observations o JOIN species s ON s.artnr = o.artnr
        LEFT JOIN locations l ON l.loknr = o.loknr
        LEFT JOIN observers ob ON ob.obserkode = o.obserkode
        LEFT JOIN trips t ON t.turid = o.turid
        LEFT JOIN code_texts ct ON ct.kind = 'adf' AND ct.code = o.adfkode
        WHERE o.obsid = ANY(:ids) AND o.hemmelig = 0 AND s.excluded = false
    """), {"ids": ids})).mappings().all()
    return [Event(**{**dict(r), "afdelinger": list(r["afdelinger"] or [])}) for r in rows]


async def _thread_state(session: AsyncSession, threads: set[tuple]) -> tuple[dict, dict]:
    if not threads:
        return {}, {}
    day = next(iter(threads))[0]
    arts = list({t[1] for t in threads})
    loks = list({t[2] for t in threads})
    rows = (await session.execute(text("""
        SELECT artnr, loknr, max(n) AS mx, array_agg(DISTINCT obserkode) AS obs FROM (
            SELECT artnr, loknr, turid, obserkode, sum(antal) AS n FROM observations
            WHERE dato = :d AND artnr = ANY(:a) AND loknr = ANY(:l) GROUP BY artnr, loknr, turid, obserkode) x
        GROUP BY artnr, loknr
    """), {"d": day, "a": arts, "l": loks})).all()
    mx, observers = {}, {}
    for artnr, loknr, m, obs in rows:
        mx[(day, artnr, loknr)] = int(m or 0)
        observers[(day, artnr, loknr)] = {o for o in (obs or []) if o}
    return mx, observers


async def _users(session: AsyncSession) -> dict[int, dict]:
    rows = (await session.execute(text("SELECT id, obserkode, prefs FROM users WHERE NOT blocked"))).all()
    return {r[0]: {"obserkode": r[1], "push_enabled": (r[2] or {}).get("push_enabled", True),
                   "quiet": (r[2] or {}).get("quiet_hours")} for r in rows}


async def _rules(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT r.id, r.user_id, r.level, r.local_max, r.min_antal, r.species_include, r.species_exclude, r.push,
               r.area_id, a.kind AS area_kind, a.ref AS area_ref, r.kommuner, r.afdelinger
        FROM alert_rules r LEFT JOIN areas a ON a.id = r.area_id WHERE r.enabled
    """))).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["kommuner"], d["afdelinger"] = list(d["kommuner"] or []), list(d["afdelinger"] or [])
        kind = d["area_kind"]
        d["count_key"] = ("dk" if kind is None else f"afd:{d['area_ref']}" if kind == "afdeling"
                          else f"kom:{d['area_ref']}" if kind == "kommune" else f"lok:{d['area_ref']}" if kind == "lokalitet"
                          else f"area:{d['area_id']}")
        out.append(d)
    return out


async def _area_members(session: AsyncSession, rules: list[dict], loknrs: set) -> dict[int, set[int]]:
    area_ids = list({r["area_id"] for r in rules if r["area_kind"] in ("polygon", "radius")})
    if not area_ids or not loknrs:
        return {}
    rows = (await session.execute(text("""
        SELECT a.id, l.loknr FROM areas a JOIN locations l ON ST_Intersects(a.geom, l.geom)
        WHERE a.id = ANY(:a) AND l.loknr = ANY(:l)
    """), {"a": area_ids, "l": [x for x in loknrs if x is not None]})).all()
    out: dict[int, set[int]] = defaultdict(set)
    for area_id, loknr in rows:
        out[area_id].add(loknr)
    return out


async def _local_counts(session: AsyncSession, rules: list[dict], events: list[Event]) -> dict[tuple[str, int], int]:
    keys = set()
    for r in rules:
        if r["level"] == "local":
            keys |= {r["count_key"], *(f"kom:{k}" for k in r["kommuner"]), *(f"afd:{a}" for a in r["afdelinger"])}
    keys = list(keys)
    if not keys:
        return {}
    rows = (await session.execute(text("""
        SELECT area_key, artnr, n_obs FROM area_species_counts WHERE area_key = ANY(:k) AND artnr = ANY(:a)
    """), {"k": keys, "a": list({e.artnr for e in events})})).all()
    return {(k, a): n for k, a, n in rows}


async def _follows(session: AsyncSession, threads: set[tuple]) -> dict[tuple, list[int]]:
    if not threads:
        return {}
    day = next(iter(threads))[0]
    rows = (await session.execute(text("""
        SELECT artnr, loknr, user_id FROM thread_follows WHERE dato = :d AND NOT muted
    """), {"d": day})).all()
    out: dict[tuple, list[int]] = defaultdict(list)
    for artnr, loknr, user_id in rows:
        if (day, artnr, loknr) in threads:
            out[(day, artnr, loknr)].append(user_id)
    return out


async def follower_ids(session: AsyncSession, thread: tuple) -> list[int]:
    rows = (await session.execute(text("""
        SELECT user_id FROM thread_follows WHERE dato = :d AND artnr = :a AND loknr = :l AND NOT muted
    """), {"d": thread[0], "a": thread[1], "l": thread[2]})).all()
    return [r[0] for r in rows]
