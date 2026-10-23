"""Fænologi-filteret: arter, der ses uden for deres normale forekomstperiode.

Normal periode pr. listeart (`species_season`):
* **dof** – har DOFbasen ét 'bemærk uden for perioden'-vindue for arten, er den normale periode resten af året.
* **stat** – ellers beregnes den af dagsaggregaterne (`species_day`, seneste 15 år): det korteste stykke af året (også hen over
  nytår), der rummer 99 % af de år-dage arten er set på, plus en margen på 5 dage i hver ende. Arter, der ses hele året,
  og arter med for få data (< 60 år-dage eller < 5 år) får ingen periode og er aldrig fænologi-fund.

Dagen i året tælles på et ikke-skudår (1–365), så 29. februar og senere datoer ikke rykker perioderne.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump

log = logging.getLogger("portal.seasons")

YEARS = 15
COVER = 0.99
MARGIN = 5
MIN_DAYS = 60
MIN_YEARS = 5
YEAR_ROUND = 330  # en periode på så mange dage eller mere regnes som 'hele året'
N = 365


def doy365(d: date) -> int:
    """Dag i året 1–365 uden skudår."""
    n = d.timetuple().tm_yday
    return n - 1 if d.month > 2 and (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else n


def _ddmm_doy(value: str) -> int | None:
    try:
        dd, mm = value.split("-")
        return doy365(date(2025, int(mm), int(dd)))
    except ValueError:
        return None


def shortest_arc(counts: np.ndarray, cover: float = COVER) -> tuple[int, int] | None:
    """Det korteste (cirkulære) stykke, der rummer mindst `cover` af massen. Returnerer (start, længde), 0-baseret."""
    total = counts.sum()
    if total <= 0:
        return None
    need = cover * total
    ext = np.concatenate([counts, counts])
    cum = np.concatenate([[0.0], np.cumsum(ext)])
    best = None
    for start in range(len(counts)):
        # mindste slutposition, så summen fra start når need
        end = int(np.searchsorted(cum, cum[start] + need - 1e-9, side="left"))
        length = end - start
        if end <= len(ext) and (best is None or length < best[1]):
            best = (start, length)
    return best


def stat_window(counts: np.ndarray) -> tuple[int, int] | None:
    """(start_doy, end_doy) 1–365 for den normale periode, eller None når arten ses hele året."""
    arc = shortest_arc(counts)
    if arc is None:
        return None
    start, length = arc
    if length + 2 * MARGIN >= YEAR_ROUND:
        return None
    s = (start - MARGIN) % N
    e = (start + length - 1 + MARGIN) % N
    return s + 1, e + 1


def is_outside(start: int, end: int, doy: int) -> bool:
    return not (start <= doy <= end) if start <= end else end < doy < start


async def refresh(session: AsyncSession) -> int:
    rows = (await session.execute(text(f"""
        SELECT coalesce(s.list_artnr, s.artnr) AS a,
               extract(doy FROM sd.dato)::int - CASE WHEN extract(month FROM sd.dato) > 2
                   AND extract(year FROM sd.dato)::int % 4 = 0
                   AND (extract(year FROM sd.dato)::int % 100 <> 0 OR extract(year FROM sd.dato)::int % 400 = 0) THEN 1 ELSE 0 END AS d,
               count(DISTINCT sd.dato) AS n, count(DISTINCT extract(year FROM sd.dato)) AS y
        FROM species_day sd JOIN species s ON s.artnr = sd.artnr
        WHERE s.art_type IN ('art', 'underart') AND sd.dato >= current_date - interval '{YEARS} years'
        GROUP BY 1, 2
    """))).all()
    counts: dict[int, np.ndarray] = {}
    for a, d, n, _ in rows:
        counts.setdefault(a, np.zeros(N))[min(max(d, 1), N) - 1] += n
    years = {r[0]: r[1] for r in (await session.execute(text(f"""
        SELECT coalesce(s.list_artnr, s.artnr), count(DISTINCT extract(year FROM sd.dato))
        FROM species_day sd JOIN species s ON s.artnr = sd.artnr
        WHERE s.art_type IN ('art', 'underart') AND sd.dato >= current_date - interval '{YEARS} years' GROUP BY 1"""))).all()}

    # DOFbasens egne vinduer: præcis ét vindue pr. art → resten af året er den normale periode
    dof: dict[int, tuple[int, int]] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}
    for navn, fra, til in (await session.execute(text("SELECT artnavn, fra, til FROM phenology_windows"))).all():
        by_name.setdefault(navn, []).append((fra, til))
    for artnr, navn in (await session.execute(text("SELECT artnr, navn FROM species WHERE art_type = 'art'"))).all():
        ws = by_name.get(navn)
        if ws and len(ws) == 1:
            f, t = _ddmm_doy(ws[0][0]), _ddmm_doy(ws[0][1])
            if f and t:
                dof[artnr] = (t % N + 1, (f - 2) % N + 1)

    out = []
    for a, c in counts.items():
        if a in dof:
            out.append((a, *dof[a], "dof", int(c.sum()), years.get(a, 0)))
            continue
        if c.sum() < MIN_DAYS or years.get(a, 0) < MIN_YEARS:
            continue
        w = stat_window(c)
        if w:
            out.append((a, *w, "stat", int(c.sum()), years.get(a, 0)))
    for a, w in dof.items():
        if a not in counts:
            out.append((a, *w, "dof", 0, 0))
    await session.execute(text("DELETE FROM species_season"))
    for a, s, e, src, n, y in out:
        await session.execute(text("""
            INSERT INTO species_season (artnr, start_doy, end_doy, source, n_days, n_years)
            VALUES (:a, :s, :e, :src, :n, :y)
        """), {"a": a, "s": s, "e": e, "src": src, "n": n, "y": y})
    await session.commit()
    await bump("feed")
    log.info("Normale perioder beregnet for %d arter", len(out))
    return len(out)
