"""Admin → Tællinger: status, statistik, udtræk og gridceller for spontantællinger (metode 98) og FiNN-tællinger (99).

Tællingerne er ture i field_trips (api/trips.py) med fem minutters komplet artsliste; observationerne ligger i
field_reports. Positionen er turens lat/lon (dér, man stod). Gridcellerne er kvadrater i Det Danske Kvadratnet
(UTM 32/ETRS89, domain/geo.grid_cell) og regnes i Python – tællingerne er få nok til det, og så kræver det ikke PostGIS
i brugerdatabaserne."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.domain.geo import grid_cell, grid_ring
from portal.services import finn
from portal.timeutil import dk_now

SPONTAN, FINN = 98, 99
KINDS = {"alle": (SPONTAN, FINN), "spontan": (SPONTAN,), "finn": (FINN,)}
METODE = {SPONTAN: "Spontantælling", FINN: "FiNN-tælling"}


@dataclass
class Filter:
    kind: str = "alle"
    fra: date | None = None
    til: date | None = None
    site: int | None = None
    obserkode: str | None = None
    artnr: int | None = None
    cell: str | None = None  # gridcelle (filtreres i Python efter SQL'en)

    def sql(self, t: str = "t") -> tuple[str, dict]:
        w, p = [f"{t}.metode_id = ANY(:metoder)"], {"metoder": list(KINDS.get(self.kind, KINDS["alle"]))}
        if self.fra:
            w.append(f"{t}.dato >= :fra")
            p["fra"] = self.fra
        if self.til:
            w.append(f"{t}.dato <= :til")
            p["til"] = self.til
        if self.site:
            w.append(f"{t}.finn_site_id = :site")
            p["site"] = self.site
        if self.obserkode:
            w.append(f"upper({t}.obserkode) = :obs")
            p["obs"] = self.obserkode.upper()
        if self.artnr:
            w.append(f"EXISTS (SELECT 1 FROM field_reports fr WHERE fr.trip_id = {t}.id AND fr.artnr = :artnr)")
            p["artnr"] = self.artnr
        return " AND ".join(w), p


async def _cell_ids(db: AsyncSession, f: Filter) -> list[int] | None:
    """Tællingerne i filterets gridcelle (None = intet cellefilter)."""
    if not f.cell:
        return None
    km_ = int(f.cell.split("km_")[0])
    where, p = f.sql()
    rows = (await db.execute(text(f"SELECT t.id, t.lat, t.lon FROM field_trips t WHERE {where} AND t.lat IS NOT NULL"), p)).all()
    return [i for i, lat, lon in rows if grid_cell(lat, lon, km_) == f.cell]


async def _where(db: AsyncSession, f: Filter) -> tuple[str, dict]:
    where, p = f.sql()
    ids = await _cell_ids(db, f)
    if ids is not None:
        where += " AND t.id = ANY(:cell_ids)"
        p["cell_ids"] = ids
    return where, p


async def _site_names(db: AsyncSession) -> dict[int, str]:
    rows = (await db.execute(text("SELECT id, title FROM finn_sites"))).all()
    if rows:
        return dict(rows)
    return {s["id"]: s["title"] for s in (await finn.data())["sites"]}


# ---------------------------------------------------------------- status og statistik


async def overview(db: AsyncSession, f: Filter) -> dict[str, Any]:
    where, p = await _where(db, f)
    q = lambda sql, extra=None: db.execute(text(sql), {**p, **(extra or {})})  # noqa: E731
    tot = (await q(f"""
        WITH c AS (SELECT t.id, t.metode_id, t.obserkode, t.lat, t.finn_site_id FROM field_trips t WHERE {where})
        SELECT count(*) AS taellinger,
               count(*) FILTER (WHERE metode_id = {SPONTAN}) AS spontan,
               count(*) FILTER (WHERE metode_id = {FINN}) AS finn,
               count(DISTINCT upper(obserkode)) AS observatoerer,
               count(DISTINCT finn_site_id) AS omraader,
               (SELECT count(*) FROM field_reports r WHERE r.trip_id IN (SELECT id FROM c)) AS observationer,
               (SELECT coalesce(sum(r.antal), 0) FROM field_reports r WHERE r.trip_id IN (SELECT id FROM c)) AS individer,
               (SELECT count(DISTINCT r.artnr) FROM field_reports r WHERE r.trip_id IN (SELECT id FROM c)) AS arter
        FROM c""")).mappings().one()
    total = tot["taellinger"] or 0

    per_trip = (await q(f"""
        SELECT count(DISTINCT r.artnr) AS n FROM field_trips t LEFT JOIN field_reports r ON r.trip_id = t.id
        WHERE {where} GROUP BY t.id""")).scalars().all()
    months = (await q(f"""
        SELECT to_char(t.dato, 'YYYY-MM') AS m, count(*) FILTER (WHERE t.metode_id = {SPONTAN}) AS spontan,
               count(*) FILTER (WHERE t.metode_id = {FINN}) AS finn
        FROM field_trips t WHERE {where} GROUP BY 1 ORDER BY 1""")).mappings().all()
    hours = (await q(f"""
        SELECT substring(t.turtidfra, 1, 2)::int AS h, count(*) AS n FROM field_trips t
        WHERE {where} AND t.turtidfra ~ '^[0-9]{{2}}:' GROUP BY 1 ORDER BY 1""")).all()
    species = (await q(f"""
        SELECT r.artnr, s.navn, count(DISTINCT r.trip_id) AS taellinger, sum(r.antal) AS individer, max(r.antal) AS max
        FROM field_reports r JOIN field_trips t ON t.id = r.trip_id LEFT JOIN species s ON s.artnr = r.artnr
        WHERE {where} GROUP BY r.artnr, s.navn ORDER BY 3 DESC, 4 DESC LIMIT 40""")).mappings().all()
    observers = (await q(f"""
        SELECT upper(t.obserkode) AS obserkode, max(u.navn) AS navn, count(*) AS taellinger, max(t.dato) AS seneste
        FROM field_trips t LEFT JOIN users u ON u.id = t.user_id WHERE {where}
        GROUP BY 1 ORDER BY 3 DESC, 4 DESC LIMIT 25""")).mappings().all()
    names = await _site_names(db)
    sites = (await q(f"""
        SELECT t.finn_site_id AS id, count(*) AS taellinger, count(DISTINCT upper(t.obserkode)) AS observatoerer, max(t.dato) AS seneste
        FROM field_trips t WHERE {where} AND t.finn_site_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""")).mappings().all()
    counted = {r["id"] for r in sites}

    def avg(xs: list[int]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    return {
        "total": {**dict(tot), "arter_pr_taelling": avg(list(per_trip)),
                  "median_arter": sorted(per_trip)[len(per_trip) // 2] if per_trip else None},
        "maaneder": [dict(r) for r in months],
        "timer": [{"h": h, "n": n} for h, n in hours],
        "arter": [{**dict(r), "frekvens": round(r["taellinger"] / total, 3) if total else 0} for r in species],
        "observatoerer": [{**dict(r), "seneste": r["seneste"].isoformat()} for r in observers],
        "omraader": [{**dict(r), "title": names.get(r["id"], f"FiNN-område {r['id']}"), "seneste": r["seneste"].isoformat()}
                     for r in sites],
        "omraader_uden": sorted(({"id": i, "title": n} for i, n in names.items() if i not in counted and not f.site),
                                key=lambda x: x["title"].lower()) if f.kind != "spontan" else [],
    }


async def status(db: AsyncSession) -> dict[str, Any]:
    """Status uafhængigt af filteret: i gang lige nu, seneste dage, datakvalitet og FiNN-data."""
    now = dk_now()
    today, hm = now.date(), now.strftime("%H:%M")
    m = list(KINDS["alle"])
    periods = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE dato = CAST(:d AS date)) AS i_dag,
               count(*) FILTER (WHERE dato > CAST(:d AS date) - 7) AS uge,
               count(*) FILTER (WHERE dato > CAST(:d AS date) - 30) AS maaned,
               count(*) FILTER (WHERE dato >= date_trunc('year', CAST(:d AS date))) AS aar,
               count(*) AS alle,
               count(*) FILTER (WHERE metode_id = 99 AND finn_site_id IS NULL) AS finn_uden_omraade,
               count(*) FILTER (WHERE lat IS NULL) AS uden_position,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM field_reports r WHERE r.trip_id = field_trips.id)) AS uden_obs,
               count(*) FILTER (WHERE komplet IS NOT TRUE) AS ikke_komplet
        FROM field_trips WHERE metode_id = ANY(:m)"""), {"d": today, "m": m})).mappings().one()
    running = (await db.execute(text("""
        SELECT t.id, t.metode_id, t.turtidfra, t.turtidtil, upper(t.obserkode) AS obserkode, u.navn, l.navn AS lok,
               (SELECT count(*) FROM field_reports r WHERE r.trip_id = t.id) AS n_obs
        FROM field_trips t LEFT JOIN users u ON u.id = t.user_id LEFT JOIN locations l ON l.loknr = t.loknr
        WHERE t.metode_id = ANY(:m) AND t.dato = :d AND t.turtidfra <= :hm AND t.turtidtil >= :hm
        ORDER BY t.turtidfra"""), {"m": m, "d": today, "hm": hm})).mappings().all()
    # FiNN-tællinger, der står uden for det område, de er knyttet til
    names = await _site_names(db)
    data = await finn.data(db)
    rings = {s["id"]: s["ring"] for s in data["sites"]}
    outside = 0
    for sid, lat, lon in (await db.execute(text(
            "SELECT finn_site_id, lat, lon FROM field_trips WHERE metode_id = 99 AND finn_site_id IS NOT NULL AND lat IS NOT NULL"))).all():
        if sid in rings and not finn.inside((lat, lon), rings[sid]):
            outside += 1
    fs = (await db.execute(text("""
        SELECT count(*) AS alle, count(*) FILTER (WHERE aktiv AND removed_at IS NULL) AS aktive, count(*) FILTER (WHERE edited) AS rettede,
               count(*) FILTER (WHERE origin = 'portal') AS egne, count(*) FILTER (WHERE removed_at IS NOT NULL) AS fjernede,
               max(synced_at) AS synk FROM finn_sites"""))).mappings().one()
    last = (await db.execute(text("SELECT status, created_at FROM finn_syncs ORDER BY id DESC LIMIT 1"))).mappings().first()
    return {
        "perioder": dict(periods),
        "i_gang": [{**dict(r), "metode": METODE[r["metode_id"]]} for r in running],
        "finn_uden_for_omraade": outside,
        "finn": {**dict(fs), "synk": fs["synk"].isoformat() if fs["synk"] else None, "kilde": "portal" if fs["alle"] else "dofbasen",
                 "omraader_i_appen": len(data["sites"]), "punkter_i_appen": len(data["points"]), "navne": len(names),
                 "afventer": bool(last and last["status"] == "klar")},
    }


# ---------------------------------------------------------------- tællinger og udtræk

TRIP_COLS = ["id", "metode", "dato", "start", "slut", "obserkode", "observatør", "loknr", "lokalitet", "finn_område_id",
             "finn_område", "lat", "lon", "gridcelle_1km", "gridcelle_10km", "komplet", "observationer", "arter", "individer",
             "temperatur", "skydække", "vindretning", "vindstyrke", "nedbør", "nedbørstype", "sigtbarhed", "medobservatører",
             "noter", "oprettet"]
OBS_COLS = ["tælling_id", "metode", "dato", "start", "obserkode", "observatør", "loknr", "lokalitet", "finn_område_id",
            "finn_område", "lat", "lon", "gridcelle_1km", "gridcelle_10km", "observation_id", "artnr", "art", "latin", "antal",
            "køn", "adfærd", "alder", "dragt", "obs_lat", "obs_lon", "hemmelig", "sikkerhed", "noter"]


async def _trips(db: AsyncSession, f: Filter, limit: int | None = None, offset: int = 0, trip_id: int | None = None) -> list[dict]:
    where, p = await _where(db, f)
    if trip_id is not None:
        where += " AND t.id = :tid"
        p["tid"] = trip_id
    lim = f"LIMIT {int(limit)} OFFSET {int(offset)}" if limit else ""
    rows = (await db.execute(text(f"""
        SELECT t.id, t.metode_id, t.dato, t.turtidfra, t.turtidtil, upper(t.obserkode) AS obserkode, u.navn, t.loknr, l.navn AS lok,
               t.finn_site_id, t.lat, t.lon, t.komplet, t.temperatur, t.skydaekke, t.vindretning, t.vindstyrke, t.nedboer, t.nedboertype,
               t.sigtbarhed, t.medobser, t.turnoter, t.created_at,
               coalesce(a.n_obs, 0) AS n_obs, coalesce(a.n_arter, 0) AS n_arter, coalesce(a.n_ind, 0) AS n_ind
        FROM field_trips t LEFT JOIN users u ON u.id = t.user_id LEFT JOIN locations l ON l.loknr = t.loknr
        LEFT JOIN LATERAL (SELECT count(*) AS n_obs, count(DISTINCT r.artnr) AS n_arter, sum(r.antal) AS n_ind
                           FROM field_reports r WHERE r.trip_id = t.id) a ON true
        WHERE {where} ORDER BY t.dato DESC, t.turtidfra DESC NULLS LAST, t.id DESC {lim}"""), p)).mappings().all()
    return [dict(r) for r in rows]


async def count(db: AsyncSession, f: Filter) -> int:
    where, p = await _where(db, f)
    return (await db.execute(text(f"SELECT count(*) FROM field_trips t WHERE {where}"), p)).scalar() or 0


async def trips_page(db: AsyncSession, f: Filter, limit: int, offset: int) -> dict:
    names = await _site_names(db)
    rows = await _trips(db, f, limit, offset)
    return {"total": await count(db, f), "rows": [{
        "id": r["id"], "metode": METODE[r["metode_id"]], "metode_id": r["metode_id"], "dato": r["dato"].isoformat(),
        "fra": r["turtidfra"], "til": r["turtidtil"], "obserkode": r["obserkode"], "navn": r["navn"], "loknr": r["loknr"],
        "lok": r["lok"], "finn_site_id": r["finn_site_id"], "omraade": names.get(r["finn_site_id"]) if r["finn_site_id"] else None,
        "lat": r["lat"], "lon": r["lon"], "n_obs": r["n_obs"], "n_arter": r["n_arter"], "n_ind": r["n_ind"],
        "celle": grid_cell(r["lat"], r["lon"], 10) if r["lat"] is not None else None} for r in rows]}


def _cells(lat, lon) -> tuple[str | None, str | None]:
    return (grid_cell(lat, lon, 1), grid_cell(lat, lon, 10)) if lat is not None and lon is not None else (None, None)


async def export_rows(db: AsyncSession, f: Filter, level: str) -> tuple[list[str], list[list]]:
    """Udtrækket som kolonner og rækker: én række pr. tælling eller pr. observation."""
    names = await _site_names(db)
    trips = await _trips(db, f)
    if level == "taellinger":
        out = []
        for r in trips:
            c1, c10 = _cells(r["lat"], r["lon"])
            out.append([r["id"], METODE[r["metode_id"]], r["dato"].isoformat(), r["turtidfra"], r["turtidtil"], r["obserkode"], r["navn"],
                        r["loknr"], r["lok"], r["finn_site_id"], names.get(r["finn_site_id"]), r["lat"], r["lon"], c1, c10,
                        "ja" if r["komplet"] else "nej", r["n_obs"], r["n_arter"], r["n_ind"], r["temperatur"], r["skydaekke"],
                        r["vindretning"], r["vindstyrke"], r["nedboer"], r["nedboertype"], r["sigtbarhed"], r["medobser"],
                        r["turnoter"], r["created_at"].isoformat(timespec="seconds")])
        return TRIP_COLS, out
    by_id = {r["id"]: r for r in trips}
    obs = (await db.execute(text("""
        SELECT r.trip_id, r.id, r.artnr, s.navn, s.latin, r.antal, r.koen, r.adfkode, r.alderkode, r.dragtkode, r.obs_lat, r.obs_lon,
               r.hemmelig, r.sikkerhed, r.fuglnoter
        FROM field_reports r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.trip_id = ANY(:ids)
        ORDER BY r.trip_id, r.id"""), {"ids": list(by_id)})).mappings().all() if by_id else []
    out = []
    for o in obs:
        t = by_id[o["trip_id"]]
        c1, c10 = _cells(t["lat"], t["lon"])
        out.append([t["id"], METODE[t["metode_id"]], t["dato"].isoformat(), t["turtidfra"], t["obserkode"], t["navn"], t["loknr"],
                    t["lok"], t["finn_site_id"], names.get(t["finn_site_id"]), t["lat"], t["lon"], c1, c10, o["id"], o["artnr"],
                    o["navn"], o["latin"], o["antal"], o["koen"], o["adfkode"], o["alderkode"], o["dragtkode"], o["obs_lat"],
                    o["obs_lon"], "ja" if o["hemmelig"] else "nej", o["sikkerhed"], o["fuglnoter"]])
    return OBS_COLS, out


async def trip_detail(db: AsyncSession, tid: int) -> dict | None:
    rows = await _trips(db, Filter(), trip_id=tid)
    t = rows[0] if rows else None
    if t is None:
        return None
    obs = (await db.execute(text("""
        SELECT r.id, r.artnr, s.navn, r.antal, r.adfkode, r.koen, r.alderkode, r.hemmelig, r.fuglnoter
        FROM field_reports r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.trip_id = :t ORDER BY s.sortering NULLS LAST, r.id"""),
        {"t": tid})).mappings().all()
    names = await _site_names(db)
    return {**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in t.items()}, "metode": METODE[t["metode_id"]],
            "omraade": names.get(t["finn_site_id"]), "observationer": [dict(o) for o in obs]}


# ---------------------------------------------------------------- gridceller


async def grid(db: AsyncSession, f: Filter, km_: int) -> dict:
    """Tællingerne samlet pr. kvadrat: antal tællinger, observatører, arter (i alt og i gennemsnit pr. tælling)."""
    where, p = f.sql()
    rows = (await db.execute(text(f"""
        SELECT t.id, t.lat, t.lon, t.dato, upper(t.obserkode) AS obs, t.metode_id,
               coalesce(array_agg(DISTINCT r.artnr) FILTER (WHERE r.artnr IS NOT NULL), '{{}}') AS arter, coalesce(sum(r.antal), 0) AS ind
        FROM field_trips t LEFT JOIN field_reports r ON r.trip_id = t.id
        WHERE {where} AND t.lat IS NOT NULL AND t.lon IS NOT NULL GROUP BY t.id"""), p)).mappings().all()
    cells: dict[str, dict] = {}
    for r in rows:
        code = grid_cell(r["lat"], r["lon"], km_)
        c = cells.setdefault(code, {"code": code, "taellinger": 0, "spontan": 0, "finn": 0, "obs": set(), "arter": set(),
                                    "sum_arter": 0, "individer": 0, "seneste": r["dato"]})
        c["taellinger"] += 1
        c["spontan" if r["metode_id"] == SPONTAN else "finn"] += 1
        c["obs"].add(r["obs"])
        c["arter"].update(r["arter"])
        c["sum_arter"] += len(r["arter"])
        c["individer"] += int(r["ind"] or 0)
        c["seneste"] = max(c["seneste"], r["dato"])
    out = [{"code": c["code"], "ring": grid_ring(c["code"]), "taellinger": c["taellinger"], "spontan": c["spontan"], "finn": c["finn"],
            "observatoerer": len(c["obs"]), "arter": len(c["arter"]), "arter_pr_taelling": round(c["sum_arter"] / c["taellinger"], 1),
            "individer": c["individer"], "seneste": c["seneste"].isoformat()} for c in cells.values()]
    out.sort(key=lambda c: -c["taellinger"])
    return {"km": km_, "celler": out, "taellinger": len(rows)}


async def grid_cell_detail(db: AsyncSession, f: Filter, code: str) -> dict:
    """Én gridcelle: tællingerne i den og arterne med frekvens (andel af cellens tællinger)."""
    f.cell = code
    ids = await _cell_ids(db, f) or []
    species = (await db.execute(text("""
        SELECT r.artnr, s.navn, count(DISTINCT r.trip_id) AS taellinger, sum(r.antal) AS individer
        FROM field_reports r LEFT JOIN species s ON s.artnr = r.artnr WHERE r.trip_id = ANY(:ids)
        GROUP BY r.artnr, s.navn ORDER BY 3 DESC, 4 DESC"""), {"ids": ids})).mappings().all() if ids else []
    page = await trips_page(db, f, 200, 0)
    n = len(ids)
    return {"code": code, "ring": grid_ring(code), "taellinger": n,
            "arter": [{**dict(r), "frekvens": round(r["taellinger"] / n, 3) if n else 0} for r in species],
            "rows": page["rows"]}


def default_period() -> tuple[date, date]:
    today = dk_now().date()
    return today - timedelta(days=365), today
