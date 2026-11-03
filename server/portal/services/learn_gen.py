"""Læring: generatorer – aktiviteter, der får et nyt, konkret spørgsmål ved hver visning (docs/laering-spec.md §5.2).

Datadrevne spørgsmål (``obs_odds``, ``obs_habitat``) bygges af DOFbasens observationer, men kun for en enkelt lokalitet ad gangen
(``loknr``-indekseret) og aldrig med hemmelige observationer (``hemmelig = 0``). De andre generatorer genbruger aggregaterne og
artsquizzens/lydquizzens bygning. Facit forlader aldrig serveren: en generator returnerer en dict med ``correct`` og ``explanation``,
og spilleaget (``learn_play``) lægger facit i Valkey og sender kun spørgsmålet til klienten.
"""

from __future__ import annotations

import json
import random
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import get_json, kv
from portal.db import ObsSession
from portal.services import species_quiz

MIN_TRIPS_LOC = 30  # en lokalitet bruges kun til spørgsmål, når den har mindst så mange ture i perioden
YEARS = 12
MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"]

GENERATORS = {"foto_art", "foto_sammenlign", "lyd_art", "obs_odds", "obs_saeson", "obs_habitat", "obs_sjaelden", "obs_status"}
NEEDS_PHOTOS = {"foto_art", "foto_sammenlign"}
NEEDS_SOUND = {"lyd_art"}
NEEDS_DATA = {"obs_odds", "obs_saeson", "obs_habitat", "obs_sjaelden", "obs_status"}

# doy-afstand med omløb over nytår (366 dage)
_DOY = "abs(((extract(doy from {c})::int - :d + 183) % 366 + 366) % 366 - 183) <= :w"


class GenError(Exception):
    pass


def week_index(d: date | None = None) -> int:
    return min(51, (((d or date.today()).timetuple().tm_yday) - 1) // 7)


def week_label(week: int) -> str:
    d = date(2025, 1, 1).toordinal() + week * 7
    dd = date.fromordinal(d)
    return f"uge {week + 1} (omkring {dd.day}. {MONTHS[dd.month - 1]})"


# ---------------------------------------------------------------- lokaliteter

async def popular_locations() -> list[dict]:
    """Lokaliteter med mange observerede arter de seneste år (cachet 6 timer)."""
    cached = await get_json("lr:locs")
    if cached:
        return cached
    async with ObsSession() as s:
        rows = (await s.execute(text("""
            SELECT l.loknr, l.navn, l.lat, l.lon FROM location_year y JOIN locations l ON l.loknr = y.loknr
            WHERE y.year >= :y AND y.n_taxa >= 60 AND l.merged_into IS NULL AND l.lat IS NOT NULL
            GROUP BY l.loknr, l.navn, l.lat, l.lon ORDER BY sum(y.n_obs) DESC LIMIT 400"""), {"y": date.today().year - 3})).mappings().all()
    out = [dict(r) for r in rows]
    await kv().set("lr:locs", json.dumps(out), ex=6 * 3600)
    return out


async def location(loknr: int) -> dict | None:
    async with ObsSession() as s:
        r = (await s.execute(text("SELECT loknr, navn, lat, lon FROM locations WHERE loknr = :l"), {"l": loknr})).mappings().first()
    return dict(r) if r else None


async def loc_frequency(loknr: int, doy: int, window: int) -> tuple[int, dict[int, tuple[str, int]]]:
    """(antal ture, {artnr: (navn, ture med arten)}) på lokaliteten i perioden ±window dage om dagen doy, de seneste YEARS år.
    Kun arter på artslisten og aldrig hemmelige observationer."""
    since = date(date.today().year - YEARS, 1, 1)
    params = {"l": loknr, "d": doy, "w": window, "since": since}
    async with ObsSession() as s:
        trips = (await s.execute(text(f"SELECT count(*) FROM trips WHERE loknr = :l AND dato >= :since AND {_DOY.format(c='dato')}"), params)).scalar() or 0
        rows = (await s.execute(text(f"""
            SELECT sp.artnr, sp.navn, count(DISTINCT o.turid) AS n FROM observations o
            JOIN species sp ON sp.artnr = o.artnr AND sp.art_type = 'art' AND sp.list_artnr = sp.artnr AND NOT sp.excluded
            WHERE o.loknr = :l AND o.hemmelig = 0 AND o.turid IS NOT NULL AND o.dato >= :since AND {_DOY.format(c='o.dato')}
            GROUP BY sp.artnr, sp.navn"""), params)).all()
    return trips, {a: (n, k) for a, n, k in rows}


async def weekly() -> dict:
    return await get_json("pheno:weekly") or {}


def _fmt_pct(k: int, n: int) -> str:
    return f"{round(100 * k / max(1, n))} %"


# ---------------------------------------------------------------- obs_odds / obs_habitat

async def _pick_location(cfg: dict, rng: random.Random) -> dict:
    if cfg.get("loknr"):
        loc = await location(int(cfg["loknr"]))
        if loc:
            return loc
    locs = await popular_locations()
    if not locs:
        raise GenError("Der er endnu ikke nok observationer til lokalitetsspørgsmål")
    return rng.choice(locs)


async def loc_question(kind: str, cfg: dict, rng: random.Random) -> dict:
    """Spørgsmål om en lokalitet: ``obs_odds`` (en uge) eller ``obs_habitat`` (en måned)."""
    wk = await weekly()
    if not wk:
        raise GenError("Fænologidata er ikke beregnet endnu")
    n_valg = max(2, min(4, int(cfg.get("n_valg") or 3)))
    for _ in range(12):
        loc = await _pick_location(cfg, rng)
        if kind == "obs_odds":
            uge = cfg.get("uge")
            week = week_index() if uge == "nu" else (int(uge) - 1 if isinstance(uge, int) else rng.randrange(52))
            week = max(0, min(51, week))
            doy, window, label = week * 7 + 4, 7, week_label(week)
        else:
            m = int(cfg["maaned"]) - 1 if cfg.get("maaned") else rng.randrange(12)
            week = min(51, int(m * 30.4 / 7))
            doy, window, label = int(m * 30.4) + 15, 15, MONTHS[m]
        trips, freq = await loc_frequency(loc["loknr"], doy, window)
        if trips < MIN_TRIPS_LOC:
            if cfg.get("loknr"):
                raise GenError(f"{loc['navn']} har kun {trips} ture i perioden – der skal mindst være {MIN_TRIPS_LOC}")
            continue
        good = [(a, v) for a, v in freq.items() if 0.3 <= v[1] / trips <= 0.95 and str(a) in wk]
        absent = [(a, w) for a, w in wk.items() if int(a) not in freq and w["rr"][week] >= 0.04]
        if not good or len(absent) < n_valg - 1:
            continue
        ra, (rn, rk) = rng.choice(good)
        wrong = rng.sample(absent, n_valg - 1)
        options = [rn] + [w["navn"] for _, w in wrong]
        if len(set(options)) < n_valg:
            continue
        sted = loc["navn"]
        if kind == "obs_odds":
            prompt = f"Du er ved {sted} i {label}. Hvilken af disse arter er mest sandsynlig at se?"
        else:
            prompt = f"Du står ved {sted} i {label}. Hvilken art hører mest til her?"
        expl = (f"{rn} blev set på {_fmt_pct(rk, trips)} af de {trips} ture til {sted} i denne periode. "
                + ", ".join(f"{w['navn']} er almindelig i landet nu, men blev ikke set her" for _, w in wrong[:2]) + ".")
        return {"prompt": prompt, "options": options, "correct": rn, "explanation": expl, "artnr": ra,
                "map": {"lat": loc["lat"], "lon": loc["lon"], "navn": sted, "loknr": loc["loknr"]}, "loknr": loc["loknr"]}
    raise GenError("Der er ikke data nok til et spørgsmål lige nu")


async def national_odds(cfg: dict, rng: random.Random) -> dict:
    """Landsdækkende ``obs_odds`` uden lokalitet: ud fra fænologiens rapporteringsrate for ugen."""
    wk = await weekly()
    if not wk:
        raise GenError("Fænologidata er ikke beregnet endnu")
    n_valg = max(2, min(4, int(cfg.get("n_valg") or 2)))
    uge = cfg.get("uge")
    week = week_index() if uge == "nu" else (max(0, min(51, int(uge) - 1)) if isinstance(uge, int) else rng.randrange(52))
    present = [(a, w) for a, w in wk.items() if w["rr"][week] >= 0.08]
    absent = [(a, w) for a, w in wk.items() if w["rr"][week] <= 0.005 and max(w["rr"]) >= 0.05]
    if not present or len(absent) < n_valg - 1:
        raise GenError("Der er ikke data nok til et spørgsmål lige nu")
    ra, right = rng.choice(present)
    wrong = rng.sample(absent, n_valg - 1)
    return {"prompt": f"Hvilken af disse arter er mest sandsynlig at se i Danmark i {week_label(week)}?",
            "options": [right["navn"]] + [w["navn"] for _, w in wrong], "correct": right["navn"], "artnr": int(ra),
            "explanation": f"{right['navn']} indrapporteres på ca. {round(100 * right['rr'][week])} % af turene i denne uge; "
                           + ", ".join(f"{w['navn']} næsten aldrig" for _, w in wrong) + "."}


async def generate_obs(kind: str, cfg: dict, rng: random.Random) -> dict:
    if kind == "obs_habitat":
        return await loc_question(kind, cfg, rng)
    if kind == "obs_odds":
        if cfg.get("loknr") or cfg.get("lokalitet"):
            return await loc_question(kind, cfg, rng)
        return await national_odds(cfg, rng)
    raise GenError("Ukendt generator")


# ---------------------------------------------------------------- foto_sammenlign

async def photo_pair(db: AsyncSession, a: int, b: int, rng: random.Random) -> dict:
    """To fotos side om side: «Hvilket foto viser {A}?». Returnerer ``media`` (to billeder), ``options``, ``correct`` og ``reveal``."""
    names = {r["artnr"]: r for r in (await db.execute(text("SELECT artnr, navn, latin FROM species WHERE artnr = ANY(:a) AND NOT excluded"), {"a": [a, b]})).mappings()}
    if a not in names or b not in names or a == b:
        raise GenError("Ukendte arter i parret")
    photos = {}
    for art in (a, b):
        row = (await db.execute(text(f"""SELECT p.id, m.medium_key, m.storage_key,
                (SELECT ph.navn FROM gal_photographers ph WHERE ph.id = p.photographer_id) AS fotograf
            {species_quiz._SINGLE} AND b.artnr = :a ORDER BY random() LIMIT 1"""), {"a": art})).mappings().first()
        if not row:
            raise GenError(f"Der er ikke noget foto af {names[art]['navn']} endnu")
        photos[art] = row
    ask, other = rng.choice([(a, b), (b, a)])
    order = [ask, other]
    rng.shuffle(order)
    urls = [{"url": "/media/art/" + (photos[x]["medium_key"] or photos[x]["storage_key"]), "credit": photos[x]["fotograf"]} for x in order]
    return {"prompt": f"Hvilket af de to fotos viser {names[ask]['navn']}?", "options": ["Foto 1", "Foto 2"], "correct": f"Foto {order.index(ask) + 1}",
            "media": {"kind": "images", "urls": urls}, "artnr": ask,
            "explanation": f"Foto {order.index(ask) + 1} viser {names[ask]['navn']} ({names[ask]['latin']}); det andet foto viser {names[other]['navn']}.",
            "reveal": {"artnr": ask, "navn": names[ask]["navn"], "latin": names[ask]["latin"], "andet": names[other]["navn"]}}
