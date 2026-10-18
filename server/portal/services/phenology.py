"""Fænologi: rykker fuglene tidligere eller senere end normalt?

Metode (forklaret i UI'et):
- Indsatskorrigeret rapporteringsrate pr. dag: RR = ture med arten / alle ture den dag.
- Glattet med 7-dages løbende middel. En helårs-baseline (10 %-fraktil) trækkes fra, så
  standfugle/vintergæster med konstant forekomst ikke dominerer sæsonkurven.
- For hver sæson (forår 1/1–30/6, efterår 1/7–31/12) findes dagene hvor 10 %, 50 % og 90 % af
  sæsonens 'masse' er passeret (q10/q50/q90) samt første fund.
- Trend: Theil–Sen-hældning af q50 mod år (dage pr. 10 år) med Kendalls τ-test.
- I år: forsinkelse/fremrykning skønnes ved at forskyde den normale kurve ±30 dage og finde den
  forskydning der passer bedst til årets kurve indtil nu (mindste kvadraters metode med frit niveau).
- Klima: sammenhæng mellem forårstemperatur (DMI, landsgennemsnit feb–apr) og forårets q10.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import get_json, kv, set_json
from portal.timeutil import dk_today

log = logging.getLogger("portal.pheno")

SEASONS = {"spring": (1, 181), "autumn": (182, 366)}
SEASON_LABEL = {"spring": "Forår", "autumn": "Efterår"}
MIN_YEARS_TREND = 6


# ---------------------------------------------------------------- aggregater


async def refresh_aggregates(session: AsyncSession, since: date) -> None:
    await session.execute(text("DELETE FROM species_day WHERE dato >= :d"), {"d": since})
    await session.execute(text("""
        INSERT INTO species_day (artnr, dato, n_obs, n_ind, n_trips, n_migr)
        SELECT artnr, dato, count(*), sum(antal), count(DISTINCT turid),
               coalesce(sum(antal) FILTER (WHERE split_part(coalesce(adfkode,''), ' ', 1)
                    IN ('T','TF','N','NØ','Ø','SØ','S','SV','V','NV')), 0)
        FROM observations WHERE dato >= :d AND dato <= current_date GROUP BY artnr, dato
    """), {"d": since})
    await session.execute(text("DELETE FROM effort_day WHERE dato >= :d"), {"d": since})
    await session.execute(text("""
        INSERT INTO effort_day (dato, n_trips, n_observers)
        SELECT dato, count(DISTINCT turid), count(DISTINCT obserkode)
        FROM observations WHERE dato >= :d AND dato <= current_date GROUP BY dato
    """), {"d": since})
    await session.commit()


async def import_climate(session: AsyncSession, rows: list[tuple[int, int, float]]) -> None:
    for y, m, t in rows:
        await session.execute(text("""
            INSERT INTO climate_month (year, month, mean_temp) VALUES (:y, :m, :t)
            ON CONFLICT (year, month) DO UPDATE SET mean_temp = EXCLUDED.mean_temp
        """), {"y": y, "m": m, "t": t})
    await session.commit()


# ---------------------------------------------------------------- matematik


def smooth(x: np.ndarray, w: int = 7) -> np.ndarray:
    if len(x) < w:
        return x
    kernel = np.ones(w) / w
    padded = np.pad(x, (w // 2, w // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def quantile_doys(mass: np.ndarray, start_doy: int) -> tuple[float, float, float] | None:
    total = mass.sum()
    if total <= 0:
        return None
    cum = np.cumsum(mass) / total
    out = []
    for q in (0.1, 0.5, 0.9):
        idx = int(np.searchsorted(cum, q))
        idx = min(idx, len(cum) - 1)
        prev = cum[idx - 1] if idx > 0 else 0.0
        frac = (q - prev) / (cum[idx] - prev) if cum[idx] > prev else 0.0
        out.append(start_doy + idx - 1 + frac + 0.5)
    return out[0], out[1], out[2]


def theil_sen(x: np.ndarray, y: np.ndarray) -> dict | None:
    n = len(x)
    if n < MIN_YEARS_TREND:
        return None
    slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(n) for j in range(i + 1, n) if x[j] != x[i]]
    if not slopes:
        return None
    slope = float(np.median(slopes))
    intercept = float(np.median(y - slope * x))
    # Kendalls tau med normaltilnærmelse
    s = sum(np.sign(x[j] - x[i]) * np.sign(y[j] - y[i]) for i in range(n) for j in range(i + 1, n))
    var = n * (n - 1) * (2 * n + 5) / 18
    z = (s - np.sign(s)) / math.sqrt(var) if s != 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    tau = s / (n * (n - 1) / 2)
    return {"slope_per_decade": round(slope * 10, 2), "intercept": intercept, "tau": round(float(tau), 3),
            "p": round(p, 4), "n_years": n}


def best_lag(current: np.ndarray, baseline: np.ndarray, start: int, end: int, max_lag: int = 30) -> dict | None:
    """Forskydning (dage) af den normale kurve der passer bedst til årets kurve i [start, end).

    Positiv lag = året er SENERE end normalt.
    """
    y = current[start:end]
    if len(y) < 20 or y.sum() <= 0:
        return None
    best = None
    for lag in range(-max_lag, max_lag + 1):
        lo, hi = start - lag, end - lag
        if lo < 0 or hi > len(baseline):
            continue
        b = baseline[lo:hi]
        bb = float(b @ b)
        if bb <= 0:
            continue
        alpha = float(b @ y) / bb
        if alpha <= 0:
            continue
        sse = float(((y - alpha * b) ** 2).sum())
        if best is None or sse < best[1]:
            best = (lag, sse, alpha)
    if best is None:
        return None
    lag, sse, alpha = best
    sst = float(((y - y.mean()) ** 2).sum()) or 1e-12
    return {"lag_days": lag, "fit_r2": round(1 - sse / sst, 3), "level": round(alpha, 2)}


# ---------------------------------------------------------------- data


async def _effort(session: AsyncSession) -> dict[date, int]:
    """Indsats pr. dag – kun for år med data i mindst 11 måneder (arkivet har kun maj/juni før 2010)."""
    rows = (await session.execute(text("""
        SELECT dato, n_trips FROM effort_day WHERE extract(year FROM dato) IN (
            SELECT extract(year FROM dato) FROM effort_day GROUP BY 1
            HAVING count(DISTINCT extract(month FROM dato)) >= 11 OR extract(year FROM max(dato)) = extract(year FROM now()))
    """))).all()
    return {d: n for d, n in rows}


async def species_rr(session: AsyncSession, artnr: int, effort: dict[date, int] | None = None) -> dict[int, np.ndarray]:
    """Årsvis daglig rapporteringsrate (366 dage) for arten (inkl. underarter)."""
    effort = effort or await _effort(session)
    ids = [r[0] for r in (await session.execute(
        text("SELECT artnr FROM species WHERE artnr = :a OR list_artnr = :a"), {"a": artnr})).all()] or [artnr]
    rows = (await session.execute(text("""
        SELECT dato, sum(n_trips) FROM species_day WHERE artnr = ANY(:ids) GROUP BY dato
    """), {"ids": ids})).all()
    years: dict[int, np.ndarray] = {}
    for d, n in rows:
        e = effort.get(d)
        if not e:
            continue
        arr = years.setdefault(d.year, np.zeros(366))
        arr[d.timetuple().tm_yday - 1] = n / e
    return years


def edge_doys(seg: np.ndarray, start_doy: int, rising: bool) -> tuple[float, float, float] | None:
    """Dag hvor kurven første gang (stigende flanke) / sidste gang (faldende) er ≥ 10/50/90 % af toppen."""
    peak = seg.max()
    if peak <= 0:
        return None
    out = []
    for f in (0.1, 0.5, 0.9):
        idx = np.nonzero(seg >= f * peak)[0]
        if not len(idx):
            return None
        out.append(start_doy + (idx[0] if rising else idx[-1]))
    if not rising:
        out.reverse()  # q10 < q50 < q90 i kalenderrækkefølge
    return float(out[0]), float(out[1]), float(out[2])


def profile(baseline: np.ndarray) -> str:
    """sommergæst / vintergæst / gennemtrækker ud fra normalkurvens form."""
    summer = float(baseline[159:200].mean())
    winter = float(np.concatenate([baseline[:40], baseline[334:]]).mean())
    passage = float(max(baseline[59:160].max(), baseline[212:320].max()))
    if summer >= 0.4 * passage and summer > 2 * winter:
        return "sommer"
    if winter >= 0.4 * passage and winter > 2 * summer:
        return "vinter"
    return "passage"


# Hvilken begivenhed måles i hver sæson for hver profil: (navn, metode)
EVENTS = {
    "sommer": {"spring": ("ankomst", "rising"), "autumn": ("afrejse", "falling")},
    "vinter": {"spring": ("afrejse", "falling"), "autumn": ("ankomst", "rising")},
    "passage": {"spring": ("træktop", "mass"), "autumn": ("træktop", "mass")},
}


def year_metrics(rr: np.ndarray, year: int, upto_doy: int | None = None, prof: str = "passage") -> dict[str, dict]:
    s = smooth(rr)
    floor = float(np.quantile(s, 0.10))
    out = {}
    for season, (a, b) in SEASONS.items():
        if upto_doy is not None and upto_doy < b:
            continue
        seg = np.clip(s[a - 1:b] - floor, 0, None)
        method = EVENTS[prof][season][1]
        q = quantile_doys(seg, a) if method == "mass" else edge_doys(seg, a, method == "rising")
        raw = rr[a - 1:b]
        nz = np.nonzero(raw)[0]
        if q is None or len(nz) < 5:
            continue
        out[season] = {"q10": q[0], "q50": q[1], "q90": q[2], "first_doy": int(a + nz[0]), "mass": float(seg.sum()),
                       "n_days": int(len(nz))}
    return out


def classify(baseline: np.ndarray) -> str:
    peak = float(baseline.max())
    if peak <= 0:
        return "ukendt"
    low = float(np.quantile(baseline, 0.1))
    ratio = low / peak
    if ratio < 0.05:
        return "trækfugl"
    if ratio < 0.35:
        return "delvis trækfugl"
    return "standfugl"


async def recompute_metrics(session: AsyncSession) -> int:
    """Natlig genberegning af nøgledatoer for alle arter + oversigt i Valkey."""
    effort = await _effort(session)
    if not effort:
        return 0
    today = dk_today()
    species = (await session.execute(text("""
        SELECT s.artnr, s.navn FROM species s
        WHERE s.art_type = 'art' AND s.list_artnr = s.artnr AND NOT s.excluded
          AND EXISTS (SELECT 1 FROM species_day sd WHERE sd.artnr = s.artnr)
    """))).all()
    climate = await spring_temps(session)
    overview = []
    weekly: dict[int, dict] = {}
    await session.execute(text("DELETE FROM pheno_metrics"))
    for artnr, navn in species:
        years = await species_rr(session, artnr, effort)
        complete = {y: v for y, v in years.items() if y < today.year}
        if len(complete) < 3:
            continue
        base, _ = _baseline(years, today.year)
        prof = profile(base)
        wk = [float(base[i * 7:(i + 1) * 7].mean()) for i in range(52)]
        if max(wk) >= 0.01:
            weekly[artnr] = {"navn": navn, "rr": [round(v, 4) for v in wk]}
        rows = []
        for y, rr in years.items():
            upto = today.timetuple().tm_yday if y == today.year else None
            for season, m in year_metrics(rr, y, upto, prof).items():
                rows.append({"a": artnr, "y": y, "s": season, **m})
        for r in rows:
            await session.execute(text("""
                INSERT INTO pheno_metrics (artnr, year, season, q10, q50, q90, first_doy, mass, n_days)
                VALUES (:a, :y, :s, :q10, :q50, :q90, :first_doy, :mass, :n_days)
            """), r)
        summary = summarize(navn, artnr, rows, years, today, climate, prof)
        if summary:
            overview.append(summary)
    await session.commit()
    await set_json("pheno:overview", {"generated": today.isoformat(), "species": overview})
    await set_json("pheno:weekly", weekly)
    r = kv()
    for pattern in ("pheno:sp:*", "pheno:arr:*"):
        async for key in r.scan_iter(pattern):
            await r.delete(key)
    log.info("Fænologi genberegnet for %s arter", len(overview))
    return len(overview)


def _baseline(years: dict[int, np.ndarray], current_year: int, n: int = 10) -> tuple[np.ndarray, list[int]]:
    base_years = sorted(y for y in years if y < current_year)[-n:]
    if not base_years:
        return np.zeros(366), []
    stack = np.vstack([smooth(years[y]) for y in base_years])
    return stack.mean(axis=0), base_years


def summarize(navn: str, artnr: int, rows: list[dict], years: dict[int, np.ndarray], today: date,
              climate: dict[int, float], prof: str = "passage") -> dict | None:
    base, base_years = _baseline(years, today.year)
    if not base_years:
        return None
    kind = classify(base)
    out: dict = {"artnr": artnr, "navn": navn, "type": kind, "profile": prof, "seasons": {}}
    for season, (a, b) in SEASONS.items():
        hist = [r for r in rows if r["s"] == season and r["y"] < today.year]
        if len(hist) < 3:
            continue
        # Kun meningsfuldt hvis sæsonen har en tydelig top (ikke flad standfuglekurve)
        seg = base[a - 1:b]
        # Mindst 0,4 % af alle ture i toppen – ellers er kurven for tynd til at sige noget om timing
        if seg.max() < 0.004 or (seg.max() - np.quantile(base, 0.1)) / seg.max() < 0.3:
            continue
        xs = np.array([r["y"] for r in hist], dtype=float)
        trend50 = theil_sen(xs, np.array([r["q50"] for r in hist]))
        trend10 = theil_sen(xs, np.array([r["q10"] for r in hist]))
        normal = {k: round(float(np.median([r[k] for r in hist])), 1) for k in ("q10", "q50", "q90", "first_doy")}
        info = {"normal": normal, "trend_q50": trend50, "trend_q10": trend10, "n_years": len(hist),
                "event": EVENTS[prof][season][0]}
        this = next((r for r in rows if r["s"] == season and r["y"] == today.year), None)
        if this:
            info["this_year"] = {k: round(this[k], 1) for k in ("q10", "q50", "q90", "first_doy")}
        doy = today.timetuple().tm_yday
        if a <= doy <= b and today.year in years:
            # Sæsonen er i gang: sammenlign kurverne indtil i dag
            cur = smooth(years[today.year])
            cum_base = np.cumsum(np.clip(seg - np.quantile(base, 0.1), 0, None))
            frac = float(cum_base[min(doy - a, len(cum_base) - 1)] / cum_base[-1]) if cum_base[-1] > 0 else 0
            if frac >= 0.15:
                lag = best_lag(cur, base, a - 1, doy - 1)
                # Forskydning ved søgegrænsen (±30) er et tegn på dårlig tilpasning, ikke et resultat
                if lag and lag["fit_r2"] >= 0.5 and abs(lag["lag_days"]) < 30:
                    info["status"] = {**lag, "season_progress": round(frac, 2)}
        if season == "spring" and climate:
            pairs = [(climate[r["y"]], r["q10"]) for r in hist if r["y"] in climate]
            if len(pairs) >= 6:
                t, q = np.array(pairs).T
                if t.std() > 0:
                    slope, intercept = np.polyfit(t, q, 1)
                    r = float(np.corrcoef(t, q)[0, 1])
                    info["climate"] = {"days_per_degree": round(float(slope), 2), "r": round(r, 2), "n": len(pairs)}
        out["seasons"][season] = info
    return out if out["seasons"] else None


async def spring_temps(session: AsyncSession) -> dict[int, float]:
    rows = (await session.execute(text("""
        SELECT year, avg(mean_temp) FROM climate_month WHERE month IN (2, 3, 4) GROUP BY year HAVING count(*) = 3
    """))).all()
    return {int(y): float(t) for y, t in rows}


async def species_detail(session: AsyncSession, artnr: int) -> dict:
    today = dk_today()
    years = await species_rr(session, artnr)
    if not years:
        return {"artnr": artnr, "years": []}
    base, base_years = _baseline(years, today.year)
    stack = np.vstack([smooth(years[y]) for y in base_years]) if base_years else np.zeros((1, 366))
    cur = smooth(years.get(today.year, np.zeros(366)))
    doy = today.timetuple().tm_yday
    metrics = (await session.execute(text("""
        SELECT year, season, q10, q50, q90, first_doy, mass FROM pheno_metrics WHERE artnr = :a ORDER BY year
    """), {"a": artnr})).mappings().all()
    overview = await get_json("pheno:overview") or {}
    summary = next((s for s in overview.get("species", []) if s["artnr"] == artnr), None)
    weekly = lambda arr: [round(float(arr[i:i + 7].mean()) * 100, 3) for i in range(0, 364, 7)]  # noqa: E731
    return {
        "artnr": artnr,
        "baseline_years": base_years,
        "weeks": {
            "mean": weekly(base),
            "p10": weekly(np.quantile(stack, 0.1, axis=0)),
            "p90": weekly(np.quantile(stack, 0.9, axis=0)),
            "current": weekly(cur)[: max(1, doy // 7)],
            "last_year": weekly(smooth(years[today.year - 1])) if today.year - 1 in years else None,
        },
        "metrics": [dict(m) for m in metrics],
        "summary": summary,
        "type": classify(base),
    }


async def arrivals(session: AsyncSession, year: int) -> list[dict]:
    """Årets første fund af trækfugle sammenlignet med de foregående 10 års median."""
    rows = (await session.execute(text("""
        WITH firsts AS (
            SELECT coalesce(s.list_artnr, s.artnr) AS artnr, extract(year FROM sd.dato)::int AS y,
                   min(extract(doy FROM sd.dato))::int AS doy
            FROM species_day sd JOIN species s ON s.artnr = sd.artnr
            WHERE extract(year FROM sd.dato) BETWEEN :y0 AND :y AND extract(doy FROM sd.dato) BETWEEN 32 AND 181
            GROUP BY 1, 2
        )
        SELECT f.artnr, sp.navn, f.doy AS this_doy,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY f2.doy) FROM firsts f2 WHERE f2.artnr = f.artnr AND f2.y < :y) AS normal_doy,
               (SELECT count(*) FROM firsts f2 WHERE f2.artnr = f.artnr AND f2.y < :y) AS n_years
        FROM firsts f JOIN species sp ON sp.artnr = f.artnr
        WHERE f.y = :y AND sp.art_type = 'art' AND sp.list_artnr = sp.artnr
    """), {"y": year, "y0": year - 10})).mappings().all()
    overview = await get_json("pheno:overview") or {}
    # Kun sommergæster: for dem er første forårsfund en meningsfuld ankomst (overvintrere er fjernet ved at starte 1/2)
    migrants = {s["artnr"] for s in overview.get("species", []) if s.get("profile") == "sommer"}
    out = []
    for r in rows:
        if r["normal_doy"] is None or r["n_years"] < 5 or (migrants and r["artnr"] not in migrants):
            continue
        normal = float(r["normal_doy"])
        if normal < 40:
            continue
        out.append({"artnr": r["artnr"], "navn": r["navn"],
                    "dato": (date(year, 1, 1) + timedelta(days=r["this_doy"] - 1)).isoformat(),
                    "normal": (date(year, 1, 1) + timedelta(days=round(normal) - 1)).isoformat(),
                    "diff_days": round(r["this_doy"] - normal), "n_years": r["n_years"]})
    out.sort(key=lambda r: r["dato"])
    return out
