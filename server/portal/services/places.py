"""Steder: afdelinger, kommuner og lokaliteter – søgning, lokalitetsindekset og lokalitetssiden (api/geo.py)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import cached, kv, version, worker_command
from portal.domain import geo, kategori
from portal.services import locyear
from portal.timeutil import dk_today

LOC_TTL = 3600  # lokalitetssiderne (lokalitet, artsliste, top 10, art på lokalitet) caches en time
INDEX_SORTS = ("aktivitet", "aar", "arter", "senest", "navn", "afstand")


async def afdelinger(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("SELECT id, navn, slug FROM afdelinger ORDER BY navn"))).mappings().all()
    return [dict(r) for r in rows]


async def kommuner(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("SELECT kode, navn FROM kommuner ORDER BY navn"))).mappings().all()
    return [dict(r) for r in rows]


async def kommune_geojson(session: AsyncSession, kode: str) -> dict | None:
    async def produce():
        raw = (await session.execute(text("SELECT ST_AsGeoJSON(geom_simple, 5) FROM kommuner WHERE kode = :k"), {"k": kode})).scalar()
        return json.loads(raw) if raw else None
    return await cached(f"komgeo:{kode}", 86400, produce)


async def search_locations(session: AsyncSession, q: str) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.navn ILIKE :q ORDER BY (l.navn ILIKE :p) DESC, length(l.navn) LIMIT 25
    """), {"q": f"%{q}%", "p": f"{q}%"})).mappings().all()
    return [dict(r) for r in rows]


async def _index_rows(session: AsyncSession, y: int, live: bool) -> list[dict]:
    """Alle lokaliteter med tal for perioden (bygget af workeren i location_year), cachet til næste bygning."""
    async def produce():
        # Indeværende år: seneste 30 dage fra byggetidspunktet, og 'senest set' falder tilbage på alle år.
        # Øvrige perioder: tal og højeste kategori i perioden.
        rows = (await session.execute(text("""
            SELECT l.loknr, l.navn, l.lat, l.lon, l.kommune_kode, k.navn AS kommune, l.afdeling_id,
                   coalesce(y.n_obs, 0) AS n_year, coalesce(y.n_taxa, 0) AS n_taxa,
                   CASE WHEN :live THEN coalesce(y.n_30, 0) ELSE 0 END AS n_30,
                   CASE WHEN :live THEN coalesce(y.rank30, 0) ELSE coalesce(y.rank, 0) END AS rank30,
                   CASE WHEN :live THEN coalesce(y.last_date, a.last_date) ELSE y.last_date END AS last_date
            FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
            LEFT JOIN location_year y ON y.loknr = l.loknr AND y.year = :y
            LEFT JOIN location_year a ON a.loknr = l.loknr AND a.year = 0
        """), {"y": y, "live": live})).mappings().all()
        return [{**dict(r), "last_date": r["last_date"].isoformat() if r["last_date"] else None} for r in rows]
    return await cached(f"lokindex:{y}:{await version('locyear')}", 6 * 3600, produce)


def filter_and_sort(allrows: list[dict], *, q: str = "", kommune: str = "", afdeling: int | None = None, kat: str = "",
                    active: bool = False, sort: str = "aktivitet", live: bool = True,
                    lat: float | None = None, lon: float | None = None) -> list[dict]:
    """Filtrér og sortér lokalitetsindeksets rækker (ren funktion – testes uden database). sort=afstand kræver lat/lon."""
    if sort == "aktivitet" and not live:
        sort = "aar"  # 'seneste 30 dage' findes kun for indeværende år
    kat_rank = kategori.rank(kat)
    ql = q.strip().lower()
    rows = [r for r in allrows
            if (not ql or ql in r["navn"].lower())
            and (not kommune or r["kommune_kode"] == kommune)
            and (afdeling is None or r["afdeling_id"] == afdeling)
            and (not kat_rank or r["rank30"] >= kat_rank)
            and (not active or r["n_year"] > 0)]
    if sort == "afstand":
        if lat is None or lon is None:
            raise ValueError("Afstand kræver en position")
        rows = [{**r, "km": round(geo.km((lat, lon), (r["lat"], r["lon"])), 1)} for r in rows if r["lat"] is not None]
        rows.sort(key=lambda r: r["km"])
    elif sort == "navn":
        rows.sort(key=lambda r: r["navn"].lower())
    else:
        key = {"aktivitet": lambda r: (r["n_30"], r["n_year"]), "aar": lambda r: (r["n_year"], r["n_taxa"]),
               "arter": lambda r: (r["n_taxa"], r["n_year"]), "senest": lambda r: (r["last_date"] or "", r["n_year"])}[sort]
        rows.sort(key=key, reverse=True)
    # Ved søgning står navne der begynder med søgeordet først (stabil sortering bevarer rækkefølgen i øvrigt)
    if ql and sort != "afstand":
        rows.sort(key=lambda r: not r["navn"].lower().startswith(ql))
    return rows


async def locations_index(session: AsyncSession, *, year: str = "", limit: int = 100, offset: int = 0, **filters) -> dict:
    """Lokalitetsindeks: alle lokaliteter med tal for indeværende år (standard), et valgt år eller alle år. Tallene
    bygges af workeren hver 6. time (location_year) – her læses kun tabellen, og der filtreres, sorteres og deles i
    sider, så telefonen kun henter de første rækker. Er perioden ikke bygget endnu, bestilles den hos workeren."""
    this_year = dk_today().year
    y = this_year if year == "" else 0 if year == "alle" else int(year)
    live = y == this_year

    years = [r[0] for r in (await session.execute(text("SELECT DISTINCT year FROM location_year WHERE year > 0 ORDER BY 1 DESC"))).all()]
    if this_year not in years:
        years.insert(0, this_year)
    if not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM location_year WHERE year = :y)"), {"y": y})).scalar():
        # Perioden er ikke bygget endnu (første opstart, eller et nyt år) – workeren bygger den, og siden spørger igen
        if await kv().set(f"lokyear:requested:{y}", "1", nx=True, ex=600):
            await worker_command("location_years", years=[y] if y else None)
        return {"building": True, "total": 0, "all": 0, "items": [], "years": years, "year": year, "live": live,
                "built": await locyear.built_info()}

    allrows = await _index_rows(session, y, live)
    rows = filter_and_sort(allrows, live=live, **filters)
    page = [{**{k: v for k, v in r.items() if k not in ("rank30", "kommune_kode")}, "kategori": kategori.BY_RANK.get(r["rank30"]) if r["rank30"] else None}
            for r in rows[offset:offset + limit]]
    return {"total": len(rows), "all": len(allrows), "items": page, "years": years, "year": year, "live": live,
            "built": await locyear.built_info()}


async def location(session: AsyncSession, loknr: int) -> dict | None:
    """Lokalitetssiden: stamdata, seneste 30 dages arter og tal for alle år (cachet en time)."""
    loc = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune, a.navn AS afdeling FROM locations l
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id WHERE l.loknr = :l
    """), {"l": loknr})).mappings().first()
    if not loc:
        return None

    async def produce():
        recent = (await session.execute(text(f"""
            SELECT o.dato, o.artnr, s.navn AS art, max(o.antal) AS antal, max({kategori.rank_sql("o.kategori")}) AS rank
            FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE o.loknr = :l AND o.hemmelig = 0 AND o.dato > current_date - 30
            GROUP BY o.dato, o.artnr, s.navn ORDER BY o.dato DESC, rank DESC, s.navn LIMIT 1500
        """), {"l": loknr})).mappings().all()
        # n_taxa = krydsbare arter som på årslisterne (artslisten viser dog alle taxa)
        stats = (await session.execute(text(f"""
            WITH d AS (SELECT loknr, artnr, dato, bool_or(antal > 0) AS pos FROM observations WHERE loknr = :l GROUP BY loknr, artnr, dato)
            SELECT count(*) AS n_obs, count(DISTINCT s.list_artnr) FILTER (WHERE d.pos AND {locyear.COUNTED_DAY}) AS n_taxa,
                   min(d.dato) AS first, max(d.dato) AS last
            FROM d JOIN species s ON s.artnr = d.artnr LEFT JOIN species ls ON ls.artnr = s.list_artnr
        """), {"l": loknr})).mappings().first()
        return {"recent": [{**{k: r[k] for k in ("dato", "artnr", "art", "antal")}, "kategori": kategori.from_rank(r["rank"])}
                           for r in recent],
                "stats": dict(stats)}

    return {**dict(loc), **await cached(f"lok2:{loknr}", LOC_TTL, produce)}


async def species_list(session: AsyncSession, loknr: int, year: int | None) -> dict:
    """Artsliste med rekorder (de mest aktive lokaliteter har den klar fra workeren, se locyear.prebuild_species)."""
    return await cached(locyear.species_key(loknr, year), LOC_TTL, lambda: locyear.species_list(session, loknr, year))


async def own_species(session: AsyncSession, loknr: int, obserkode: str, year: int | None) -> list[int]:
    """Arter observatøren selv har indtastet på lokaliteten (i det valgte år) – til filteret "Mangler hos mig"."""
    rows = (await session.execute(text("""
        SELECT DISTINCT artnr FROM observations
        WHERE loknr = :l AND obserkode = :k AND antal > 0
          AND (CAST(:y AS int) IS NULL OR dato >= make_date(CAST(:y AS int), 1, 1) AND dato < make_date(CAST(:y AS int) + 1, 1, 1))
    """), {"l": loknr, "k": obserkode, "y": year})).scalars().all()
    return list(rows)


async def records(session: AsyncSession, loknr: int, artnr: int, year: int | None) -> list[dict]:
    """Top 10 unikke dage for arten på lokaliteten (evt. inden for ét år): dagens største enkeltobservation, med
    observatør og tråd."""
    async def produce():
        rows = (await session.execute(text("""
            SELECT * FROM (
                SELECT DISTINCT ON (o.dato) o.dato, o.antal, o.obsid, o.adfkode, o.obserkode,
                       coalesce(nullif(trim(coalesce(ob.fornavn, '') || ' ' || coalesce(ob.efternavn, '')), ''), o.obserkode) AS observer
                FROM observations o LEFT JOIN observers ob ON ob.obserkode = o.obserkode
                WHERE o.loknr = :l AND o.artnr = :a AND o.hemmelig = 0 AND o.antal > 0
                  AND (CAST(:y AS int) IS NULL OR o.dato >= make_date(CAST(:y AS int), 1, 1) AND o.dato < make_date(CAST(:y AS int) + 1, 1, 1))
                ORDER BY o.dato, o.antal DESC, o.obsid
            ) d ORDER BY antal DESC, dato DESC LIMIT 10
        """), {"l": loknr, "a": artnr, "y": year})).mappings().all()
        return [dict(r) for r in rows]
    return await cached(f"lok:rec:{loknr}:{artnr}:{year or 'alle'}", LOC_TTL, produce)


async def location_extra(session: AsyncSession, loknr: int) -> dict:
    """Beskrivelse, typer, IBA og grænse fra DOFbasen (eller rettet i portalen) samt forælder og delområder – til
    lokalitetssiden. Cachet med versionen 'locations', som import og redigering tæller op."""
    async def produce():
        d = (await session.execute(text("""SELECT typer_tekst, beskrivelse, region, kommuner, iba_id, iba_navn, source
            FROM location_details WHERE loknr = :l"""), {"l": loknr})).mappings().first()
        g = (await session.execute(text("""SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.00001), 6) AS g, punkter, source,
            round((ST_Area(geom::geography) / 10000)::numeric, 1) AS ha
            FROM location_shapes WHERE loknr = :l AND valid_to IS NULL AND status = 'aktiv'"""), {"l": loknr})).mappings().first()
        rel = (await session.execute(text("""SELECT l.parent_loknr, p.navn AS parent_navn, l.merged_into
            FROM locations l LEFT JOIN locations p ON p.loknr = l.parent_loknr WHERE l.loknr = :l"""), {"l": loknr})).mappings().first()
        kids = (await session.execute(text("SELECT loknr, navn FROM locations WHERE parent_loknr = :l AND merged_into IS NULL ORDER BY navn"),
                                      {"l": loknr})).mappings().all()
        return {
            "detaljer": dict(d) if d else None,
            "graense": {"geojson": json.loads(g["g"]), "punkter": g["punkter"], "ha": float(g["ha"]), "source": g["source"]} if g else None,
            "foraelder": {"loknr": rel["parent_loknr"], "navn": rel["parent_navn"]} if rel and rel["parent_loknr"] else None,
            "lagt_sammen_med": rel["merged_into"] if rel else None,
            "delomraader": [dict(k) for k in kids],
        }
    return await cached(f"lokx:{loknr}:{await version('locations')}", LOC_TTL, produce)
