"""Arter, lokaliteter, afdelinger, kommuner og brugerens egne områder."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import cached, worker_command
from portal.db import get_session
from portal.models import Area, User
from portal.services import areas, dofext, locspecies, migration, places, species_info
from portal.services.areas import KINDS, area_geojson, scope_for_area, set_geometry

router = APIRouter(prefix="/api", tags=["geo"])


@router.get("/afdelinger")
async def afdelinger(db: AsyncSession = Depends(get_session)):
    return await places.afdelinger(db)


@router.get("/kommuner")
async def kommuner(db: AsyncSession = Depends(get_session)):
    return await places.kommuner(db)


@router.get("/kommuner/{kode}/geojson")
async def kommune_geojson(kode: str, db: AsyncSession = Depends(get_session)):
    data = await places.kommune_geojson(db, kode)
    if data is None:
        raise HTTPException(404)
    return data


@router.get("/locations")
async def search_locations(q: str = Query(min_length=2, max_length=60), db: AsyncSession = Depends(get_session)):
    return await places.search_locations(db, q)


@router.get("/locations/index")
async def locations_index(q: str = Query("", max_length=60), kommune: str = Query("", max_length=4), afdeling: int | None = None,
                          year: str = Query("", pattern=r"^(|alle|\d{4})$"),
                          kat: str = Query("", pattern="^(|SU|SUB|bemaerk)$"), active: bool = False,
                          sort: str = Query("aktivitet", pattern="^(aktivitet|aar|arter|senest|navn|afstand)$"),
                          lat: float | None = None, lon: float | None = None, limit: int = Query(100, ge=1, le=300),
                          offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_session)):
    """Lokalitetsindeks (services/places.locations_index). sort=afstand kræver lat/lon."""
    try:
        return await places.locations_index(db, year=year, limit=limit, offset=offset, q=q, kommune=kommune, afdeling=afdeling,
                                            kat=kat, active=active, sort=sort, lat=lat, lon=lon)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/locations/{loknr}")
async def location(loknr: int, db: AsyncSession = Depends(get_session)):
    data = await places.location(db, loknr)
    if data is None:
        raise HTTPException(404)
    return {**data, "groups": migration.GROUPS}


@router.get("/locations/{loknr}/species")
async def location_species(loknr: int, year: int | None = Query(None, ge=1900, le=2200), user: User | None = Depends(optional_user),
                           db: AsyncSession = Depends(get_session)):
    """Artsliste med rekorder for lokaliteten – i alt eller for ét år. Tung på de store lokaliteter (fx Grenen), så den
    hentes for sig, mens resten af siden vises, og caches en time (undtagen brugerens egne arter); for de 100 mest aktive
    lokaliteter bygges den på forhånd af workeren. years = årene med observationer."""
    mine = await places.own_species(db, loknr, user.obserkode, year) if user is not None and user.obserkode else []
    return {**await places.species_list(db, loknr, year), "year": year, "mine": mine}


@router.get("/locations/{loknr}/records/{artnr}")
async def location_records(loknr: int, artnr: int, year: int | None = Query(None, ge=1900, le=2200),
                           db: AsyncSession = Depends(get_session)):
    """Top 10 unikke dage for arten på lokaliteten (evt. inden for ét år)."""
    return await places.records(db, loknr, artnr, year)


@router.get("/locations/{loknr}/species/{artnr}")
async def location_species_detail(loknr: int, artnr: int, year: int | None = Query(None, ge=1900, le=2200),
                                  db: AsyncSession = Depends(get_session)):
    """Én art på lokaliteten: gennem året, år for år, tidspunkt i forhold til solen og adfærd (evt. inden for ét år)."""
    data = await cached(f"lok:art2:{loknr}:{artnr}:{year or 'alle'}", places.LOC_TTL,
                        lambda: locspecies.build(db, loknr, artnr, year))
    if data is None:
        raise HTTPException(404)
    return data


@router.get("/species")
async def species_search(q: str = Query("", max_length=60), limit: int = Query(30, le=1000),
                         db: AsyncSession = Depends(get_session)):
    return await species_info.search(db, q, limit)


@router.get("/species/index")
async def species_index(db: AsyncSession = Depends(get_session)):
    """Artsindeks: alle taxa med årets tal (dagsaggregater + dagens observationer)."""
    return await species_info.index(db)


@router.get("/species/{artnr}")
async def species_detail(artnr: int, db: AsyncSession = Depends(get_session)):
    data = await species_info.detail(db, artnr)
    if data is None:
        raise HTTPException(404)
    return data


async def _list_artnr(db: AsyncSession, artnr: int) -> int:
    ls = await species_info.list_artnr(db, artnr)
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
    return [await area_out(db, a) for a in await areas.user_areas(db, user.id)]


@router.post("/areas")
async def create_area(body: AreaIn, user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if body.kind not in KINDS:
        raise HTTPException(400, "Ukendt områdetype")
    if await areas.count_for_user(db, user.id) >= areas.MAX_PER_USER:
        raise HTTPException(400, f"Højst {areas.MAX_PER_USER} områder pr. bruger")
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
    return {"key": key, "species": await areas.area_species(db, key)}


@router.get("/map/days")
async def map_days(days: int = Query(1, ge=1, le=7), back: int = Query(0, ge=0, le=1), db: AsyncSession = Depends(get_session)):
    """Tråde med trækretning for `days` døgn, der slutter `back` døgn før i dag (services/migration.map_days)."""
    return await migration.map_days(db, days, back)


@router.get("/map/today")
async def map_today(level: str = Query("bemaerk", pattern="^(SU|SUB|bemaerk)$"), user: User | None = Depends(optional_user),
                    db: AsyncSession = Depends(get_session)):
    from portal.services.areas import Scope
    from portal.services.feed import threads
    from portal.timeutil import dk_today

    items = await threads(db, dk_today(), scope=Scope("dk"), level=level)
    return [t for t in items if t["lat"] is not None]
