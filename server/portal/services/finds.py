"""Estimeret antal fund af SU- og SUB-arter.

Observationer af samme art hører til samme fund, når de ligger under 15 km fra hinanden og højst én dag
fra hinanden – så en fugl der bliver på stedet i flere dage, eller ses af flere på samme dag, tæller én
gang. Sammenhængen er kædet: A–B og B–C giver ét fund, selvom A og C ligger længere fra hinanden.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv, version

LINK_KM = 15.0
LINK_DAYS = 1
CLASSES = ("SU", "SUB")


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def cluster(points: list[tuple[date, float, float]]) -> list[int]:
    """Fund-id for hvert punkt (dato, lat, lon). Punkterne skal være sorteret efter dato."""
    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_day: dict[date, list[int]] = defaultdict(list)
    for i, (d, lat, lon) in enumerate(points):
        for k in range(LINK_DAYS + 1):
            for j in by_day.get(d - timedelta(days=k), ()):
                if find(i) != find(j) and _km((lat, lon), points[j][1:]) < LINK_KM:
                    parent[find(i)] = find(j)
        by_day[d].append(i)
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
        rows = (await session.execute(text("""
            SELECT o.dato, o.loknr, avg(coalesce(o.obs_lat, l.lat)) AS lat, avg(coalesce(o.obs_lon, l.lon)) AS lon,
                   max(o.antal) AS antal, count(*) AS n, bool_or(o.hemmelig <> 0) AS secret
            FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
            WHERE s.list_artnr = :a AND o.antal > 0 AND o.dato <= current_date AND l.lat IS NOT NULL
            GROUP BY o.dato, o.loknr ORDER BY o.dato, o.loknr
        """), {"a": list_artnr})).all()
        roots = cluster([(r.dato, r.lat, r.lon) for r in rows])
        groups: dict[int, list] = defaultdict(list)
        for r, root in zip(rows, roots):
            groups[root].append(r)
        ordered = sorted(groups.items(), key=lambda kv_: (kv_[1][0].dato, kv_[1][0].loknr))
        names = dict((await session.execute(text("SELECT loknr, navn FROM locations WHERE loknr = ANY(:l)"),
                                            {"l": list({g[0].loknr for g in groups.values()})})).all())
        finds = []
        for _, g in ordered:
            first = g[0]
            # Hemmelige lokaliteter tæller med, men vises ikke
            where = {} if first.secret else {"loknr": first.loknr, "lok": names.get(first.loknr),
                                             "lat": round(first.lat, 4), "lon": round(first.lon, 4)}
            finds.append({"first": first.dato.isoformat(), "last": max(r.dato for r in g).isoformat(),
                          "n_days": len({r.dato for r in g}), "n_obs": sum(r.n for r in g), "max_antal": max(r.antal for r in g),
                          "n_sites": len({r.loknr for r in g}), "loknr": None, "lok": None, "lat": None, "lon": None, **where})
        per_year: dict[int, int] = defaultdict(int)
        for f in finds:
            y = int(f["first"][:4])
            per_year[y] += 1
            f["year_no"] = per_year[y]
        index = {root: i for i, (root, _) in enumerate(ordered)}
        members = {f"{r.dato.isoformat()}|{r.loknr}": index[root] for r, root in zip(rows, roots)}
        return {"artnr": list_artnr, "klass": klass, "link_km": LINK_KM, "link_days": LINK_DAYS, "total": len(finds),
                "per_year": [{"year": y, "n": n} for y, n in sorted(per_year.items())], "finds": finds, "members": members}

    key = f"finds:{list_artnr}:{await version('finds')}"
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
    return {**f, "year": y, "year_total": next(p["n"] for p in data["per_year"] if p["year"] == y),
            "total": data["total"], "since": data["finds"][0]["first"][:4] if data["finds"] else None,
            "link_km": LINK_KM, "link_days": LINK_DAYS}


def public(data: dict | None, limit: int = 400) -> dict | None:
    """Til artssiden: uden medlemsindeks, nyeste fund først."""
    if data is None:
        return None
    return {**{k: v for k, v in data.items() if k not in ("members", "finds")}, "finds": data["finds"][::-1][:limit]}
