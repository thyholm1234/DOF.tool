"""Estimeret antal fund og fugle af SU- og SUB-arter.

Observationerne samles pr. dag, lokalitet og adfærd i punkter: stationære fugle (rastende, fouragerende, trækforsøgende
TF …) for sig og trækkende for sig pr. retning. Stationære og trækkende kædes aldrig sammen – en trækkende fugl er på vej
væk og er ikke den, der raster på stedet.

Stationære punkter hører til samme fund, når
  1. de ligger under 15 km og højst én dag fra hinanden, eller
  2. de er fra samme lokalitet med højst 7 dage siden arten sidst blev set der (en fugl der bliver på stedet, men ikke
     ses hver dag). Et længere hul deler serien i to fund.
Trækkende punkter hører til samme fund, når de har ca. samme retning (højst 45° imellem) og
  1. er fra samme sted samme dag (flere observatører på trækstedet), eller
  2. ligger ud ad trækretningen: samme dag på linjen gennem stedet i retningen, eller næste dag i retningen,
     højst 40 km fra hinanden. Samme sted en anden dag er nye fugle.
Sammenhængen er kædet (A–B og B–C giver ét fund), men et fund må højst strække sig over 30 km (trækkende 100 km), målt som
diagonalen i rektanglet om punkterne – ellers starter et nyt fund. Så smelter et travlt træk- eller rastested ikke
sammen til ét fund over flere uger.

Fugle i et fund = det største antal i et af dets punkter (2 ét sted og 1 et andet sted i kæden er 2 fugle). For
stationære er punktets antal det største antal i én observation; for trækkende den tur, der talte flest i alt.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv, version

LINK_KM = 15.0
LINK_DAYS = 1
SITE_GAP_DAYS = 7
MAX_SPAN_KM = 30.0
MIG_LINK_KM = 40.0
MIG_SPAN_KM = 100.0
SAME_PLACE_KM = 2.0
DIR_TOL = 45.0
CLASSES = ("SU", "SUB")
DIRS = {"N": 0.0, "NNØ": 22.5, "NØ": 45.0, "ØNØ": 67.5, "Ø": 90.0, "ØSØ": 112.5, "SØ": 135.0, "SSØ": 157.5,
        "S": 180.0, "SSV": 202.5, "SV": 225.0, "VSV": 247.5, "V": 270.0, "VNV": 292.5, "NV": 315.0, "NNV": 337.5}
# Adfærd hvor fuglen er trukket videre: sidste led er en trækretning eller T/TU/OF (fx "N", "R SV", "FU T").
# Ender koden på rast/fouragering ("N R", "T FU"), er fuglen blevet på stedet. TF (trækforsøg) er ikke træk.
MIGRATING = (*DIRS, "T", "TU", "OF")


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360


def _diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _arc(angles: list[float]) -> float:
    """Mindste cirkelbue, der rummer alle retningerne."""
    if len(angles) < 2:
        return 0.0
    a = sorted(angles)
    gaps = [b - x for x, b in pairwise(a)] + [a[0] + 360 - a[-1]]
    return 360 - max(gaps)


def migration(adfkode: str | None) -> tuple[bool, float | None]:
    """(trækkende, retning i grader eller None) ud fra adfærdens sidste led uden "HØ" (kun hørt)."""
    parts = [p for p in (adfkode or "").split() if p != "HØ"]
    last = parts[-1] if parts else None
    return last in MIGRATING, DIRS.get(last)


def is_migrating(adfkode: str | None) -> bool:
    return migration(adfkode)[0]


def cluster(points: list[tuple]) -> list[int]:
    """Fund-id for hvert punkt (dato, lat, lon[, loknr, trækkende, retning]). Punkterne skal være sorteret efter dato."""
    points = [(d, lat, lon, *(list(rest) + [None, False, None])[:3]) for d, lat, lon, *rest in points]
    parent = list(range(len(points)))
    box: dict[int, list[float]] = {}   # rod → [min lat, max lat, min lon, max lon]
    dirs: dict[int, list[float]] = {}  # rod → kendte trækretninger

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int, migrating: bool) -> None:
        a, b = find(i), find(j)
        if a == b:
            return
        ba, bb = box[a], box[b]
        nb = [min(ba[0], bb[0]), max(ba[1], bb[1]), min(ba[2], bb[2]), max(ba[3], bb[3])]
        if _km((nb[0], nb[2]), (nb[1], nb[3])) > (MIG_SPAN_KM if migrating else MAX_SPAN_KM):
            return
        nd = dirs[a] + dirs[b]
        if migrating and _arc(nd) > DIR_TOL:
            return
        parent[a] = b
        box[b], dirs[b] = nb, nd
        del box[a], dirs[a]

    def heading(i: int) -> float | None:
        """Punktets retning, ellers fundets (middel af de kendte retninger)."""
        if points[i][5] is not None:
            return points[i][5]
        ds = dirs[find(i)]
        if not ds:
            return None
        x, y = sum(math.sin(math.radians(d)) for d in ds), sum(math.cos(math.radians(d)) for d in ds)
        return math.degrees(math.atan2(x, y)) % 360

    stay_by_day: dict[date, list[int]] = defaultdict(list)
    mig_by_day: dict[date, list[int]] = defaultdict(list)
    last_at_site: dict[int, int] = {}  # loknr → seneste stationære punkt
    for i, (d, lat, lon, *rest) in enumerate(points):
        loknr, migrating, direction = rest
        box[i] = [lat, lat, lon, lon]
        dirs[i] = [direction] if direction is not None else []
        if not migrating:
            for k in range(LINK_DAYS + 1):
                for j in stay_by_day.get(d - timedelta(days=k), ()):
                    if _km((lat, lon), points[j][1:3]) < LINK_KM:
                        union(i, j, False)
            if loknr is not None:
                j = last_at_site.get(loknr)
                if j is not None and (d - points[j][0]).days <= SITE_GAP_DAYS:
                    union(i, j, False)
                last_at_site[loknr] = i
            stay_by_day[d].append(i)
            continue
        for k in (0, 1):
            for j in mig_by_day.get(d - timedelta(days=k), ()):
                km = _km(points[j][1:3], (lat, lon))
                if km < SAME_PLACE_KM:
                    if k == 0:  # samme sted samme dag; samme sted næste dag er nye fugle
                        union(i, j, True)
                    continue
                if km > MIG_LINK_KM:
                    continue
                h = heading(j)
                h = heading(i) if h is None else h
                if h is None:
                    continue
                b = _bearing(points[j][1:3], (lat, lon))
                # Samme dag kendes rækkefølgen ikke: linjen gennem stedet i retningen; næste dag: videre i retningen
                if (min(_diff(b, h), _diff(b, h + 180)) if k == 0 else _diff(b, h)) <= DIR_TOL:
                    union(i, j, True)
        mig_by_day[d].append(i)
    return [find(i) for i in range(len(points))]


async def _species(session: AsyncSession, artnr: int) -> tuple[int, str] | None:
    row = (await session.execute(text("""
        SELECT ls.artnr, ls.klass FROM species s JOIN species ls ON ls.artnr = s.list_artnr WHERE s.artnr = :a
    """), {"a": artnr})).first()
    return (row[0], row[1]) if row and row[1] in CLASSES else None


async def species_finds(session: AsyncSession, artnr: int, fresh: bool = False) -> dict | None:
    """Alle estimerede fund af en SU/SUB-art (None for andre arter). members: 'dato|loknr' → fund-indeks."""
    sp = await _species(session, artnr)
    if sp is None:
        return None
    list_artnr, klass = sp

    async def produce() -> dict:
        obs = (await session.execute(text("""
            SELECT o.dato, o.loknr, o.turid, coalesce(o.adfkode, '') AS adf, coalesce(o.obs_lat, l.lat) AS lat,
                   coalesce(o.obs_lon, l.lon) AS lon, o.antal, o.hemmelig <> 0 AS secret
            FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
            WHERE s.list_artnr = :a AND o.antal > 0 AND o.dato <= current_date AND l.lat IS NOT NULL
        """), {"a": list_artnr})).all()
        # Punkter: dag, lokalitet og adfærd (stationær / trækkende pr. retning, "T" = uden retning)
        acc: dict[tuple, dict] = {}
        for o in obs:
            mig, direction = migration(o.adf)
            k = (o.dato, o.loknr, (direction if direction is not None else "T") if mig else None)
            p = acc.setdefault(k, {"lat": 0.0, "lon": 0.0, "n": 0, "max": 0, "trips": defaultdict(int), "secret": False})
            p["lat"] += o.lat
            p["lon"] += o.lon
            p["n"] += 1
            p["max"] = max(p["max"], o.antal)
            p["trips"][o.turid] += o.antal
            p["secret"] |= bool(o.secret)
        keys = sorted(acc, key=lambda k: (k[0], k[1], k[2] is not None, str(k[2])))
        rows = []
        for k in keys:
            p = acc[k]
            mig = k[2] is not None
            rows.append({"dato": k[0], "loknr": k[1], "mig": mig, "dir": k[2] if mig and k[2] != "T" else None,
                         "lat": p["lat"] / p["n"], "lon": p["lon"] / p["n"], "n": p["n"], "secret": p["secret"],
                         # trækkende: turen der talte flest forbi; stationære: største antal i én observation
                         "antal": max(p["trips"].values()) if mig else p["max"]})
        roots = cluster([(r["dato"], r["lat"], r["lon"], r["loknr"], r["mig"], r["dir"]) for r in rows])
        groups: dict[int, list] = defaultdict(list)
        for r, root in zip(rows, roots):
            groups[root].append(r)
        ordered = sorted(groups.items(), key=lambda kv_: (kv_[1][0]["dato"], kv_[1][0]["loknr"], kv_[1][0]["mig"]))
        names = dict((await session.execute(text("SELECT loknr, navn FROM locations WHERE loknr = ANY(:l)"),
                                            {"l": list({g[0]["loknr"] for g in groups.values()})})).all())
        finds = []
        for _, g in ordered:
            first = g[0]
            # Hemmelige lokaliteter tæller med, men vises ikke
            where = {} if first["secret"] else {"loknr": first["loknr"], "lok": names.get(first["loknr"]),
                                                "lat": round(first["lat"], 4), "lon": round(first["lon"], 4)}
            birds = max(r["antal"] for r in g)
            finds.append({"first": first["dato"].isoformat(), "last": max(r["dato"] for r in g).isoformat(),
                          "n_days": len({r["dato"] for r in g}), "n_obs": sum(r["n"] for r in g), "max_antal": birds,
                          "n_birds": birds, "migrating": first["mig"], "n_sites": len({r["loknr"] for r in g}),
                          "loknr": None, "lok": None, "lat": None, "lon": None, **where})
        per_year: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for f in finds:
            y = int(f["first"][:4])
            per_year[y][0] += 1
            per_year[y][1] += f["n_birds"]
            f["year_no"] = per_year[y][0]
        index = {root: i for i, (root, _) in enumerate(ordered)}
        # En tråd (dag + lokalitet) kan høre til flere fund (stationær og trækkende): den stationære vinder, ellers flest fugle
        members: dict[str, int] = {}
        best: dict[str, tuple] = {}
        for r, root in zip(rows, roots):
            m = f"{r['dato'].isoformat()}|{r['loknr']}"
            rank = (not r["mig"], r["antal"])
            if m not in best or rank > best[m]:
                best[m], members[m] = rank, index[root]
        return {"artnr": list_artnr, "klass": klass, "link_km": LINK_KM, "link_days": LINK_DAYS, "site_gap_days": SITE_GAP_DAYS,
                "span_km": MAX_SPAN_KM, "mig_link_km": MIG_LINK_KM, "total": len(finds), "birds": sum(f["n_birds"] for f in finds),
                "per_year": [{"year": y, "n": n, "birds": b} for y, (n, b) in sorted(per_year.items())],
                "finds": finds, "members": members}

    key = f"finds3:{list_artnr}:{await version('finds')}"
    if fresh:
        await kv().delete(key)
    return await cached(key, 1800, produce)


async def thread_find(session: AsyncSession, day: date, artnr: int, loknr: int) -> dict | None:
    """Fundet som en tråd hører til: nummer i året, varighed og antal fund i året/i alt."""
    data = await species_finds(session, artnr)
    if data is None:
        return None
    key = f"{day.isoformat()}|{loknr}"
    if key not in data["members"]:
        # Nye observationer siden beregningen: regn arten igen
        data = await species_finds(session, artnr, fresh=True)
        if key not in data["members"]:
            return None
    f = data["finds"][data["members"][key]]
    y = int(f["first"][:4])
    py = next(p for p in data["per_year"] if p["year"] == y)
    return {**f, "year": y, "year_total": py["n"], "year_birds": py["birds"], "total": data["total"], "birds": data["birds"],
            "since": data["finds"][0]["first"][:4] if data["finds"] else None,
            "link_km": LINK_KM, "link_days": LINK_DAYS, "site_gap_days": SITE_GAP_DAYS, "span_km": MAX_SPAN_KM}


def public(data: dict | None, limit: int = 400) -> dict | None:
    """Til artssiden: uden medlemsindeks, nyeste fund først."""
    if data is None:
        return None
    return {**{k: v for k, v in data.items() if k not in ("members", "finds")}, "finds": data["finds"][::-1][:limit]}
