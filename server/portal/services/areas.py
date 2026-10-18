"""Områder: afdeling, kommune, lokalitet, tegnet polygon eller radius om et punkt.

Al rumlig logik sker i PostGIS. Et område oversættes til et SQL-prædikat på observations-aliaset
`o` (og evt. lokalitets-aliaset `l`), som feed, lister, scoreboards og notifikationer deler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import Area

KINDS = ("afdeling", "kommune", "lokalitet", "polygon", "radius")


@dataclass(frozen=True)
class Scope:
    """Et område reduceret til hvad SQL har brug for."""

    kind: str  # dk / afdeling / kommune / lokalitet / area
    ref: str = ""

    @property
    def key(self) -> str:
        return {"dk": "dk", "afdeling": f"afd:{self.ref}", "kommune": f"kom:{self.ref}",
                "lokalitet": f"lok:{self.ref}", "area": f"area:{self.ref}"}[self.kind]

    def predicate(self, alias: str = "o", pname: str = "scope_ref") -> tuple[str, dict]:
        if self.kind == "dk":
            return "TRUE", {}
        if self.kind == "afdeling":
            return f"CAST(:{pname} AS smallint) = ANY({alias}.afdelinger)", {pname: int(self.ref)}
        if self.kind == "kommune":
            return (f"{alias}.loknr IN (SELECT loknr FROM locations WHERE kommune_kode = :{pname})", {pname: self.ref})
        if self.kind == "lokalitet":
            return f"{alias}.loknr = :{pname}", {pname: int(self.ref)}
        if self.kind == "area":
            return (f"{alias}.loknr IN (SELECT lx.loknr FROM locations lx JOIN areas ax ON ST_Intersects(ax.geom, lx.geom) "
                    f"WHERE ax.id = :{pname})", {pname: int(self.ref)})
        raise ValueError(self.kind)


def scope_for_area(area: Area | None) -> Scope:
    if area is None:
        return Scope("dk")
    if area.kind in ("polygon", "radius"):
        return Scope("area", str(area.id))
    return Scope(area.kind, str(area.ref))


def parse_scope(value: str | None) -> Scope:
    """'dk' | 'afd:574' | 'kom:0101' | 'lok:123' | 'area:7'"""
    if not value or value == "dk":
        return Scope("dk")
    prefix, _, ref = value.partition(":")
    kind = {"afd": "afdeling", "kom": "kommune", "lok": "lokalitet", "area": "area"}.get(prefix)
    if not kind or not ref:
        raise ValueError(f"Ukendt område {value}")
    if kind in ("afdeling", "lokalitet", "area") and not ref.isdigit():
        raise ValueError(f"Ukendt område {value}")
    return Scope(kind, ref)


async def set_geometry(session: AsyncSession, area: Area, geojson: dict | None, center: tuple[float, float] | None) -> None:
    """Gem geometri: polygon fra GeoJSON eller cirkel (radius i meter) om center=(lat, lon)."""
    if area.kind == "polygon" and geojson:
        geom = geojson.get("geometry", geojson)
        await session.execute(text("""
            UPDATE areas SET geom = ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326)) WHERE id = :id
        """), {"g": json.dumps(geom), "id": area.id})
    elif area.kind == "radius" and center and area.radius_m:
        await session.execute(text("""
            UPDATE areas SET geom = ST_Buffer(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :r, 32)::geometry
            WHERE id = :id
        """), {"lat": center[0], "lon": center[1], "r": area.radius_m, "id": area.id})
    elif area.kind == "kommune":
        await session.execute(text("UPDATE areas SET geom = (SELECT geom_simple FROM kommuner WHERE kode = :k) WHERE id = :id"),
                              {"k": area.ref, "id": area.id})
    elif area.kind == "lokalitet":
        await session.execute(text("""
            UPDATE areas SET geom = (SELECT ST_Buffer(geom::geography, 500)::geometry FROM locations WHERE loknr = :l) WHERE id = :id
        """), {"l": int(area.ref or 0), "id": area.id})


async def area_geojson(session: AsyncSession, area_id: int) -> dict | None:
    raw = (await session.execute(text("SELECT ST_AsGeoJSON(geom, 5) FROM areas WHERE id = :id"), {"id": area_id})).scalar()
    return json.loads(raw) if raw else None


async def refresh_area_counts(session: AsyncSession, scope: Scope) -> int:
    """Historiske antal observationer pr. art i et område (grundlag for 'lokale hits')."""
    pred, params = scope.predicate("o")
    await session.execute(text("DELETE FROM area_species_counts WHERE area_key = :k"), {"k": scope.key})
    res = await session.execute(text(f"""
        INSERT INTO area_species_counts (area_key, artnr, n_obs, n_days, last_date)
        SELECT :k, o.artnr, count(*), count(DISTINCT o.dato), max(o.dato)
        FROM observations o WHERE {pred} AND o.dato < current_date
        GROUP BY o.artnr
    """), {"k": scope.key, **params})
    return res.rowcount or 0


async def refresh_all_area_counts(session: AsyncSession) -> None:
    """Natlig genberegning: hele DK, alle afdelinger og kommuner samt brugeroprettede områder."""
    await session.execute(text("DELETE FROM area_species_counts WHERE area_key = 'dk' OR area_key LIKE 'afd:%' OR area_key LIKE 'kom:%'"))
    await session.execute(text("""
        INSERT INTO area_species_counts (area_key, artnr, n_obs, n_days, last_date)
        SELECT 'dk', artnr, count(*), count(DISTINCT dato), max(dato) FROM observations WHERE dato < current_date GROUP BY artnr
    """))
    await session.execute(text("""
        INSERT INTO area_species_counts (area_key, artnr, n_obs, n_days, last_date)
        SELECT 'afd:' || a, artnr, count(*), count(DISTINCT dato), max(dato)
        FROM observations, unnest(afdelinger) a WHERE dato < current_date GROUP BY a, artnr
    """))
    await session.execute(text("""
        INSERT INTO area_species_counts (area_key, artnr, n_obs, n_days, last_date)
        SELECT 'kom:' || l.kommune_kode, o.artnr, count(*), count(DISTINCT o.dato), max(o.dato)
        FROM observations o JOIN locations l USING (loknr)
        WHERE o.dato < current_date AND l.kommune_kode IS NOT NULL GROUP BY l.kommune_kode, o.artnr
    """))
    await session.commit()
    areas = (await session.execute(text("SELECT DISTINCT kind, ref, id FROM areas WHERE kind IN ('lokalitet','polygon','radius')"))).all()
    seen = set()
    for kind, ref, area_id in areas:
        scope = Scope("lokalitet", ref) if kind == "lokalitet" else Scope("area", str(area_id))
        if scope.key in seen:
            continue
        seen.add(scope.key)
        await refresh_area_counts(session, scope)
        await session.commit()
