"""Én art på én lokalitet: forekomst gennem året og over årene, tidspunkt på dagen i forhold til solen, og adfærd.

Dage tælles som på lokalitetssiden: én pr. dag med dagens største enkeltobservation. 'Andel' er dage med arten ud af
dage med observationer på lokaliteten (af alle arter) – det udligner, at nogle uger og år er bedre dækket end andre.
'Fugle pr. liste' er artens antal lagt sammen over alle turlister på lokaliteten, delt med antallet af turlister
(fra trips – samme tal som de forskellige turid'er i observationerne, men uden at læse dem alle).

Tidspunkt: observationens klokkeslæt (midt i intervallet, når fra–til er højst 3 timer, ellers fra-tiden). '00:00' er
DOFbasens 'ikke angivet' og tæller ikke. Hver observation lægges ved den nærmeste af solopgang og solnedgang den dag på
lokaliteten.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services.sun import sun_times
from portal.timeutil import dk_today

WEEKS = 52
BIN = 30          # minutter pr. søjle omkring solopgang/-nedgang
SPAN = 9 * 60     # ± minutter omkring begivenheden – dækker halvdelen af den længste dag og nat i Danmark
DK = (56.0, 10.5)  # lokaliteter uden koordinater
DIR16 = {"N": 0, "NNØ": 22.5, "NØ": 45, "ØNØ": 67.5, "Ø": 90, "ØSØ": 112.5, "SØ": 135, "SSØ": 157.5, "S": 180,
         "SSV": 202.5, "SV": 225, "VSV": 247.5, "V": 270, "VNV": 292.5, "NV": 315, "NNV": 337.5}
DIR8 = ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"]


def _week(d: date) -> int:
    return min((d.timetuple().tm_yday - 1) // 7, WEEKS - 1)


def _minutes(s: str | None) -> int | None:
    if not s or s == "00:00" or ":" not in s:
        return None
    h, _, m = s.partition(":")
    try:
        v = int(h) * 60 + int(m)
    except ValueError:
        return None
    return v if 0 <= v < 1440 else None


def obs_minute(fra: str | None, til: str | None) -> int | None:
    a, b = _minutes(fra), _minutes(til)
    if a is None:
        return None
    return (a + b) // 2 if b is not None and 0 < b - a <= 180 else a


def _direction(code: str | None) -> str | None:
    """Trækretning (otte streger) fra adfærdskoden, fx 'SV', 'R SV' eller 'FU NØ'."""
    for tok in (code or "").split():
        if tok in DIR16:
            return DIR8[round(DIR16[tok] / 45) % 8]
    return None


async def build(session: AsyncSession, loknr: int, artnr: int, year: int | None) -> dict | None:
    loc = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.loknr = :l
    """), {"l": loknr})).mappings().first()
    sp = (await session.execute(text("SELECT artnr, navn, latin, klass FROM species WHERE artnr = :a"), {"a": artnr})).mappings().first()
    if not loc or not sp:
        return None
    rows = (await session.execute(text("""
        SELECT o.dato, o.antal, o.adfkode, o.obstidfra, o.obstidtil, o.turid FROM observations o
        WHERE o.loknr = :l AND o.artnr = :a AND o.hemmelig = 0 AND o.antal > 0
    """), {"l": loknr, "a": artnr})).all()
    visit_days = (await session.execute(text("SELECT DISTINCT dato FROM observations WHERE loknr = :l AND hemmelig = 0"),
                                        {"l": loknr})).scalars().all()
    lists_by_year = dict((await session.execute(text("""
        SELECT extract(year FROM dato)::int, count(*) FROM trips WHERE loknr = :l GROUP BY 1
    """), {"l": loknr})).all())
    codes = dict((await session.execute(text("SELECT code, text FROM code_texts WHERE kind = 'adf'"))).all())
    lat, lon = (loc["lat"], loc["lon"]) if loc["lat"] is not None else DK

    # Én pr. dag: dagens største enkeltobservation
    day_max: dict[date, int] = {}
    for r in rows:
        if r.antal > day_max.get(r.dato, 0):
            day_max[r.dato] = r.antal
    visits_by_year: dict[int, int] = defaultdict(int)
    for d in visit_days:
        visits_by_year[d.year] += 1

    per_year: dict[int, dict] = {}
    for d, n in day_max.items():
        y = per_year.setdefault(d.year, {"year": d.year, "days": 0, "max": 0, "max_date": None, "birds": 0})
        y["days"] += 1
        y["birds"] += n
        if n > y["max"] or (n == y["max"] and d > y["max_date"]):
            y["max"], y["max_date"] = n, d
    list_birds: dict[int, int] = defaultdict(int)
    for r in rows:
        list_birds[r.dato.year] += r.antal
    years = [{**y, "visits": visits_by_year.get(y["year"], 0), "max_date": y["max_date"].isoformat(),
              "lists": lists_by_year.get(y["year"], 0), "list_birds": list_birds[y["year"]]}
             for y in sorted(per_year.values(), key=lambda y: y["year"])]

    def chosen(d: date) -> bool:
        return year is None or d.year == year

    sel_days = {d: n for d, n in day_max.items() if chosen(d)}
    sel_rows = [r for r in rows if chosen(r.dato)]
    sel_visits = [d for d in visit_days if chosen(d)]

    # Gennem året (uger à 7 dage fra 1. januar)
    weeks = [{"days": 0, "visits": 0, "max": 0, "birds": 0} for _ in range(WEEKS)]
    for d in sel_visits:
        weeks[_week(d)]["visits"] += 1
    for d, n in sel_days.items():
        w = weeks[_week(d)]
        w["days"] += 1
        w["birds"] += n
        w["max"] = max(w["max"], n)

    # Tidspunkt på dagen
    rise = [[0, 0] for _ in range(2 * SPAN // BIN)]  # [observationer, fugle] pr. søjle fra -SPAN til +SPAN
    sset = [[0, 0] for _ in range(2 * SPAN // BIN)]
    grid = [[0] * 48 for _ in range(WEEKS)]          # observationer pr. uge × halve time (klokkeslæt)
    grid_birds = [[0] * 48 for _ in range(WEEKS)]
    timed = 0
    for r in sel_rows:
        m = obs_minute(r.obstidfra, r.obstidtil)
        if m is None:
            continue
        timed += 1
        w = _week(r.dato)
        grid[w][m // 30] += 1
        grid_birds[w][m // 30] += r.antal
        st = sun_times(r.dato, round(lat, 2), round(lon, 2))
        if st is None:
            continue
        dr, ds = m - st[0], m - st[1]
        target, off = (rise, dr) if abs(dr) <= abs(ds) else (sset, ds)
        i = min(max((off + SPAN) // BIN, 0), len(target) - 1)
        target[i][0] += 1
        target[i][1] += r.antal

    # Solens gang gennem året (midt i hver uge) for det valgte år, ellers i år
    ref_year = year or dk_today().year
    curve = []
    for w in range(WEEKS):
        st = sun_times(date(ref_year, 1, 1) + timedelta(days=w * 7 + 3), round(lat, 2), round(lon, 2))
        curve.append(list(st) if st else None)

    # Adfærd
    by_code: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    months: dict[str, list[int]] = defaultdict(lambda: [0] * 12)
    dirs = {k: [0, 0] for k in DIR8}
    for r in sel_rows:
        code = (r.adfkode or "").strip()
        by_code[code][0] += 1
        by_code[code][1] += r.antal
        months[code][r.dato.month - 1] += 1
        dd = _direction(code)
        if dd:
            dirs[dd][0] += 1
            dirs[dd][1] += r.antal
    behaviour = sorted(({"code": c, "text": codes.get(c) or ("Ikke angivet" if not c else c), "n": v[0], "birds": v[1],
                         "months": months[c]} for c, v in by_code.items()), key=lambda b: -b["n"])

    first, last = (min(sel_days), max(sel_days)) if sel_days else (None, None)
    rec = max(sel_days.items(), key=lambda kv: (kv[1], kv[0])) if sel_days else None
    return {
        "location": dict(loc), "species": dict(sp), "year": year, "years": years,
        "stats": {"days": len(sel_days), "obs": len(sel_rows), "visits": len(sel_visits), "birds": sum(sel_days.values()),
                  "lists": sum(n for y, n in lists_by_year.items() if year is None or y == year),
                  "list_birds": sum(r.antal for r in sel_rows),
                  "max": rec[1] if rec else None, "max_date": rec[0].isoformat() if rec else None,
                  "first": first.isoformat() if first else None, "last": last.isoformat() if last else None},
        "weeks": weeks,
        "time": {"timed": timed, "bin": BIN, "span": SPAN, "rise": rise, "set": sset, "grid": grid, "grid_birds": grid_birds,
                 "curve": curve, "curve_year": ref_year},
        "behaviour": behaviour, "directions": [{"dir": k, "n": v[0], "birds": v[1]} for k, v in dirs.items()],
    }
