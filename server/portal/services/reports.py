"""Feltmeldinger: egne indtastninger i portalen med DOFbasens felter.

Hver melding er én fugl med dato, koordinat og lokalitet (foreslået ud fra koordinaten) og de samme felter som
DOFbasens indtastning (art, antal, adfærd, køn, alder, dragt, metode, tider, noter, hemmelig). Ældre meldinger kan
høre til en tur med turtider og turnoter. Portalen tilføjer:

- Status – indtasteren kan angive, om fuglen er under observation. Derefter meldes status på selve tråden
  (thread_status nedenfor), så alle kan melde på SU/SUB-fund – også dem der kun er i DOFbasen.
- Sikkerhed – bestemmelsessikkerhed (sikker/sandsynlig/mulig) og grundlag (set, hørt, foto ...),
  samt beskyttelse: hemmelig (kun synlig for indtasteren) eller skjult præcis position.

Meldingerne vises i de samme tråde (art + lokalitet + dag) som DOFbasen-data, kører gennem de samme
alarmregler og sendes ikke til dofbasen.dk.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, version
from portal.dof.classify import get_classifier
from portal.models import FieldReport, FieldReportUpdate, ThreadStatus, User
from portal.services import alerts, seasons
from portal.services.areas import Scope
from portal.timeutil import TZ

STATUS = {"til_stede": "Under observation", "fløjet": "Fløjet/forsvundet", "ikke_genfundet": "Ikke genfundet",
          "ukendt": "Ikke angivet"}
SIKKERHED = {"sikker": "Sikker", "sandsynlig": "Sandsynlig", "mulig": "Mulig"}
GRUNDLAG = {"set": "Set", "hoert": "Hørt", "foto": "Foto", "lyd": "Lydoptagelse", "haand": "I hånden/ringmærket",
            "beskrevet": "Detaljeret beskrevet"}
# Markeringer på en observation (som afkrydsningerne i DOFbasen-appen)
MAERKER = {"kalder": "Kalder", "hoert": "Kun hørt", "traek_ind": "Trækker ind", "traek_ud": "Trækker ud", "burfugl": "Burfugl",
           "syg": "Syg", "olie": "Olieskadet", "fiskegrej": "Fiskegrej", "plastik": "Plastik"}
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
            "maerker": [{"code": k, "text": v} for k, v in MAERKER.items()],
        }

    return await cached(f"report:codes:v2:{await version('reference')}", 86400, produce)


async def species_list(session: AsyncSession) -> list[dict]:
    """Hele artslisten til hurtig søgning i browseren (også uden net)."""

    async def produce():
        rows = (await session.execute(text("""
            SELECT artnr, navn, latin, klass, art_type, list_artnr FROM species WHERE NOT excluded
            ORDER BY art_type <> 'art', sortering NULLS LAST, navn
        """))).mappings().all()
        return [dict(r) for r in rows]

    return await cached(f"report:species:v2:{await version('reference')}", 86400, produce)  # v2: med list_artnr


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
    args = {"u": user.id, "k": user.obserkode}
    # Portalens egne meldinger (brugergruppen) og brugerens DOFbasen-data (observationsgruppen) hentes hver for sig og lægges sammen
    mine_locs = (await session.execute(text("SELECT loknr, max(dato) AS dato FROM field_reports WHERE user_id = :u GROUP BY loknr"), args)).all()
    dof_locs = (await session.execute(text("SELECT loknr, max(dato) AS dato FROM trips WHERE obserkode = :k AND dato > current_date - 400 "
                                           "AND loknr IS NOT NULL GROUP BY loknr"), args)).all()
    last: dict[int, date] = {}
    for loknr, dato in [*mine_locs, *dof_locs]:
        if loknr is not None and (loknr not in last or dato > last[loknr]):
            last[loknr] = dato
    top = sorted(last.items(), key=lambda kv: kv[1], reverse=True)[:12]
    locs = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.loknr = ANY(:l)"""), {"l": [t[0] for t in top]})).mappings().all()
    by_loc = {r["loknr"]: r for r in locs}
    locs = [{**dict(by_loc[n]), "dato": d} for n, d in top if n in by_loc]

    counts: dict[int, int] = {}
    for q, w in (("SELECT artnr, count(*) FROM field_reports WHERE user_id = :u AND dato > current_date - 60 GROUP BY artnr", args),
                 ("SELECT artnr, count(*) FROM observations WHERE obserkode = :k AND dato > current_date - 60 GROUP BY artnr", args)):
        for artnr, n in (await session.execute(text(q), w)).all():
            counts[artnr] = counts.get(artnr, 0) + n
    names = dict((await session.execute(text("SELECT artnr, navn FROM species WHERE artnr = ANY(:a)"), {"a": list(counts)})).all())
    species = [{"artnr": a, "navn": names[a], "n": n} for a, n in sorted(counts.items(), key=lambda kv: -kv[1]) if a in names][:16]

    codes: dict[str, int] = {}
    for q in ("SELECT adfkode, count(*) FROM field_reports WHERE user_id = :u AND adfkode IS NOT NULL GROUP BY adfkode",
              "SELECT adfkode, count(*) FROM observations WHERE obserkode = :k AND dato > current_date - 120 "
              "AND adfkode IS NOT NULL GROUP BY adfkode"):
        for code, n in (await session.execute(text(q), args)).all():
            codes[code] = codes.get(code, 0) + n
    texts = dict((await session.execute(text("SELECT code, text FROM code_texts WHERE kind = 'adf' AND code = ANY(:c)"),
                                        {"c": list(codes)})).all())
    adf = [{"code": c, "text": texts[c], "n": n} for c, n in sorted(codes.items(), key=lambda kv: -kv[1]) if c in texts][:8]
    return {"locations": locs, "species": species, "adf": adf}


# ---------------------------------------------------------------- meldinger


async def _in_dofbasen(session: AsyncSession, rows: list[dict]) -> set[tuple]:
    """Hvilke meldinger (dato, artnr, loknr, obserkode) findes også som observation i DOFbasen (observationsgruppen)."""
    if not rows:
        return set()
    found = (await session.execute(text("""
        SELECT DISTINCT dato, artnr, loknr, obserkode FROM observations
        WHERE obserkode = ANY(:k) AND dato BETWEEN :a AND :b AND artnr = ANY(:art) AND loknr = ANY(:lok)
    """), {"k": list({r["obserkode"] for r in rows}), "a": min(r["dato"] for r in rows), "b": max(r["dato"] for r in rows),
           "art": list({r["artnr"] for r in rows}), "lok": list({r["loknr"] for r in rows})})).all()
    return {tuple(f) for f in found}


async def my_reports(session: AsyncSession, user: User, limit: int = 2000) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT r.id, r.dato, coalesce(r.obstidfra, t.turtidfra) AS tid, r.artnr, s.navn AS art, s.sortering,
               r.antal, ca.text AS adf, r.loknr, l.navn AS lok, k.navn AS kommune, r.kategori, r.sikkerhed, r.status,
               r.status_at, jsonb_array_length(coalesce(r.media, '[]'::jsonb)) AS n_media, r.created_at, r.obserkode
        FROM field_reports r LEFT JOIN field_trips t ON t.id = r.trip_id JOIN species s ON s.artnr = r.artnr
        JOIN locations l ON l.loknr = r.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
        WHERE r.user_id = :u ORDER BY r.dato DESC, tid DESC NULLS LAST, r.id DESC LIMIT :n
    """), {"u": user.id, "n": limit})).mappings().all()
    seen = await _in_dofbasen(session, rows)
    rows = [{**dict(r), "i_dofbasen": (r["dato"], r["artnr"], r["loknr"], r["obserkode"]) in seen} for r in rows]
    return [{**dict(r), "dato": r["dato"].isoformat(), "live": is_live(r["status"], r["status_at"]),
             "status_at": r["status_at"].isoformat() if r["status_at"] else None, "created_at": r["created_at"].isoformat()}
            for r in rows]


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
               t.turtidfra, t.turtidtil, t.medobser, l.navn AS lok, k.navn AS kommune, l.lat AS lok_lat, l.lon AS lok_lon
        FROM field_reports r JOIN species s ON s.artnr = r.artnr LEFT JOIN field_trips t ON t.id = r.trip_id
        JOIN users u ON u.id = r.user_id JOIN locations l ON l.loknr = r.loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
        LEFT JOIN code_texts cd ON cd.kind = 'dragt' AND cd.code = r.dragtkode
        WHERE {where} AND {vis} ORDER BY coalesce(r.obstidfra, t.turtidfra, '99:99'), r.created_at
    """), {**params, "viewer": viewer.id if viewer else -1})).mappings().all()
    if not rows:
        return []
    seen = await _in_dofbasen(session, rows)
    rows = [{**dict(r), "i_dofbasen": (r["dato"], r["artnr"], r["loknr"], r["obserkode"]) in seen} for r in rows]
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
        d = {k: r[k] for k in ("id", "artnr", "art", "latin", "antal", "koen", "adfkode", "adf", "alderkode",
                                "dragtkode", "dragt", "metode", "obstidfra", "obstidtil", "fuglnoter", "hemmelig",
                                "skjul_position", "sikkerhed", "grundlag", "retningslinjer", "status", "kategori", "media", "obserkode",
                                "observer", "loknr", "lok", "kommune", "lok_lat", "lok_lon", "turtidfra", "medobser",
                                "i_dofbasen", "trip_id", "antalmetode", "maerker", "client_id")}
        d.update(kilde="portal", dato=r["dato"].isoformat(), status_at=r["status_at"].isoformat() if r["status_at"] else None,
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
                     viewer: User | None, local_basis: str = "lokalitet") -> list[dict]:
    """Flet dagens feltmeldinger ind i feedets tråde (samme filtre som DOFbasen-data)."""
    pred, params = scope.predicate("r")
    conds = ["r.dato = :day", "(NOT r.hemmelig OR r.user_id = :viewer)", pred]
    if level not in ("local", "pheno", "all"):
        conds.append("r.kategori = ANY(:kats)")
        params["kats"] = [k for k, v in RANK.items() if v >= LEVELS.get(level, 1)]
    # Feltmeldinger ligger i brugergruppen; lokale artstal og fænologi i observationsgruppen. De slås derfor op for sig
    # bagefter og filtreres her – en sætning må ikke blande de to grupper.
    rows = [dict(r) for r in (await session.execute(text(f"""
        SELECT r.id, r.artnr, r.loknr, r.antal, r.kategori, r.status, r.status_at, r.sikkerhed, r.created_at, r.obserkode,
               r.obstidfra, r.afdelinger, s.navn AS art, s.art_type, coalesce(s.list_artnr, s.artnr) AS list_or_artnr,
               l.navn AS lok, l.lat, l.lon, k.navn AS kommune, u.navn AS observer, ca.text AS adf, t.turtidfra
        FROM field_reports r JOIN species s ON s.artnr = r.artnr JOIN locations l ON l.loknr = r.loknr
        LEFT JOIN field_trips t ON t.id = r.trip_id JOIN users u ON u.id = r.user_id
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        LEFT JOIN code_texts ca ON ca.kind = 'adf' AND ca.code = r.adfkode
        WHERE {' AND '.join(conds)} ORDER BY r.created_at
    """), {"day": day, "viewer": viewer.id if viewer else -1, **params})).mappings().all()]
    if rows and level == "local":
        # Estimerede fund i alle år – på meldingens lokalitet eller i hele området (som feed.threads)
        arts = list({r["artnr"] for r in rows})
        if local_basis == "omraade":
            counts = {(None, a): n for a, n in (await session.execute(text(
                "SELECT artnr, n_finds FROM area_species_counts WHERE area_key = :ck AND artnr = ANY(:a)"),
                {"ck": scope.count_key, "a": arts})).all()}
        else:
            counts = {(lok, a): n for lok, a, n in (await session.execute(text(
                "SELECT loknr, artnr, n_finds FROM loc_species_finds WHERE loknr = ANY(:l) AND artnr = ANY(:a)"),
                {"l": list({r["loknr"] for r in rows}), "a": arts})).all()}
        place = (lambda r: None) if local_basis == "omraade" else (lambda r: r["loknr"])
        rows = [r for r in rows if r["kategori"] in ("SU", "SUB")
                or (counts.get((place(r), r["artnr"]), 0) < local_max and r["art_type"] in ("art", "underart"))]
    elif rows and level == "pheno":
        doy = seasons.doy365(day)
        season = {a: (fra, til, src) for a, fra, til, src in (await session.execute(text(
            "SELECT artnr, start_doy, end_doy, source FROM species_season WHERE artnr = ANY(:a)"),
            {"a": list({r["list_or_artnr"] for r in rows})})).all()}
        kept = []
        for r in rows:
            ss = season.get(r["list_or_artnr"])
            if ss is None or r["art_type"] not in ("art", "underart"):
                continue
            fra, til, src = ss
            outside = (doy < fra or doy > til) if fra <= til else (til < doy < fra)  # samme regel som feed.PHENO_COND
            if outside:
                kept.append({**r, "ph_from": fra, "ph_to": til, "ph_source": src})
        rows = kept
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
             "afdelinger": list(first["afdelinger"] or []), "source": "portal", "portal": portal,
             **({"pheno": {"fra": first["ph_from"], "til": first["ph_to"], "kilde": first["ph_source"]}} if level == "pheno" else {})}
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
            SELECT s.navn, s.art_type, s.list_artnr, l.navn AS lok, l.kommune_kode, u.navn AS observer, c.text AS adf, t.turtidfra
            FROM species s JOIN locations l ON l.loknr = :l JOIN users u ON u.id = :u
            LEFT JOIN field_trips t ON t.id = :t
            LEFT JOIN code_texts c ON c.kind = 'adf' AND c.code = :adf
            WHERE s.artnr = :a
        """), {"a": r.artnr, "l": r.loknr, "u": r.user_id, "t": r.trip_id, "adf": r.adfkode})).mappings().first()
        if not row:
            continue
        out.append(alerts.Event(
            obsid=-r.id, dato=r.dato, artnr=r.artnr, loknr=r.loknr, antal=r.antal or 1, kategori=r.kategori,
            art=row["navn"], art_type=row["art_type"], lok=row["lok"], kommune_kode=row["kommune_kode"],
            afdelinger=list(r.afdelinger or []), obserkode=r.obserkode, observer=row["observer"] or r.obserkode,
            adf=row["adf"], tid=r.obstidfra or row["turtidfra"], source="portal", sikkerhed=r.sikkerhed,
            list_artnr=row["list_artnr"]))
    return out


async def notify_new(session: AsyncSession, report_ids: list[int]) -> int:
    reports = [r for r in [await session.get(FieldReport, i) for i in report_ids] if r is not None]
    events = await events_for(session, reports)
    return await alerts.process(session, [], [], extra=events) if events else 0


# ---------------------------------------------------------------- status på SU/SUB-tråde


async def rare_kategori(session: AsyncSession, day: date, artnr: int, loknr: int) -> str | None:
    """'SU'/'SUB' hvis tråden har en synlig SU- eller SUB-observation (DOFbasen eller portalen), ellers None."""
    args = {"d": day, "a": artnr, "l": loknr}
    rank = max([(await session.execute(text(f"""
        SELECT max(CASE kategori WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 ELSE 0 END) FROM {table}
        WHERE dato = :d AND artnr = :a AND loknr = :l AND {hidden}"""), args)).scalar() or 0
        for table, hidden in (("observations", "hemmelig = 0"), ("field_reports", "NOT hemmelig"))])
    return {3: "SU", 2: "SUB"}.get(rank)


def _summary(rows: list) -> dict | None:
    if not rows:
        return None
    last = rows[-1]
    return {"status": last["status"], "status_at": last["created_at"].isoformat(),
            "live": is_live(last["status"], last["created_at"])}


async def thread_status(session: AsyncSession, day: date, artnr: int, loknr: int, viewer: User | None) -> dict:
    """Trådens statusmeldinger (ældste først) og den aktuelle status (den seneste melding)."""
    rows = (await session.execute(text("""
        SELECT x.id, x.status, x.created_at, x.user_id, u.navn, u.obserkode FROM thread_statuses x
        JOIN users u ON u.id = x.user_id WHERE x.dato = :d AND x.artnr = :a AND x.loknr = :l ORDER BY x.created_at, x.id
    """), {"d": day, "a": artnr, "l": loknr})).mappings().all()
    return {"rare": await rare_kategori(session, day, artnr, loknr) is not None, "current": _summary(rows),
            "updates": [{"id": r["id"], "status": r["status"], "navn": r["navn"] or r["obserkode"],
                         "at": r["created_at"].isoformat(), "own": viewer is not None and r["user_id"] == viewer.id}
                        for r in rows]}


async def status_many(session: AsyncSession, day: date) -> dict[str, dict]:
    """Aktuel status pr. tråd-id for dagens tråde med statusmeldinger (til feedet)."""
    rows = (await session.execute(text("""
        SELECT DISTINCT ON (artnr, loknr) artnr, loknr, status, created_at FROM thread_statuses
        WHERE dato = :d ORDER BY artnr, loknr, created_at DESC, id DESC
    """), {"d": day})).mappings().all()
    return {f"{day.isoformat()}/{r['artnr']}/{r['loknr']}": _summary([r]) for r in rows}


async def notify_thread_status(session: AsyncSession, status_id: int, kategori: str) -> int:
    """En statusmelding går til trådens følgere (ikke til den, der meldte den)."""
    st = await session.get(ThreadStatus, status_id)
    if st is None:
        return 0  # trukket tilbage inden afsendelse
    recipients = set(await alerts.follower_ids(session, (st.dato, st.artnr, st.loknr)))
    recipients.discard(st.user_id)
    if not recipients:
        return 0
    row = (await session.execute(text("""
        SELECT s.navn AS art, l.navn AS lok, u.navn AS who, u.obserkode FROM species s, locations l, users u
        WHERE s.artnr = :a AND l.loknr = :l AND u.id = :u
    """), {"a": st.artnr, "l": st.loknr, "u": st.user_id})).mappings().first()
    when = st.created_at.astimezone(TZ).strftime("%H.%M") if st.created_at else ""
    msg = {"title": f"{STATUS.get(st.status, st.status)}: {row['art']}, {row['lok']}"[:240],
           "body": f"{row['who'] or row['obserkode']} {when}".strip()[:480],
           "url": f"/#/traad/{st.dato}/{st.artnr}/{st.loknr}", "tag": f"{st.dato}-{st.artnr}-{st.loknr}",
           "kategori": kategori, "rule_id": None, "push": True}
    return await alerts.deliver(session, None, {(u, f"ts:{st.id}"): msg for u in recipients})
