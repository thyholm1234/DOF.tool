"""Feltmeldinger: egne indtastninger i portalen med DOFbasens felter.

En tur (lokalitet + dato) indeholder observationer med de samme felter som DOFbasens indtastning
(art, antal, adfærd, køn, alder, dragt, metode, tider, position, noter, hemmelig). Portalen tilføjer:

- Status – om fuglen er under observation lige nu. Indtasteren og andre brugere, der står ved fuglen,
  kan bekræfte "stadig til stede" eller melde den fløjet/ikke genfundet; følgere af tråden får besked.
- Sikkerhed – bestemmelsessikkerhed (sikker/sandsynlig/mulig) og grundlag (set, hørt, foto ...),
  samt beskyttelse: hemmelig (kun synlig for indtasteren) eller skjult præcis position.

Meldingerne vises i de samme tråde (art + lokalitet + dag) som DOFbasen-data, kører gennem de samme
alarmregler og kan eksporteres som CSV med DOFbasens kolonnenavne. De sendes ikke til dofbasen.dk.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.dof.classify import get_classifier
from portal.models import FieldReport, FieldReportUpdate, FieldTrip, User
from portal.services import alerts
from portal.services.areas import Scope
from portal.timeutil import TZ

STATUS = {"til_stede": "Under observation", "fløjet": "Fløjet/forsvundet", "ikke_genfundet": "Ikke genfundet",
          "ukendt": "Ikke angivet"}
SIKKERHED = {"sikker": "Sikker", "sandsynlig": "Sandsynlig", "mulig": "Mulig"}
GRUNDLAG = {"set": "Set", "hoert": "Hørt", "foto": "Foto", "lyd": "Lydoptagelse", "haand": "I hånden/ringmærket",
            "beskrevet": "Detaljeret beskrevet"}
KOEN = {"M": "Han", "F": "Hun"}
ALDER = {"AD": "Adult", "IMM": "Immatur", "SUB": "Subadult", "JUV": "Juvenil", "PUL": "Pullus (dununge)"}
LIVE_HOURS = 3  # 'til stede' regnes som aktuel så længe efter seneste bekræftelse
RANK = {"SU": 3, "SUB": 2, "bemaerk": 1, "alm": 0}
LEVELS = {"SU": 3, "SUB": 2, "bemaerk": 1, "all": 0, "local": 0}


def alder_label(code: str) -> str:
    if code in ALDER:
        return ALDER[code]
    if code.endswith("K+"):
        return f"{code} ({code[:-2]}. kalenderår eller ældre)"
    if code.endswith("K") and code[:-1].isdigit():
        return f"{code} ({code[:-1]}. kalenderår)"
    return code


# ---------------------------------------------------------------- kodelister og opslag


async def codes(session: AsyncSession) -> dict:
    """Kodelister til formularen, sorteret efter hvor meget de bruges i DOFbasen."""

    async def produce():
        adf = (await session.execute(text("""
            SELECT c.code, c.text, coalesce(n.n, 0) AS n FROM code_texts c
            LEFT JOIN (SELECT adfkode, count(*) AS n FROM observations WHERE dato > current_date - 365 GROUP BY adfkode) n
              ON n.adfkode = c.code
            WHERE c.kind = 'adf' ORDER BY n DESC, c.text
        """))).all()
        dragt = (await session.execute(text("SELECT code, text FROM code_texts WHERE kind = 'dragt' ORDER BY text"))).all()
        alder = (await session.execute(text("""
            SELECT alderkode, count(*) FROM observations WHERE dato > current_date - 365 AND alderkode IS NOT NULL
            GROUP BY alderkode HAVING count(*) >= 20 ORDER BY count(*) DESC
        """))).all()
        metode = (await session.execute(text("""
            SELECT metode, count(*) FROM observations WHERE dato > current_date - 365 AND metode IS NOT NULL
            GROUP BY metode HAVING count(*) >= 20 ORDER BY count(*) DESC
        """))).all()
        return {
            "adf": [{"code": c, "text": t, "popular": n > 2000} for c, t, n in adf],
            "dragt": [{"code": c, "text": t} for c, t in dragt],
            "alder": [{"code": c, "text": alder_label(c)} for c, _ in alder],
            "metode": [m for m, _ in metode],
            "koen": [{"code": k, "text": v} for k, v in KOEN.items()],
            "status": [{"code": k, "text": v} for k, v in STATUS.items()],
            "sikkerhed": [{"code": k, "text": v} for k, v in SIKKERHED.items()],
            "grundlag": [{"code": k, "text": v} for k, v in GRUNDLAG.items()],
        }

    return await cached(f"report:codes:{await version('reference')}", 86400, produce)


async def species_list(session: AsyncSession) -> list[dict]:
    """Hele artslisten til hurtig søgning i browseren (også uden net)."""

    async def produce():
        rows = (await session.execute(text("""
            SELECT artnr, navn, latin, klass, art_type FROM species WHERE NOT excluded
            ORDER BY art_type <> 'art', sortering NULLS LAST, navn
        """))).mappings().all()
        return [dict(r) for r in rows]

    return await cached(f"report:species:{await version('reference')}", 86400, produce)


async def nearest_locations(session: AsyncSession, lat: float, lon: float, n: int = 20) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune,
               round((ST_Distance(l.geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000)::numeric, 2) AS km
        FROM (SELECT * FROM locations WHERE geom IS NOT NULL
              ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) LIMIT :n * 3) l
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        ORDER BY km LIMIT :n
    """), {"lat": lat, "lon": lon, "n": n})).mappings().all()
    return [{**dict(r), "km": float(r["km"])} for r in rows]


async def recent(session: AsyncSession, user: User) -> dict:
    """Seneste lokaliteter, arter og adfærd – fra portalen og brugerens egne DOFbasen-ture."""
    locs = (await session.execute(text("""
        SELECT x.loknr, l.navn, k.navn AS kommune, max(x.dato) AS dato FROM (
            SELECT loknr, dato FROM field_trips WHERE user_id = :u
            UNION ALL SELECT loknr, dato FROM trips WHERE obserkode = :k AND dato > current_date - 400
        ) x JOIN locations l ON l.loknr = x.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        GROUP BY x.loknr, l.navn, k.navn ORDER BY max(x.dato) DESC LIMIT 12
    """), {"u": user.id, "k": user.obserkode})).mappings().all()
    species = (await session.execute(text("""
        SELECT x.artnr, s.navn, count(*) AS n FROM (
            SELECT artnr FROM field_reports WHERE user_id = :u AND dato > current_date - 60
            UNION ALL SELECT artnr FROM observations WHERE obserkode = :k AND dato > current_date - 60
        ) x JOIN species s ON s.artnr = x.artnr GROUP BY x.artnr, s.navn ORDER BY n DESC LIMIT 16
    """), {"u": user.id, "k": user.obserkode})).mappings().all()
    adf = (await session.execute(text("""
        SELECT x.adfkode AS code, c.text, count(*) AS n FROM (
            SELECT adfkode FROM field_reports WHERE user_id = :u AND adfkode IS NOT NULL
            UNION ALL SELECT adfkode FROM observations WHERE obserkode = :k AND dato > current_date - 120 AND adfkode IS NOT NULL
        ) x JOIN code_texts c ON c.kind = 'adf' AND c.code = x.adfkode GROUP BY x.adfkode, c.text ORDER BY n DESC LIMIT 8
    """), {"u": user.id, "k": user.obserkode})).mappings().all()
    return {"locations": [dict(r) for r in locs], "species": [dict(r) for r in species], "adf": [dict(r) for r in adf]}


# ---------------------------------------------------------------- ture og meldinger


async def trip_dict(session: AsyncSession, trip: FieldTrip, viewer: User | None = None) -> dict:
    loc = (await session.execute(text("""
        SELECT l.navn, l.lat, l.lon, k.navn AS kommune, a.navn AS afdeling FROM locations l
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id WHERE l.loknr = :l
    """), {"l": trip.loknr})).mappings().first() or {}
    reports = await _report_rows(session, "r.trip_id = :t", {"t": trip.id}, viewer, include_hidden=True)
    return {"id": trip.id, "dato": trip.dato.isoformat(), "loknr": trip.loknr, "lok": loc.get("navn"),
            "kommune": loc.get("kommune"), "afdeling": loc.get("afdeling"), "lat": loc.get("lat"), "lon": loc.get("lon"),
            "turtidfra": trip.turtidfra, "turtidtil": trip.turtidtil, "medobser": trip.medobser, "turnoter": trip.turnoter,
            "obserkode": trip.obserkode, "reports": reports}


async def my_trips(session: AsyncSession, user: User, limit: int = 60) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT t.id, t.dato, t.loknr, l.navn AS lok, k.navn AS kommune, t.turtidfra, t.turtidtil,
               count(r.id) AS n, count(DISTINCT r.artnr) AS n_species,
               count(r.id) FILTER (WHERE r.kategori <> 'alm') AS n_notable,
               count(r.id) FILTER (WHERE r.status = 'til_stede') AS n_live
        FROM field_trips t JOIN locations l ON l.loknr = t.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        LEFT JOIN field_reports r ON r.trip_id = t.id
        WHERE t.user_id = :u GROUP BY t.id, l.navn, k.navn ORDER BY t.dato DESC, t.id DESC LIMIT :n
    """), {"u": user.id, "n": limit})).mappings().all()
    return [{**dict(r), "dato": r["dato"].isoformat()} for r in rows]


async def classify(session: AsyncSession, report: FieldReport) -> None:
    loc = (await session.execute(text("SELECT afdeling_id FROM locations WHERE loknr = :l"), {"l": report.loknr})).first()
    report.afdelinger = [loc[0]] if loc and loc[0] else []
    navn = (await session.execute(text("SELECT navn FROM species WHERE artnr = :a"), {"a": report.artnr})).scalar() or ""
    clf = await get_classifier(session)
    report.kategori = clf.kategori(report.artnr, navn, report.dato, report.antal or 1, report.afdelinger)


async def add_status(session: AsyncSession, report: FieldReport, user: User, status: str, note: str | None) -> FieldReportUpdate:
    upd = FieldReportUpdate(report_id=report.id, user_id=user.id, status=status, note=(note or "").strip() or None)
    session.add(upd)
    report.status = status
    report.status_at = datetime.now(timezone.utc)
    await session.flush()
    return upd


def is_live(status: str, status_at: datetime | None) -> bool:
    return status == "til_stede" and status_at is not None and \
        datetime.now(timezone.utc) - status_at < timedelta(hours=LIVE_HOURS)


async def _report_rows(session: AsyncSession, where: str, params: dict, viewer: User | None,
                       include_hidden: bool = False) -> list[dict]:
    """Meldinger med opslagstekster. Hemmelige vises kun for indtasteren; skjult position kun for indtasteren."""
    vis = "(NOT r.hemmelig OR r.user_id = :viewer)" if not include_hidden else "TRUE"
    rows = (await session.execute(text(f"""
        SELECT r.*, s.navn AS art, s.latin, ca.text AS adf, cd.text AS dragt, u.navn AS observer,
               t.turtidfra, t.turtidtil, t.medobser,
               EXISTS (SELECT 1 FROM observations o WHERE o.dato = r.dato AND o.artnr = r.artnr AND o.loknr = r.loknr
                       AND o.obserkode = r.obserkode) AS i_dofbasen
        FROM field_reports r JOIN species s ON s.artnr = r.artnr JOIN field_trips t ON t.id = r.trip_id
        JOIN users u ON u.id = r.user_id
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = r.dragtkode
        WHERE {where} AND {vis} ORDER BY coalesce(r.obstidfra, t.turtidfra, '99:99'), r.created_at
    """), {**params, "viewer": viewer.id if viewer else -1})).mappings().all()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    upd = (await session.execute(text("""
        SELECT x.report_id, x.status, x.note, x.created_at, u.navn, u.obserkode FROM field_report_updates x
        JOIN users u ON u.id = x.user_id WHERE x.report_id = ANY(:ids) ORDER BY x.created_at
    """), {"ids": ids})).mappings().all()
    by: dict[int, list] = {}
    for x in upd:
        by.setdefault(x["report_id"], []).append({"status": x["status"], "note": x["note"], "navn": x["navn"] or x["obserkode"],
                                                  "at": x["created_at"].isoformat()})
    out = []
    for r in rows:
        own = viewer is not None and r["user_id"] == viewer.id
        d = {k: r[k] for k in ("id", "trip_id", "artnr", "art", "latin", "antal", "koen", "adfkode", "adf", "alderkode",
                                "dragtkode", "dragt", "metode", "obstidfra", "obstidtil", "fuglnoter", "hemmelig",
                                "skjul_position", "sikkerhed", "grundlag", "status", "kategori", "media", "obserkode",
                                "observer", "loknr", "turtidfra", "medobser", "i_dofbasen")}
        d.update(dato=r["dato"].isoformat(), status_at=r["status_at"].isoformat() if r["status_at"] else None,
                 created_at=r["created_at"].isoformat(), live=is_live(r["status"], r["status_at"]), own=own,
                 observer=r["observer"] or r["obserkode"], updates=by.get(r["id"], []))
        if own or not r["skjul_position"]:
            d.update(obs_lat=r["obs_lat"], obs_lon=r["obs_lon"], radius_m=r["radius_m"])
        out.append(d)
    return out


async def thread_reports(session: AsyncSession, day: date, artnr: int, loknr: int, viewer: User | None) -> list[dict]:
    return await _report_rows(session, "r.dato = :d AND r.artnr = :a AND r.loknr = :l", {"d": day, "a": artnr, "l": loknr}, viewer)


# ---------------------------------------------------------------- feed


async def merge_feed(session: AsyncSession, threads: list[dict], day: date, scope: Scope, level: str, local_max: int,
                     viewer: User | None) -> list[dict]:
    """Flet dagens feltmeldinger ind i feedets tråde (samme filtre som DOFbasen-data)."""
    pred, params = scope.predicate("r")
    conds = ["r.dato = :day", "(NOT r.hemmelig OR r.user_id = :viewer)", pred]
    joins = ""
    if level == "local":
        joins = "LEFT JOIN area_species_counts c ON c.area_key = :ck AND c.artnr = r.artnr"
        conds.append("((coalesce(c.n_obs, 0) < :lmax AND s.art_type IN ('art', 'underart')) OR r.kategori IN ('SU','SUB'))")
        params.update(ck=scope.key, lmax=local_max)
    elif level != "all":
        conds.append("r.kategori = ANY(:kats)")
        params["kats"] = [k for k, v in RANK.items() if v >= LEVELS.get(level, 1)]
    rows = (await session.execute(text(f"""
        SELECT r.id, r.artnr, r.loknr, r.antal, r.kategori, r.status, r.status_at, r.sikkerhed, r.created_at, r.obserkode,
               r.obstidfra, r.afdelinger, s.navn AS art, l.navn AS lok, l.lat, l.lon, k.navn AS kommune, u.navn AS observer,
               ca.text AS adf, t.turtidfra
        FROM field_reports r JOIN species s ON s.artnr = r.artnr JOIN locations l ON l.loknr = r.loknr
        JOIN field_trips t ON t.id = r.trip_id JOIN users u ON u.id = r.user_id
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode {joins}
        WHERE {' AND '.join(conds)} ORDER BY r.created_at
    """), {"day": day, "viewer": viewer.id if viewer else -1, **params})).mappings().all()
    if not rows:
        return threads
    by_id = {t["id"]: t for t in threads}
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(f"{day.isoformat()}/{r['artnr']}/{r['loknr']}", []).append(r)
    order = ["mulig", "sandsynlig", "sikker"]
    for tid, rs in groups.items():
        last = max(rs, key=lambda r: r["status_at"] or r["created_at"])
        live = any(is_live(r["status"], r["status_at"]) for r in rs)
        latest_status = max(rs, key=lambda r: r["status_at"] or datetime.min.replace(tzinfo=timezone.utc))
        portal = {"n": len(rs), "live": live, "status": latest_status["status"],
                  "status_at": latest_status["status_at"].isoformat() if latest_status["status_at"] else None,
                  "sikkerhed": max((r["sikkerhed"] for r in rs), key=order.index)}
        seen = max((r["status_at"] or r["created_at"]) for r in rs)
        rank = max(RANK.get(r["kategori"], 0) for r in rs)
        if tid in by_id:
            t = by_id[tid]
            t["portal"] = portal
            t["max_antal"] = max(t["max_antal"], max(r["antal"] for r in rs))
            if seen.isoformat() > t["last_seen"]:
                t["last_seen"] = seen.isoformat()
            if rank > RANK.get(t["kategori"], 0):
                t["kategori"] = {3: "SU", 2: "SUB", 1: "bemaerk"}.get(rank, "alm")
            continue
        first = rs[0]
        t = {"id": tid, "dato": day.isoformat(), "artnr": first["artnr"], "loknr": first["loknr"], "art": first["art"],
             "lok": first["lok"], "kommune": first["kommune"], "lat": first["lat"], "lon": first["lon"],
             "kategori": {3: "SU", 2: "SUB", 1: "bemaerk"}.get(rank, "alm"),
             "max_antal": max(r["antal"] for r in rs), "n_obs": len(rs), "n_observers": len({r["obserkode"] for r in rs}),
             "first_seen": first["created_at"].isoformat(), "last_seen": seen.isoformat(),
             "last": {"obsid": None, "antal": last["antal"], "adf": last["adf"], "tid": last["obstidfra"] or last["turtidfra"],
                      "observer": last["observer"] or last["obserkode"], "obserkode": last["obserkode"]},
             "afdelinger": list(first["afdelinger"] or []), "source": "portal", "portal": portal}
        threads.append(t)
        by_id[tid] = t
    threads.sort(key=lambda t: t["last_seen"], reverse=True)
    return threads


# ---------------------------------------------------------------- notifikationer


async def events_for(session: AsyncSession, reports: list[FieldReport]) -> list[alerts.Event]:
    out = []
    for r in reports:
        if r.hemmelig:
            continue
        row = (await session.execute(text("""
            SELECT s.navn, s.art_type, l.navn AS lok, l.kommune_kode, u.navn AS observer, c.text AS adf, t.turtidfra
            FROM species s, locations l, users u, field_trips t
            LEFT JOIN code_texts c ON c.kind = 'adf' AND c.code = :adf
            WHERE s.artnr = :a AND l.loknr = :l AND u.id = :u AND t.id = :t
        """), {"a": r.artnr, "l": r.loknr, "u": r.user_id, "t": r.trip_id, "adf": r.adfkode})).mappings().first()
        if not row:
            continue
        out.append(alerts.Event(
            obsid=-r.id, dato=r.dato, artnr=r.artnr, loknr=r.loknr, antal=r.antal or 1, kategori=r.kategori,
            art=row["navn"], art_type=row["art_type"], lok=row["lok"], kommune_kode=row["kommune_kode"],
            afdelinger=list(r.afdelinger or []), obserkode=r.obserkode, observer=row["observer"] or r.obserkode,
            adf=row["adf"], tid=r.obstidfra or row["turtidfra"], source="portal", sikkerhed=r.sikkerhed))
    return out


async def notify_new(session: AsyncSession, report_ids: list[int]) -> int:
    reports = [r for r in [await session.get(FieldReport, i) for i in report_ids] if r is not None]
    events = await events_for(session, reports)
    return await alerts.process(session, [], [], extra=events) if events else 0


async def notify_status(session: AsyncSession, report_id: int, update_id: int) -> int:
    """Statusændringer går til trådens følgere og til indtasteren (ikke til den, der meldte statussen)."""
    report = await session.get(FieldReport, report_id)
    upd = await session.get(FieldReportUpdate, update_id)
    if report is None or upd is None or report.hemmelig:
        return 0
    thread = (report.dato, report.artnr, report.loknr)
    recipients = set(await alerts.follower_ids(session, thread)) | {report.user_id}
    recipients.discard(upd.user_id)
    if not recipients:
        return 0
    row = (await session.execute(text("""
        SELECT s.navn AS art, l.navn AS lok, u.navn AS who, u.obserkode FROM species s, locations l, users u
        WHERE s.artnr = :a AND l.loknr = :l AND u.id = :u
    """), {"a": report.artnr, "l": report.loknr, "u": upd.user_id})).mappings().first()
    when = upd.created_at.astimezone(TZ).strftime("%H.%M") if upd.created_at else ""
    msg = {"title": f"{STATUS.get(upd.status, upd.status)}: {row['art']}, {row['lok']}"[:240],
           "body": " · ".join(b for b in (f"{row['who'] or row['obserkode']} {when}".strip(), upd.note) if b)[:480],
           "url": f"/#/traad/{report.dato}/{report.artnr}/{report.loknr}", "tag": f"{report.dato}-{report.artnr}-{report.loknr}",
           "kategori": report.kategori, "rule_id": None, "push": True}
    return await alerts.deliver(session, None, {(u, f"s:{upd.id}"): msg for u in recipients})


# ---------------------------------------------------------------- eksport

CSV_COLUMNS = ["Dato", "Turtidfra", "Turtidtil", "Loknr", "Loknavn", "Artnr", "Artnavn", "Latin", "Antal", "Koen",
               "Adfkode", "Adfbeskrivelse", "Alderkode", "Dragtkode", "Dragtbeskrivelse", "Obserkode", "Medobser",
               "Turnoter", "Fuglnoter", "Metode", "Obstidfra", "Obstidtil", "Hemmelig", "obs_laengdegrad",
               "obs_breddegrad", "radius", "Sikkerhed", "Grundlag", "Status"]


async def export_csv(session: AsyncSession, user: User) -> str:
    rows = (await session.execute(text("""
        SELECT r.*, t.turtidfra, t.turtidtil, t.medobser, t.turnoter, l.navn AS loknavn, s.navn AS artnavn, s.latin,
               ca.text AS adf, cd.text AS dragt
        FROM field_reports r JOIN field_trips t ON t.id = r.trip_id JOIN locations l ON l.loknr = r.loknr
        JOIN species s ON s.artnr = r.artnr
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = r.dragtkode
        WHERE r.user_id = :u ORDER BY r.dato, t.id, r.id
    """), {"u": user.id})).mappings().all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(CSV_COLUMNS)
    num = lambda v: "" if v is None else str(v).replace(".", ",")  # noqa: E731
    for r in rows:
        w.writerow([r["dato"].isoformat(), r["turtidfra"] or "", r["turtidtil"] or "", r["loknr"], r["loknavn"],
                    f"{r['artnr']:05d}", r["artnavn"], r["latin"] or "", r["antal"], r["koen"] or "", r["adfkode"] or "",
                    r["adf"] or "", r["alderkode"] or "", r["dragtkode"] or "", r["dragt"] or "", r["obserkode"],
                    r["medobser"] or "", r["turnoter"] or "", r["fuglnoter"] or "", r["metode"] or "",
                    r["obstidfra"] or "", r["obstidtil"] or "", 1 if r["hemmelig"] else 0, num(r["obs_lon"]),
                    num(r["obs_lat"]), r["radius_m"] or "", SIKKERHED.get(r["sikkerhed"], r["sikkerhed"]),
                    ", ".join(GRUNDLAG.get(g, g) for g in (r["grundlag"] or [])), STATUS.get(r["status"], r["status"])])
    return buf.getvalue()
