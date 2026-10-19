"""Data fra DOF's andre tjenester til artssiden: Atlas III (yngleudbredelse) og punkttællinger.

- Atlas: dofbasen.dk/atlas/REST (udbredelse pr. 5×5 km-kvadrat i Atlas I 1971–74, II 1993–96 og III 2014–17)
  og kvadraternes geometri fra DOF's WFS. Atlas bruger sit eget art-id, som slås op via artslisten.
- Punkttællinger: Danmarksfugle-backend (bestandsindeks sommer og vinter, 100 = første år).

Endpoints kræver femcifret artnr med foranstillede nuller (02870). Data ændres sjældent og caches længe.
"""

from __future__ import annotations

import logging
import math

import httpx

from portal.cache import cached

log = logging.getLogger("portal.dofext")

UA = {"User-Agent": "DOF.tool/1.0 (feltornitolog-portal; artsside med atlas og punkttaellinger)"}
ATLAS = "https://dofbasen.dk/atlas/REST"
WFS = "https://dofbasen.dk/geoserverwrapper/index.php/wfs"
DANMARKSFUGLE = "https://service.dofbasen.dk/DanmarksFugleBackend/api"
WEEK = 7 * 86400
MONTH = 30 * 86400
ATLAS_NAMES = {1: "Atlas I (1971–74)", 2: "Atlas II (1993–96)", 3: "Atlas III (2014–17)"}


def artnr5(artnr: int) -> str:
    return f"{artnr:05d}"


async def _json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------- atlas

async def _atlas_ids() -> dict[str, str]:
    async def produce():
        return {a["artnr"]: a["id"] for a in await _json(f"{ATLAS}/arter")}
    return await cached("ext:atlas:ids", MONTH, produce)


async def atlas_squares() -> dict:
    """Alle atlaskvadrater som {kode: [navn, [[lat, lon] × 4]]} (hjørner, uden lukkepunkt)."""
    async def produce():
        out = {}
        for zone in (32, 33):
            data = await _json(WFS, {"service": "WFS", "version": "1.0.0", "request": "GetFeature",
                                     "outputformat": "application/json", "maxfeatures": "5000",
                                     "typeName": f"dofbasen:atlas_kvadrater_utm{zone}", "srsName": "EPSG:4326"})
            for f in data["features"]:
                ring = f["geometry"]["coordinates"][0][:4]
                out[f["properties"]["kvadratkode"].upper()] = [f["properties"].get("stednavn") or "",
                                                               [[round(y, 4), round(x, 4)] for x, y in ring]]
        return out
    return await cached("ext:atlas:squares", MONTH, produce)


async def atlas(artnr: int) -> dict | None:
    """Udbredelse i de tre atlasperioder: {periode: {kvadrat: sikker|sandsynlig|mulig}} + antal kvadrater."""
    key = artnr5(artnr)

    async def produce():
        aid = (await _atlas_ids()).get(key)
        if not aid:
            return {"available": False}
        periods = {}
        for p in (1, 2, 3):
            rows = await _json(f"{ATLAS}/udbredelse/for/art/{aid}/i/atlas/{p}")
            periods[p] = {r["kvadratkode"].upper(): r["sandsynlighed"] for r in rows}
        counts = {p: {k: sum(1 for v in sq.values() if v == k) for k in ("sikker", "sandsynlig", "mulig")}
                  for p, sq in periods.items()}
        s2, s3 = set(periods[2]), set(periods[3])
        return {"available": any(periods.values()), "artnr": key, "periods": periods, "counts": counts,
                "names": ATLAS_NAMES,
                "change": {"kun_2": len(s2 - s3), "kun_3": len(s3 - s2), "begge": len(s2 & s3)}}

    try:
        return await cached(f"ext:atlas:{key}", MONTH, produce)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Atlas for %s fejlede: %s", key, exc)
        return {"available": False, "error": True}


# ---------------------------------------------------------------- punkttællinger

def _series(block: dict) -> list[dict]:
    """Indeks ligger i halvårspar fra firstYear: [vinter år, sommer år, vinter år+1, ...]; 0 = ingen værdi."""
    summer = block["season"].lower().startswith("sommer")
    vals = block["indices"][1::2] if summer else block["indices"][0::2]
    return [{"year": block["firstYear"] + i, "index": v} for i, v in enumerate(vals) if v and v > 0]


def _trend(points: list[dict]) -> dict | None:
    """Log-lineær regression: gennemsnitlig ændring i % pr. år og samlet ændring over perioden."""
    if len(points) < 5:
        return None
    xs = [p["year"] for p in points]
    ys = [math.log(p["index"]) for p in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = [y - (my + b * (x - mx)) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in resid) / max(n - 2, 1) / sxx)
    t = b / se if se else 0.0
    return {"from": xs[0], "to": xs[-1], "n_years": n, "pct_per_year": round((math.exp(b) - 1) * 100, 1),
            "pct_total": round((math.exp(b * (xs[-1] - xs[0])) - 1) * 100), "significant": abs(t) > 2.0,
            "direction": "stabil" if abs(t) <= 2.0 else "fremgang" if b > 0 else "tilbagegang"}


def _season(block: dict) -> dict:
    pts = _series(block)
    last = pts[-1] if pts else None
    best = max(pts, key=lambda p: p["index"]) if pts else None
    low = min(pts, key=lambda p: p["index"]) if pts else None
    recent = [p for p in pts if last and p["year"] > last["year"] - 12]
    return {"season": block["season"], "summer": block["season"].lower().startswith("sommer"), "points": pts,
            "last": last, "max": best, "min": low, "trend_all": _trend(pts), "trend_12": _trend(recent)}


async def point_counts(artnr: int) -> dict | None:
    key = artnr5(artnr)

    async def produce():
        try:
            d = await _json(f"{DANMARKSFUGLE}/art", {"artnr": key})
        except httpx.HTTPStatusError:
            return {"available": False}  # arten findes ikke i Danmarksfugle (caches, så vi ikke spørger igen)
        facts = {k: {"title": v.get("title"), "text": ", ".join(v.get("facts") or [])}
                 for k, v in (d.get("facts") or {}).items()
                 if k in ("udvikling_shortTime", "udvikling_longTime", "ynglebestand") and v.get("facts")}
        red = [{"title": r.get("title"), "classification": r.get("classification"), "description": r.get("description")}
               for r in d.get("redLists") or []]
        return {"available": bool(d.get("punktIndeksData")), "artnr": key,
                "seasons": [_season(b) for b in d.get("punktIndeksData") or []],
                "facts": facts, "redlists": red, "source": f"https://dofbasen.dk/danmarksfugle/art/{key}"}

    try:
        return await cached(f"ext:ptt:{key}", WEEK, produce)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Punkttællinger for %s fejlede: %s", key, exc)
        return {"available": False, "error": True}
