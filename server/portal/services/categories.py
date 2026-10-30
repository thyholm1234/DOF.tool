"""Listekategorier: hvilke observationer er krydsbare på årslister.

Kategorierne følger AERC/SU (Den Danske Liste): A (vild), B (kun vild 1800–1949) og C (indført, selvopretholdende) er
krydsbare – som på Netfugl. D (tvivl om oprindelse), E (undslupne/udsatte) og F (kun registreret før 1800) er ikke.
Se services/danish_list.py for listen. Kategorien for en observation findes af
SQL-funktionen bird_category (se models.EXTRA_DDL):
  1. en overstyring for serien (listeart × lokalitet × datointerval), fx et godkendt A-fund af en D-art
  2. artens kategori på datoen (kategorier har en gyldig-fra-dato, så en art kan skifte kategori over tid)
  3. DOFbasens [klammer] om artsnavnet = ikke krydsbar
  4. ellers A
Ændringer slettes aldrig, men markeres som fjernet, så historikken kan ses.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.services.setting_store import put_setting, setting_exists

log = logging.getLogger("portal.categories")

CATEGORIES = {
    "A": "Vild fugl (krydsbar)",
    "B": "Kun vild 1800–1949 (krydsbar)",
    "C": "Indført, selvopretholdende bestand (krydsbar)",
    "D": "Mulig vild – tvivl om oprindelse (ikke krydsbar)",
    "E": "Undsluppet/udsat (ikke krydsbar)",
    "F": "Kun registreret før 1800 (ikke krydsbar)",
}
COUNTABLE = ("A", "B", "C")
SERIES_GAP_DAYS = 7  # som fund-aggregeringen: mere end 7 dage uden obs. på lokaliteten deler serien

# Arter med kategori, overstyring eller [klammer] – kun deres observationer slås op i bird_category.
# CASE sikrer, at funktionen ikke kaldes for de øvrige (langt de fleste) observationer.
_CANDIDATES = """(SELECT artnr FROM species_categories WHERE removed_at IS NULL
    UNION SELECT artnr FROM category_overrides WHERE removed_at IS NULL
    UNION SELECT artnr FROM species WHERE navn LIKE '[%')"""


def countable_sql(o: str = "o", s: str = "s") -> str:
    """WHERE-udtryk: observationen er krydsbar (kræver species-alias s med list_artnr)."""
    return (f"(CASE WHEN {s}.artnr IN {_CANDIDATES} OR {s}.list_artnr IN {_CANDIDATES} "
            f"THEN bird_category({s}.artnr, {s}.list_artnr, {o}.loknr, {o}.dato) IN ('A', 'B', 'C') ELSE TRUE END)")


# Startliste over ikke-krydsbare arter (D og nedefter). Dværgsnegås har godkendte A-fund fra 17.11.2021 –
# de sættes som overstyring på den enkelte serie; alle fund før den dato er ikke krydsbare.
SEED_NOT_COUNTABLE = [
    "Guldfasan", "Alexanderparakit", "Rødhøne", "Munkeparakit", "Kanariefugl", "Brudeand", "Japansk Trane",
    "Moskusand", "Undulat", "Nymfeparakit", "Svanegås", "Stor Alexanderparakit", "Kongefasan", "Snegås", "Påfugl",
    "Marmorand", "Hjelmskallesluger", "Broget Krage", "Hvidhovedet Havørn", "Lammegrib", "Dværgsnegås",
    "Amerikansk Tårnfalk",
]
SEED_KEY = "categories_seed_v1"


async def seed_categories(session: AsyncSession) -> None:
    """Indlæs startlisten én gang (når arterne findes). Rækker der allerede har en kategori røres ikke."""
    if await setting_exists(session, SEED_KEY):
        return
    rows = (await session.execute(text("""
        SELECT artnr, lower(trim(both '[]' from navn)) FROM species WHERE art_type = 'art'
    """))).all()
    if not rows:
        return
    by_name = {n: a for a, n in rows}
    missing = []
    for navn in SEED_NOT_COUNTABLE:
        artnr = by_name.get(navn.lower())
        if artnr is None:
            missing.append(navn)
            continue
        await session.execute(text("""
            INSERT INTO species_categories (artnr, kategori, note)
            SELECT :a, 'D', 'Startliste: ikke krydsbar (D eller lavere)'
            WHERE NOT EXISTS (SELECT 1 FROM species_categories WHERE artnr = :a)
        """), {"a": artnr})
    if missing:
        log.warning("Kategori-startliste: fandt ikke %s", ", ".join(missing))
    await put_setting(session, SEED_KEY, {"missing": missing})


async def species_category(session: AsyncSession, artnr: int, day=None) -> dict:
    """Artens kategori på en dato (standard i dag) og hvor den kommer fra."""
    row = (await session.execute(text("""
        SELECT kategori, valid_from, note FROM species_categories
        WHERE artnr = :a AND removed_at IS NULL AND coalesce(valid_from, '-infinity'::date) <= coalesce(CAST(:d AS date), current_date)
        ORDER BY valid_from DESC NULLS LAST, id DESC LIMIT 1
    """), {"a": artnr, "d": day})).first()
    if row:
        return {"kategori": row[0], "source": "portal", "valid_from": row[1].isoformat() if row[1] else None, "note": row[2]}
    bracket = (await session.execute(text("SELECT navn LIKE '[%' FROM species WHERE artnr = :a"), {"a": artnr})).scalar()
    return {"kategori": "D", "source": "dofbasen"} if bracket else {"kategori": "A", "source": "standard"}


async def series(session: AsyncSession, artnr: int, year: int | None, limit: int = 400) -> list[dict]:
    """Serier af observationer af listearten pr. lokalitet (samme opdeling som fund-aggregeringen), nyeste først."""
    rows = (await session.execute(text(f"""
        WITH d AS (
            SELECT o.loknr, o.dato, count(*) AS n, max(o.antal) AS antal, bool_or(o.hemmelig <> 0) AS secret,
                   array_agg(DISTINCT o.obserkode) FILTER (WHERE o.obserkode IS NOT NULL) AS obs,
                   bird_category(min(o.artnr), :a, o.loknr, o.dato) AS kat
            FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE s.list_artnr = :a AND o.antal > 0 AND o.loknr IS NOT NULL
              AND (CAST(:y AS int) IS NULL OR o.dato BETWEEN make_date(:y, 1, 1) - {SERIES_GAP_DAYS} AND make_date(:y, 12, 31) + {SERIES_GAP_DAYS})
            GROUP BY o.loknr, o.dato
        ), g AS (
            SELECT *, CASE WHEN dato - lag(dato) OVER (PARTITION BY loknr ORDER BY dato) <= {SERIES_GAP_DAYS} THEN 0 ELSE 1 END AS brk FROM d
        ), sr AS (
            SELECT *, sum(brk) OVER (PARTITION BY loknr ORDER BY dato) AS sid FROM g
        )
        SELECT sr.loknr, l.navn AS lok, min(sr.dato) AS first, max(sr.dato) AS last, count(*) AS n_days, sum(sr.n)::int AS n_obs,
               max(sr.antal) AS max_antal, bool_or(sr.secret) AS secret, array_agg(DISTINCT sr.kat) AS kategorier,
               (SELECT array_agg(DISTINCT x) FROM sr s2, unnest(s2.obs) x WHERE s2.loknr = sr.loknr AND s2.sid = sr.sid) AS observers
        FROM sr LEFT JOIN locations l ON l.loknr = sr.loknr
        GROUP BY sr.loknr, l.navn, sr.sid
        HAVING CAST(:y AS int) IS NULL OR extract(year FROM max(sr.dato)) >= :y AND extract(year FROM min(sr.dato)) <= :y
        ORDER BY max(sr.dato) DESC, sr.loknr LIMIT :lim
    """), {"a": artnr, "y": year, "lim": limit})).mappings().all()
    return [{**dict(r), "first": r["first"].isoformat(), "last": r["last"].isoformat(),
             "kategorier": sorted(r["kategorier"]), "observers": sorted(r["observers"] or [])[:12]} for r in rows]
