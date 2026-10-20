"""Arter, lokaliteter, afdelinger, kommuner og brugerens egne områder."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import cached, version, worker_command
from portal.db import get_session
from portal.models import Area, Species, User
from portal.services import dofext, finds, locspecies, migration
from portal.services.areas import KINDS, area_geojson, scope_for_area, set_geometry
from portal.services.feed import KAT_RANK

router = APIRouter(prefix="/api", tags=["geo"])
LOC_TTL = 3600  # lokalitetssiderne (lokalitet, artsliste, top 10, art på lokalitet) caches en time


@router.get("/afdelinger")
async def afdelinger(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("SELECT id, navn, slug FROM afdelinger ORDER BY navn"))).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kommuner")
async def kommuner(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("SELECT kode, navn FROM kommuner ORDER BY navn"))).mappings().all()
    return [dict(r) for r in rows]


@router.get("/kommuner/{kode}/geojson")
async def kommune_geojson(kode: str, db: AsyncSession = Depends(get_session)):
    async def produce():
        raw = (await db.execute(text("SELECT ST_AsGeoJSON(geom_simple, 5) FROM kommuner WHERE kode = :k"), {"k": kode})).scalar()
        return json.loads(raw) if raw else None
    data = await cached(f"komgeo:{kode}", 86400, produce)
    if data is None:
        raise HTTPException(404)
    return data


@router.get("/locations")
async def search_locations(q: str = Query(min_length=2, max_length=60), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune FROM locations l LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.navn ILIKE :q ORDER BY (l.navn ILIKE :p) DESC, length(l.navn) LIMIT 25
    """), {"q": f"%{q}%", "p": f"{q}%"})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/locations/index")
async def locations_index(q: str = Query("", max_length=60), kommune: str = Query("", max_length=4), afdeling: int | None = None,
                          year: str = Query("", pattern=r"^(|alle|\d{4})$"),
                          kat: str = Query("", pattern="^(|SU|SUB|bemaerk)$"), active: bool = False,
                          sort: str = Query("aktivitet", pattern="^(aktivitet|aar|arter|senest|navn|afstand)$"),
                          lat: float | None = None, lon: float | None = None, limit: int = Query(100, ge=1, le=300),
                          offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_session)):
    """Lokalitetsindeks: alle lokaliteter med tal for indeværende år (standard), et valgt år eller alle år. Tallene
    bygges af workeren hver 6. time (location_year) – siden læser kun tabellen. Filtrering, sortering og sider sker her,
    så telefonen kun henter de første rækker. sort=afstand kræver lat/lon."""
    from portal.cache import kv
    from portal.services import locyear
    from portal.timeutil import dk_today

    this_year = dk_today().year
    y = this_year if year == "" else 0 if year == "alle" else int(year)
    live = y == this_year

    years = [r[0] for r in (await db.execute(text("SELECT DISTINCT year FROM location_year WHERE year > 0 ORDER BY 1 DESC"))).all()]
    if this_year not in years:
        years.insert(0, this_year)
    if not (await db.execute(text("SELECT EXISTS (SELECT 1 FROM location_year WHERE year = :y)"), {"y": y})).scalar():
        # Perioden er ikke bygget endnu (første opstart, eller et nyt år) – workeren bygger den, og siden spørger igen
        if await kv().set(f"lokyear:requested:{y}", "1", nx=True, ex=600):
            await worker_command("location_years", years=[y] if y else None)
        return {"building": True, "total": 0, "all": 0, "items": [], "years": years, "year": year, "live": live,
                "built": await locyear.built_info()}

    async def produce():
        # Indeværende år: seneste 30 dage fra byggetidspunktet, og 'senest set' falder tilbage på alle år.
        # Øvrige perioder: tal og højeste kategori i perioden.
        rows = (await db.execute(text("""
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

    allrows = await cached(f"lokindex:{y}:{await version('locyear')}", 6 * 3600, produce)
    if sort == "aktivitet" and not live:
        sort = "aar"  # 'seneste 30 dage' findes kun for indeværende år
    kat_rank = {"SU": 3, "SUB": 2, "bemaerk": 1}.get(kat, 0)
    ql = q.strip().lower()
    rows = [r for r in allrows
            if (not ql or ql in r["navn"].lower())
            and (not kommune or r["kommune_kode"] == kommune)
            and (afdeling is None or r["afdeling_id"] == afdeling)
            and (not kat_rank or r["rank30"] >= kat_rank)
            and (not active or r["n_year"] > 0)]
    if sort == "afstand":
        if lat is None or lon is None:
            raise HTTPException(400, "Afstand kræver en position")
        rows = [{**r, "km": round(finds._km((lat, lon), (r["lat"], r["lon"])), 1)} for r in rows if r["lat"] is not None]
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
    katname = {3: "SU", 2: "SUB", 1: "bemaerk", 0: None}
    page = [{**{k: v for k, v in r.items() if k not in ("rank30", "kommune_kode")}, "kategori": katname[r["rank30"]]}
            for r in rows[offset:offset + limit]]
    return {"total": len(rows), "all": len(allrows), "items": page, "years": years, "year": year, "live": live,
            "built": await locyear.built_info()}


@router.get("/locations/{loknr}")
async def location(loknr: int, db: AsyncSession = Depends(get_session)):
    loc = (await db.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune, a.navn AS afdeling FROM locations l
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id WHERE l.loknr = :l
    """), {"l": loknr})).mappings().first()
    if not loc:
        raise HTTPException(404)

    kat = {3: "SU", 2: "SUB", 1: "bemaerk", 0: "alm"}

    async def produce():
        recent = (await db.execute(text(f"""
            SELECT o.dato, o.artnr, s.navn AS art, max(o.antal) AS antal, max({KAT_RANK}) AS rank
            FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE o.loknr = :l AND o.hemmelig = 0 AND o.dato > current_date - 30
            GROUP BY o.dato, o.artnr, s.navn ORDER BY o.dato DESC, rank DESC, s.navn LIMIT 1500
        """), {"l": loknr})).mappings().all()
        stats = (await db.execute(text("""
            SELECT count(*) AS n_obs, count(DISTINCT artnr) AS n_taxa, min(dato) AS first, max(dato) AS last
            FROM (SELECT DISTINCT dato, artnr FROM observations WHERE loknr = :l) d
        """), {"l": loknr})).mappings().first()
        return {"recent": [{**{k: r[k] for k in ("dato", "artnr", "art", "antal")}, "kategori": kat[r["rank"]]} for r in recent],
                "stats": dict(stats)}

    return {**dict(loc), **await cached(f"lok:{loknr}", LOC_TTL, produce), "groups": migration.GROUPS}


@router.get("/locations/{loknr}/species")
async def location_species(loknr: int, year: int | None = Query(None, ge=1900, le=2200), user: User | None = Depends(optional_user),
                           db: AsyncSession = Depends(get_session)):
    """Artsliste med rekorder for lokaliteten – i alt eller for ét år. Tung på de store lokaliteter (fx Grenen), så den
    hentes for sig, mens resten af siden vises, og caches en time (undtagen brugerens egne arter); for de 100 mest aktive
    lokaliteter bygges den på forhånd af workeren. years = årene med observationer."""
    from portal.services import locyear

    async def mine_rows():
        # Arter den indloggede selv har indtastet på lokaliteten (i det valgte år) – til filteret "Mangler hos mig"
        if user is None or not user.obserkode:
            return []
        rows = (await db.execute(text("""
            SELECT DISTINCT artnr FROM observations
            WHERE loknr = :l AND obserkode = :k AND antal > 0
              AND (CAST(:y AS int) IS NULL OR dato >= make_date(CAST(:y AS int), 1, 1) AND dato < make_date(CAST(:y AS int) + 1, 1, 1))
        """), {"l": loknr, "k": user.obserkode, "y": year})).scalars().all()
        return list(rows)

    # De mest aktive lokaliteter har listen klar fra workeren (se locyear.prebuild_species)
    data = await cached(locyear.species_key(loknr, year), LOC_TTL, lambda: locyear.species_list(db, loknr, year))
    return {**data, "year": year, "mine": await mine_rows()}


@router.get("/locations/{loknr}/records/{artnr}")
async def location_records(loknr: int, artnr: int, year: int | None = Query(None, ge=1900, le=2200),
                           db: AsyncSession = Depends(get_session)):
    """Top 10 unikke dage for arten på lokaliteten (evt. inden for ét år): dagens største enkeltobservation, med
    observatør og tråd."""

    async def produce():
        rows = (await db.execute(text("""
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


@router.get("/locations/{loknr}/species/{artnr}")
async def location_species_detail(loknr: int, artnr: int, year: int | None = Query(None, ge=1900, le=2200),
                                  db: AsyncSession = Depends(get_session)):
    """Én art på lokaliteten: gennem året, år for år, tidspunkt i forhold til solen og adfærd (evt. inden for ét år)."""
    data = await cached(f"lok:art2:{loknr}:{artnr}:{year or 'alle'}", LOC_TTL,
                        lambda: locspecies.build(db, loknr, artnr, year))
    if data is None:
        raise HTTPException(404)
    return data


@router.get("/species")
async def species_search(q: str = Query("", max_length=60), limit: int = Query(30, le=1000),
                         db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT artnr, navn, latin, klass, art_type FROM species
        WHERE (:q = '' OR navn ILIKE :like OR latin ILIKE :like) AND NOT excluded
        ORDER BY (navn ILIKE :p) DESC, art_type <> 'art', navn LIMIT :n
    """), {"q": q, "like": f"%{q}%", "p": f"{q}%", "n": limit})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/species/index")
async def species_index(db: AsyncSession = Depends(get_session)):
    """Artsindeks: alle taxa med årets tal (dagsaggregater + dagens observationer)."""
    from portal.timeutil import dk_today

    async def produce():
        today = dk_today()
        rows = (await db.execute(text("""
            WITH yr AS (
                SELECT artnr, sum(n_obs) AS n_year, min(dato) AS first_year, max(dato) AS last_year,
                       sum(n_obs) FILTER (WHERE dato > :d30) AS n_30
                FROM species_day WHERE dato >= :y0 AND dato < :today GROUP BY artnr),
            td AS (SELECT artnr, count(*) AS n_today FROM observations WHERE dato = :today GROUP BY artnr),
            ever AS (SELECT artnr, max(dato) AS last_date FROM species_day GROUP BY artnr)
            SELECT s.artnr, s.navn, s.latin, s.klass, s.art_type, s.sortering,
                   coalesce(yr.n_year, 0) + coalesce(td.n_today, 0) AS n_year, coalesce(yr.n_30, 0) + coalesce(td.n_today, 0) AS n_30,
                   coalesce(td.n_today, 0) AS n_today, CASE WHEN td.n_today > 0 THEN :today ELSE greatest(yr.last_year, ever.last_date) END AS last_date,
                   CASE WHEN yr.first_year IS NOT NULL THEN yr.first_year WHEN td.n_today > 0 THEN :today END AS first_year
            FROM species s LEFT JOIN yr USING (artnr) LEFT JOIN td USING (artnr) LEFT JOIN ever USING (artnr)
            WHERE NOT s.excluded
        """), {"y0": date(today.year, 1, 1), "today": today, "d30": today - timedelta(days=30)})).mappings().all()
        return [{**dict(r), "last_date": r["last_date"].isoformat() if r["last_date"] else None,
                 "first_year": r["first_year"].isoformat() if r["first_year"] else None} for r in rows]

    return await cached(f"species:index:{await version('feed')}", 900, produce)


@router.get("/species/{artnr}")
async def species_detail(artnr: int, db: AsyncSession = Depends(get_session)):
    sp = await db.get(Species, artnr)
    if sp is None:
        raise HTTPException(404)

    async def produce():
        recent = (await db.execute(text("""
            SELECT o.dato, o.loknr, l.navn AS lok, l.lat, l.lon, max(o.antal) AS antal, max(o.kategori) AS kategori
            FROM observations o JOIN locations l ON l.loknr = o.loknr
            WHERE o.artnr = :a AND o.hemmelig = 0 AND o.dato > current_date - 30
            GROUP BY o.dato, o.loknr, l.navn, l.lat, l.lon ORDER BY o.dato DESC LIMIT 500
        """), {"a": artnr})).mappings().all()
        counts = (await db.execute(text("""
            SELECT a.navn, c.n_obs FROM area_species_counts c JOIN afdelinger a ON 'afd:' || a.id = c.area_key
            WHERE c.artnr = :a ORDER BY c.n_obs DESC
        """), {"a": artnr})).mappings().all()
        total = (await db.execute(text("SELECT n_obs, n_days, last_date FROM area_species_counts WHERE area_key='dk' AND artnr=:a"),
                                  {"a": artnr})).mappings().first()
        return {"recent": [dict(r) for r in recent], "by_afdeling": [dict(r) for r in counts],
                "total": dict(total) if total else None}

    data = await cached(f"sp:{artnr}", 900, produce)
    return {"artnr": sp.artnr, "navn": sp.navn, "latin": sp.latin, "klass": sp.klass, "art_type": sp.art_type, **data,
            "finds": finds.public(await finds.species_finds(db, artnr))}


async def _list_artnr(db: AsyncSession, artnr: int) -> int:
    ls = (await db.execute(text("SELECT list_artnr FROM species WHERE artnr = :a"), {"a": artnr})).scalar()
    if ls is None:
        raise HTTPException(404)
    return ls


@router.get("/species/{artnr}/atlas")
async def species_atlas(artnr: int, db: AsyncSession = Depends(get_session)):
    return await dofext.atlas(await _list_artnr(db, artnr))


@router.get("/species/{artnr}/punkttaelling")
async def species_point_counts(artnr: int, db: AsyncSession = Depends(get_session)):
    return await dofext.point_counts(await _list_artnr(db, artnr))


@router.get("/atlas/squares")
async def atlas_squares():
    try:
        data = await dofext.atlas_squares()
    except Exception as exc:  # noqa: BLE001 – ekstern tjeneste nede
        raise HTTPException(502, "Atlaskvadraterne kunne ikke hentes fra DOF") from exc
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=604800"})


# ---------------------------------------------------------------- områder


class AreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    ref: str | None = Field(None, max_length=40)
    radius_m: int | None = Field(None, ge=100, le=100_000)
    center: list[float] | None = None  # [lat, lon]
    geojson: dict[str, Any] | None = None
    use_for_lists: bool = True


async def area_out(db: AsyncSession, a: Area) -> dict:
    return {"id": a.id, "name": a.name, "kind": a.kind, "ref": a.ref, "radius_m": a.radius_m,
            "use_for_lists": a.use_for_lists, "scope": scope_for_area(a).key, "geojson": await area_geojson(db, a.id)}


@router.get("/areas")
async def list_areas(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    areas = (await db.execute(select(Area).where(Area.user_id == user.id).order_by(Area.name))).scalars().all()
    return [await area_out(db, a) for a in areas]


@router.post("/areas")
async def create_area(body: AreaIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if body.kind not in KINDS:
        raise HTTPException(400, "Ukendt områdetype")
    if (await db.execute(text("SELECT count(*) FROM areas WHERE user_id = :u"), {"u": user.id})).scalar() >= 30:
        raise HTTPException(400, "Højst 30 områder pr. bruger")
    if body.kind == "polygon":
        if not body.geojson:
            raise HTTPException(400, "Tegn et polygon")
        coords = (body.geojson.get("geometry", body.geojson) or {}).get("coordinates") or [[]]
        if len(coords[0]) > 500:
            raise HTTPException(400, "Polygonet har for mange punkter")
    if body.kind == "radius" and (not body.center or not body.radius_m):
        raise HTTPException(400, "Vælg centrum og radius")
    if body.kind in ("afdeling", "kommune", "lokalitet") and not body.ref:
        raise HTTPException(400, "Vælg " + body.kind)
    area = Area(user_id=user.id, name=body.name, kind=body.kind, ref=body.ref, radius_m=body.radius_m,
                use_for_lists=body.use_for_lists)
    db.add(area)
    await db.flush()
    await set_geometry(db, area, body.geojson, tuple(body.center) if body.center else None)
    await db.commit()
    if area.kind in ("polygon", "radius", "lokalitet"):
        await worker_command("area_counts", scope=scope_for_area(area).key)
    return await area_out(db, area)


@router.patch("/areas/{area_id}")
async def update_area(area_id: int, body: dict[str, Any], user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_session)):
    area = await db.get(Area, area_id)
    if area is None or area.user_id != user.id:
        raise HTTPException(404)
    if "name" in body:
        area.name = str(body["name"])[:120]
    if "use_for_lists" in body:
        area.use_for_lists = bool(body["use_for_lists"])
    await db.commit()
    return await area_out(db, area)


@router.delete("/areas/{area_id}")
async def delete_area(area_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    area = await db.get(Area, area_id)
    if area is None or area.user_id != user.id:
        raise HTTPException(404)
    await db.delete(area)
    await db.commit()
    return {"ok": True}


@router.get("/areas/{area_id}/species")
async def area_species(area_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Artsliste med historiske antal for området (viser hvad der er et 'lokalt hit')."""
    area = await db.get(Area, area_id)
    if area is None or area.user_id != user.id:
        raise HTTPException(404)
    key = scope_for_area(area).key
    rows = (await db.execute(text("""
        SELECT s.artnr, s.navn, c.n_obs, c.n_days, c.last_date FROM area_species_counts c JOIN species s ON s.artnr = c.artnr
        WHERE c.area_key = :k AND s.art_type IN ('art','underart') ORDER BY c.n_obs
    """), {"k": key})).mappings().all()
    return {"key": key, "species": [dict(r) for r in rows]}


@router.get("/map/days")
async def map_days(days: int = Query(1, ge=1, le=7), back: int = Query(0, ge=0, le=1), db: AsyncSession = Depends(get_session)):
    """Tråde (art + lokalitet + dag) med trækretning for `days` døgn, der slutter `back` døgn før i dag.
    Bemærk/SUB/SU altid; almindelige arter kun når de er trækkende med retning (ellers bliver kortet uoverskueligt)."""
    from portal.services.migration import DIRS, GROUPS, _vec, species_groups
    from portal.timeutil import dk_today

    today = dk_today()
    groups = await species_groups(db)
    out = []
    for day in [today - timedelta(days=i) for i in range(back, back + days)]:
        async def produce(day=day):
            rows = (await db.execute(text(f"""
                SELECT o.artnr, s.list_artnr, s.navn AS art, o.loknr, l.navn AS lok, l.lat, l.lon, split_part(coalesce(o.adfkode, ''), ' ', 1) AS d,
                       max({KAT_RANK}) AS rank, sum(o.antal) AS n, max(o.antal) AS mx
                FROM observations o JOIN species s ON s.artnr = o.artnr JOIN locations l ON l.loknr = o.loknr
                WHERE o.dato = :d AND o.hemmelig = 0 AND l.lat IS NOT NULL AND o.antal > 0
                  AND ({KAT_RANK} >= 1 OR split_part(coalesce(o.adfkode, ''), ' ', 1) = ANY(:dirs))
                GROUP BY o.artnr, s.list_artnr, s.navn, o.loknr, l.navn, l.lat, l.lon, d
            """), {"d": day, "dirs": list(DIRS)})).mappings().all()
            threads: dict[tuple, dict] = {}
            for r in rows:
                t = threads.setdefault((r["artnr"], r["loknr"]), {
                    "id": f"{day.isoformat()}/{r['artnr']}/{r['loknr']}", "dato": day.isoformat(), "art": r["art"], "lok": r["lok"],
                    "loknr": r["loknr"], "group": groups.get(r["list_artnr"]) or groups.get(r["artnr"]),
                    "lat": r["lat"], "lon": r["lon"], "rank": 0, "max_antal": 0, "dirs": {}})
                t["rank"] = max(t["rank"], r["rank"])
                t["max_antal"] = max(t["max_antal"], int(r["mx"] or 0))
                if r["d"] in DIRS:
                    t["dirs"][r["d"]] = t["dirs"].get(r["d"], 0) + int(r["n"] or 0)
            items = []
            for t in threads.values():
                direction, consistency = _vec(t["dirs"]) if t["dirs"] else (None, 0)
                if t["rank"] == 0 and direction is None:
                    continue
                items.append({**{k: t[k] for k in ("id", "dato", "art", "lok", "loknr", "lat", "lon", "max_antal", "group", "dirs")},
                              "kategori": {3: "SU", 2: "SUB", 1: "bemaerk"}.get(t["rank"], "alm"),
                              "dir": direction, "r": consistency, "n_dir": sum(t["dirs"].values())})
            return items
        out += await cached(f"mapdays2:{day}:{await version('feed')}", 120 if day == today else 3600, produce)
    return {"items": out, "groups": GROUPS, "dirs": DIRS}


@router.get("/map/today")
async def map_today(level: str = Query("bemaerk", pattern="^(SU|SUB|bemaerk)$"), user: User | None = Depends(optional_user),
                    db: AsyncSession = Depends(get_session)):
    from portal.services.areas import Scope
    from portal.services.feed import threads
    from portal.timeutil import dk_today

    items = await threads(db, dk_today(), scope=Scope("dk"), level=level)
    return [t for t in items if t["lat"] is not None]
