"""Arter, lokaliteter, afdelinger, kommuner og brugerens egne områder."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import optional_user, require_user
from portal.cache import cached, worker_command
from portal.db import get_session
from portal.models import Area, Species, User
from portal.services.areas import KINDS, area_geojson, scope_for_area, set_geometry

router = APIRouter(prefix="/api", tags=["geo"])


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


@router.get("/locations/{loknr}")
async def location(loknr: int, db: AsyncSession = Depends(get_session)):
    loc = (await db.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, k.navn AS kommune, a.navn AS afdeling FROM locations l
        LEFT JOIN kommuner k ON k.kode = l.kommune_kode LEFT JOIN afdelinger a ON a.id = l.afdeling_id WHERE l.loknr = :l
    """), {"l": loknr})).mappings().first()
    if not loc:
        raise HTTPException(404)

    async def produce():
        top = (await db.execute(text("""
            SELECT s.navn, count(*) AS n_obs, count(DISTINCT o.dato) AS n_days, max(o.dato) AS last_date
            FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE o.loknr = :l AND o.hemmelig = 0 GROUP BY s.navn ORDER BY n_obs DESC LIMIT 600
        """), {"l": loknr})).mappings().all()
        recent = (await db.execute(text("""
            SELECT o.dato, s.navn AS art, o.antal, o.kategori, o.artnr FROM observations o JOIN species s ON s.artnr = o.artnr
            WHERE o.loknr = :l AND o.hemmelig = 0 AND o.dato > current_date - 30 ORDER BY o.dato DESC, o.kategori LIMIT 200
        """), {"l": loknr})).mappings().all()
        stats = (await db.execute(text("""
            SELECT count(*) AS n_obs, count(DISTINCT artnr) AS n_taxa, min(dato) AS first, max(dato) AS last
            FROM observations WHERE loknr = :l
        """), {"l": loknr})).mappings().first()
        return {"species": [dict(r) for r in top], "recent": [dict(r) for r in recent], "stats": dict(stats)}

    data = await cached(f"lok:{loknr}", 900, produce)
    return {**dict(loc), **data}


@router.get("/species")
async def species_search(q: str = Query("", max_length=60), limit: int = Query(30, le=1000),
                         db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(text("""
        SELECT artnr, navn, latin, klass, art_type FROM species
        WHERE (:q = '' OR navn ILIKE :like OR latin ILIKE :like) AND NOT excluded
        ORDER BY (navn ILIKE :p) DESC, art_type <> 'art', navn LIMIT :n
    """), {"q": q, "like": f"%{q}%", "p": f"{q}%", "n": limit})).mappings().all()
    return [dict(r) for r in rows]


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
    return {"artnr": sp.artnr, "navn": sp.navn, "latin": sp.latin, "klass": sp.klass, "art_type": sp.art_type, **data}


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


@router.get("/map/today")
async def map_today(level: str = Query("bemaerk", pattern="^(SU|SUB|bemaerk)$"), user: User | None = Depends(optional_user),
                    db: AsyncSession = Depends(get_session)):
    from portal.services.areas import Scope
    from portal.services.feed import threads
    from portal.timeutil import dk_today

    items = await threads(db, dk_today(), scope=Scope("dk"), level=level)
    return [t for t in items if t["lat"] is not None]
