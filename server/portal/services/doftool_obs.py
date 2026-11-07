"""DOF.tool-gruppens datasæt: ture og observationer indtastet i DOF.tool, i samme form som DOFbasens.

Indtastningerne (field_trips/field_reports i brugergruppen) er sandheden – appen redigerer dem, offline-køen uploader
dem, og de har medier og status. Herfra bygges DOF.tool-gruppens `observations` og `trips` (dbgroups.DOFTOOL), så søgning,
Observationer og listerne kan læse dem på ligefod med DOFbasens data (services/dataset.py). DOFbasens data og de løbende
synkroniseringer derfra bor urørt i observationsgruppen.

* Id'er: obsid = -(1e9 + field_reports.id), turid = -(1e9 + field_trips.id); en melding uden tur får sin egen tur
  -(2e9 + id). De støder hverken sammen med DOFbasens (positive) eller de manuelle fund (små negative).
* Hver række er mærket indtastning = 'doftool'.
* Dubletter: står samme fugl også i DOFbasen (samme observatør, art, lokalitet og dato), tæller DOFbasens – DOF.tool-
  rækken udelades, så fuglen ikke tæller to gange, mens overgangen står på.
* Datasættet bygges helt om ved hver afstemning (det er lille), men skrives kun, når indholdet er ændret. Det kan derfor
  altid genskabes – også på en ny, dedikeret node."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import text

from portal.cache import bump, kv
from portal.db import SessionLocal, ToolSession, router

log = logging.getLogger("portal.doftool_obs")

BASE = 1_000_000_000
INDTASTNING = "doftool"
FINGERPRINT = "doftool:fingerprint"
DIRTY = "doftool:dirty_since"  # tidligste dato, der er ændret siden sidste aggregering (worker._aggregate_since)
VERSION = "doftool"  # cache-version for datasættet (services/dataset.py)
_LOOKUPS = json.loads((Path(__file__).resolve().parent.parent / "seed" / "trip_lookups.json").read_text(encoding="utf-8"))
METODE = {m["id"]: m["navn"] for m in _LOOKUPS["metoder"]}

OBS_COLS = ("obsid", "turid", "dato", "artnr", "antal", "koen", "adfkode", "alderkode", "dragtkode", "obstidfra", "obstidtil",
            "fuglnoter", "metode", "hemmelig", "kvalitet", "loknr", "obserkode", "afdelinger", "obs_lat", "obs_lon", "kategori",
            "indtastning", "first_seen", "updated_at")
TRIP_COLS = ("turid", "dato", "loknr", "obserkode", "turtidfra", "turtidtil", "turnoter", "medobser", "indtastning")


def obsid_of(report_id: int) -> int:
    return -(BASE + report_id)


def turid_of(trip_id: int | None, report_id: int) -> int:
    return -(BASE + trip_id) if trip_id else -(2 * BASE + report_id)


def is_doftool(obsid: int | None) -> bool:
    return obsid is not None and obsid <= -BASE


async def _desired() -> tuple[list[dict], list[dict]]:
    """Datasættet som det skal være: (observationer, ture) – før dubletkontrollen."""
    async with SessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT r.id, r.trip_id, r.dato, r.artnr, r.antal, r.koen, r.adfkode, r.alderkode, r.dragtkode, r.obstidfra,
                   r.obstidtil, r.fuglnoter, r.metode, r.hemmelig, r.skjul_position, r.loknr, r.obserkode, r.afdelinger,
                   r.obs_lat, r.obs_lon, r.kategori, r.created_at, r.updated_at,
                   t.metode_id, t.turtidfra, t.turtidtil, t.turnoter, t.medobser, t.loknr AS t_loknr, t.dato AS t_dato,
                   t.obserkode AS t_obserkode
            FROM field_reports r LEFT JOIN field_trips t ON t.id = r.trip_id
            ORDER BY r.id
        """))).mappings().all()
    obs, trips = [], {}
    for r in rows:
        turid = turid_of(r["trip_id"], r["id"])
        metode = METODE.get(r["metode_id"]) if r["trip_id"] else None
        obs.append({
            "obsid": obsid_of(r["id"]), "turid": turid, "dato": r["dato"], "artnr": r["artnr"], "antal": r["antal"] or 0,
            "koen": r["koen"], "adfkode": (r["adfkode"] or None) and r["adfkode"][:8], "alderkode": r["alderkode"],
            "dragtkode": r["dragtkode"], "obstidfra": r["obstidfra"], "obstidtil": r["obstidtil"], "fuglnoter": r["fuglnoter"],
            "metode": metode or r["metode"] or METODE[1], "hemmelig": 1 if r["hemmelig"] else 0, "kvalitet": None,
            "loknr": r["loknr"], "obserkode": r["obserkode"], "afdelinger": list(r["afdelinger"] or []),
            # Skjult position: heller ikke i datasættet (indtasteren ser den stadig på sin egen tur)
            "obs_lat": None if r["skjul_position"] else r["obs_lat"], "obs_lon": None if r["skjul_position"] else r["obs_lon"],
            "kategori": r["kategori"] or "alm", "indtastning": INDTASTNING,
            "first_seen": r["created_at"], "updated_at": r["updated_at"] or r["created_at"],
        })
        if turid not in trips:
            trips[turid] = {"turid": turid, "dato": r["t_dato"] or r["dato"], "loknr": r["t_loknr"] or r["loknr"],
                            "obserkode": r["t_obserkode"] or r["obserkode"], "turtidfra": r["turtidfra"], "turtidtil": r["turtidtil"],
                            "turnoter": r["turnoter"], "medobser": r["medobser"], "indtastning": INDTASTNING}
    return obs, list(trips.values())


async def _in_dofbasen(obs: list[dict]) -> set[tuple]:
    """(dato, artnr, loknr, obserkode) for fugle, der også står i DOFbasen (observationsgruppen, kun positive obsid'er)."""
    keyed = [o for o in obs if o["obserkode"] and o["loknr"]]
    if not keyed:
        return set()
    async with SessionLocal() as s:
        found = (await s.execute(text("""
            SELECT DISTINCT o.dato, o.artnr, o.loknr, o.obserkode FROM observations o
            JOIN unnest(CAST(:d AS date[]), CAST(:a AS int[]), CAST(:l AS int[]), CAST(:k AS varchar[])) AS k(dato, artnr, loknr, obserkode)
              ON o.dato = k.dato AND o.artnr = k.artnr AND o.loknr = k.loknr AND o.obserkode = k.obserkode
            WHERE o.obsid > 0
        """), {"d": [o["dato"] for o in keyed], "a": [o["artnr"] for o in keyed], "l": [o["loknr"] for o in keyed],
               "k": [o["obserkode"] for o in keyed]})).all()
    return {tuple(f) for f in found}


async def _observers(codes: set[str]) -> list[dict]:
    """Navnene på observatørerne: fra DOFbasens observatørregister, ellers fra brugerens navn i DOF.tool."""
    if not codes:
        return []
    async with SessionLocal() as s:
        known = {r["obserkode"]: dict(r) for r in (await s.execute(text(
            "SELECT obserkode, fornavn, efternavn, by FROM observers WHERE obserkode = ANY(:k)"), {"k": sorted(codes)})).mappings()}
        users = dict((await s.execute(text("SELECT obserkode, navn FROM users WHERE obserkode = ANY(:k)"), {"k": sorted(codes)})).all())
    out = []
    for k in sorted(codes):
        if k in known:
            out.append(known[k])
        else:
            fornavn, _, efternavn = (users.get(k) or "").strip().partition(" ")
            out.append({"obserkode": k, "fornavn": fornavn or None, "efternavn": efternavn or None, "by": None})
    return out


def _plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _fingerprint(obs: list[dict], trips: list[dict], observers: list[dict]) -> str:
    payload = json.dumps([[{k: _plain(v) for k, v in o.items() if k not in ("first_seen", "updated_at")} for o in obs],
                          [{k: _plain(v) for k, v in t.items()} for t in trips], observers], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def reconcile(force: bool = False) -> dict:
    """Byg DOF.tool-gruppens datasæt op fra indtastningerne. Skriver kun, når indholdet er ændret (eller force)."""
    obs, trips = await _desired()
    dup = await _in_dofbasen(obs)
    kept = [o for o in obs if (o["dato"], o["artnr"], o["loknr"], o["obserkode"]) not in dup]
    used = {o["turid"] for o in kept}
    trips = [t for t in trips if t["turid"] in used]
    observers = await _observers({o["obserkode"] for o in kept if o["obserkode"]})
    fp = _fingerprint(kept, trips, observers)
    mark = f"{FINGERPRINT}:{router.tool_node().key}"  # pr. node: en ny (tom) node fyldes, selv om indholdet er det samme
    if not force and await kv().get(mark) == fp:
        return {"changed": False, "observations": len(kept), "dubletter": len(obs) - len(kept)}
    async with ToolSession() as s:
        before = {r[0]: r[1] for r in (await s.execute(text("SELECT obsid, dato FROM observations"))).all()}
        await s.execute(text("DELETE FROM observations"))
        await s.execute(text("DELETE FROM trips"))
        await s.execute(text("DELETE FROM observers"))
        if observers:
            await s.execute(text("INSERT INTO observers (obserkode, fornavn, efternavn, by) VALUES (:obserkode, :fornavn, :efternavn, :by)"),
                            observers)
        if trips:
            await s.execute(text(f"INSERT INTO trips ({', '.join(TRIP_COLS)}) VALUES ({', '.join(':' + c for c in TRIP_COLS)})"), trips)
        if kept:
            await s.execute(text(f"INSERT INTO observations ({', '.join(OBS_COLS)}) VALUES ({', '.join(':' + c for c in OBS_COLS)})"), kept)
        await s.commit()
    # Ændrede datoer (nye, rettede og fjernede): de forudberegnede tal skal regnes om fra den tidligste
    after = {o["obsid"]: o["dato"] for o in kept}
    touched = [d for i, d in after.items() if before.get(i) != d] + [d for i, d in before.items() if i not in after]
    if touched or force:
        first = min(touched) if touched else min(after.values(), default=None)
        old = await kv().get(DIRTY)
        if first and (not old or first.isoformat() < old):
            await kv().set(DIRTY, first.isoformat())
    await kv().set(mark, fp)
    for v in (VERSION, "lists", "feed", "regions"):
        await bump(v)
    log.info("DOF.tool-datasættet er opdateret: %d observationer, %d ture (%d dubletter med DOFbasen udeladt)",
             len(kept), len(trips), len(obs) - len(kept))
    return {"changed": True, "observations": len(kept), "ture": len(trips), "dubletter": len(obs) - len(kept),
            "at": datetime.now(timezone.utc).isoformat()}
