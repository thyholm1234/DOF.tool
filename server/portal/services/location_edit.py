"""Admin → Lokaliteter, trin 3 i docs/lokaliteter-plan.md: ret grænser, opret (også inden i andre), del op, læg sammen,
ret oplysninger, gendan en tidligere version og fjern overlap fra naboer.

Regler:
* Hver ændring af en grænse er en ny version i location_shapes (source='portal'); den forrige får valid_to. Intet slettes,
  så alt kan gendannes, og en senere DOFbasen-import kan se, at portalen har rettet grænsen.
* En lokalitet med observationer slettes aldrig. Deles den, bliver den forælder for de nye dele (observationerne bliver
  på forælderen); lægges to sammen, peger den ene videre til den anden (merged_into).
* Samtidige rettelser: klienten sender id'et på den version, den har rettet i (base_id); er der kommet en nyere, afvises
  gemningen (EditError 409), så ingen overskriver en andens arbejde uden at vide det.
* locations er referencedata: skrives i observationsgruppen (session.info['home']) og kopieres til brugergruppen.
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.dbgroups import OBSERVATIONS
from portal.services.location_import import clean_border

PORTAL_START = 90_000_000
OVERLAP_WARN = 0.02  # overlap over 2 % af den mindste lokalitet giver en advarsel
MAX_MAP_FEATURES = 2500
MIN_ZOOM = 10
CUR = "valid_to IS NULL AND status = 'aktiv'"


def cur(alias: str) -> str:
    """Den gældende grænse for tabelaliasset (CUR med alias på begge kolonner)."""
    return f"{alias}.valid_to IS NULL AND {alias}.status = 'aktiv'"


class EditError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _obs(session: AsyncSession) -> None:
    session.info["home"] = OBSERVATIONS


def tolerance(zoom: int) -> float:
    """Forenkling (grader) til kortvisning – ca. en halv pixel. Fra zoom 16 vises grænsen uforenklet."""
    return 0.0 if zoom >= 16 else 360 / (256 * 2 ** zoom) / 2


async def _log(session: AsyncSession, loknr: int, action: str, user_id: int | None, before=None, after=None, note: str | None = None):
    await session.execute(text("""INSERT INTO location_edits (loknr, action, before, after, user_id, note)
        VALUES (:l, :a, CAST(:b AS jsonb), CAST(:f AS jsonb), :u, :n)"""),
                          {"l": loknr, "a": action, "b": json.dumps(before) if before is not None else None,
                           "f": json.dumps(after) if after is not None else None, "u": user_id, "n": (note or None) and note[:500]})


async def _done(session: AsyncSession) -> None:
    await session.commit()
    await bump("locations")


# ---------------------------------------------------------------- læsning


async def map_features(session: AsyncSession, bbox: tuple[float, float, float, float], zoom: int) -> dict:
    """Grænser (forenklet til zoom) og lokaliteter uden grænse (punkter) i kortudsnittet som GeoJSON."""
    if zoom < MIN_ZOOM:
        return {"type": "FeatureCollection", "features": [], "zoom_ind": True}
    w, s, e, n = bbox
    a = {"w": w, "s": s, "e": e, "n": n, "tol": tolerance(zoom), "lim": MAX_MAP_FEATURES}
    rows = (await session.execute(text(f"""
        SELECT x.loknr, l.navn, x.source, x.id AS shape_id, l.parent_loknr, l.origin,
               ST_AsGeoJSON(CASE WHEN :tol > 0 THEN ST_SimplifyPreserveTopology(x.geom, :tol) ELSE x.geom END, 6) AS g
        FROM location_shapes x JOIN locations l USING (loknr)
        WHERE {cur('x')} AND l.merged_into IS NULL AND x.geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
        ORDER BY ST_Area(x.geom) DESC LIMIT :lim"""), a)).mappings().all()
    points = (await session.execute(text(f"""
        SELECT l.loknr, l.navn, l.lat, l.lon, l.origin FROM locations l
        WHERE l.merged_into IS NULL AND l.geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
          AND NOT EXISTS (SELECT 1 FROM location_shapes x WHERE x.loknr = l.loknr AND {cur('x')}) LIMIT :lim"""), a)).mappings().all()
    feats = [{"type": "Feature", "geometry": json.loads(r["g"]),
              "properties": {k: r[k] for k in ("loknr", "navn", "source", "shape_id", "parent_loknr", "origin")}} for r in rows]
    feats += [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
               "properties": {"loknr": r["loknr"], "navn": r["navn"], "origin": r["origin"], "uden_graense": True}} for r in points]
    return {"type": "FeatureCollection", "features": feats, "afkortet": len(rows) >= MAX_MAP_FEATURES}


async def detail(session: AsyncSession, loknr: int) -> dict | None:
    loc = (await session.execute(text("""
        SELECT l.loknr, l.navn, l.lat, l.lon, l.origin, l.parent_loknr, l.merged_into, p.navn AS parent_navn, k.navn AS kommune
        FROM locations l LEFT JOIN locations p ON p.loknr = l.parent_loknr LEFT JOIN kommuner k ON k.kode = l.kommune_kode
        WHERE l.loknr = :l"""), {"l": loknr})).mappings().first()
    if loc is None:
        return None
    d = (await session.execute(text("""SELECT lokid, typer_tekst, beskrivelse, region, kommuner, afdelinger, iba_id, iba_navn,
        dofbasen, source, fetched_at, updated_at FROM location_details WHERE loknr = :l"""), {"l": loknr})).mappings().first()
    shape = (await session.execute(text(f"""SELECT id, source, punkter, valid_from, ST_AsGeoJSON(geom, 7) AS g,
        round((ST_Area(geom::geography) / 10000)::numeric, 1) AS ha FROM location_shapes WHERE loknr = :l AND {CUR}"""),
                                   {"l": loknr})).mappings().first()
    versions = (await session.execute(text("""SELECT id, source, status, punkter, valid_from, valid_to, user_id, note
        FROM location_shapes WHERE loknr = :l ORDER BY id DESC LIMIT 50"""), {"l": loknr})).mappings().all()
    children = (await session.execute(text("SELECT loknr, navn FROM locations WHERE parent_loknr = :l ORDER BY navn"),
                                      {"l": loknr})).mappings().all()
    edits = (await session.execute(text("""SELECT id, action, user_id, note, created_at FROM location_edits
        WHERE loknr = :l ORDER BY id DESC LIMIT 30"""), {"l": loknr})).mappings().all()
    n_obs = (await session.execute(text("SELECT count(*) FROM observations WHERE loknr = :l"), {"l": loknr})).scalar()
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        **dict(loc), "observationer": n_obs, "kan_slettes": loc["origin"] == "portal" and not n_obs and not children,
        "detaljer": {**dict(d), "fetched_at": iso(d["fetched_at"]), "updated_at": iso(d["updated_at"])} if d else None,
        "graense": {"id": shape["id"], "source": shape["source"], "punkter": shape["punkter"], "ha": float(shape["ha"]),
                    "valid_from": iso(shape["valid_from"]), "geojson": json.loads(shape["g"])} if shape else None,
        "versioner": [{**dict(v), "valid_from": iso(v["valid_from"]), "valid_to": iso(v["valid_to"])} for v in versions],
        "boern": [dict(c) for c in children],
        "log": [{**dict(e), "created_at": iso(e["created_at"])} for e in edits],
    }


async def shape_geojson(session: AsyncSession, loknr: int, shape_id: int) -> dict | None:
    g = (await session.execute(text("SELECT ST_AsGeoJSON(geom, 7) FROM location_shapes WHERE id = :i AND loknr = :l"),
                               {"i": shape_id, "l": loknr})).scalar()
    return json.loads(g) if g else None


# ---------------------------------------------------------------- geometri og kontroller


def _geojson_in(g: dict) -> str:
    """Kontrollér klientens GeoJSON (Polygon/MultiPolygon, tal, punkter) – samme regler som importen."""
    if isinstance(g, dict) and g.get("type") == "Feature":
        g = g.get("geometry")
    try:
        geojson, points, _ = clean_border(g)
    except ValueError as exc:
        raise EditError(f"Ugyldig grænse: {exc}") from exc
    if not geojson:
        raise EditError("Tegn en grænse")
    return geojson


# Klientens geometri gjort gyldig (selvskæringer repareres) og som MultiPolygon
_GEOM = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326)), 3))"


async def _current(session: AsyncSession, loknr: int) -> dict | None:
    return (await session.execute(text(f"SELECT id, source FROM location_shapes WHERE loknr = :l AND {CUR} FOR UPDATE"),
                                  {"l": loknr})).mappings().first()


async def _location(session: AsyncSession, loknr: int) -> dict:
    loc = (await session.execute(text("SELECT loknr, navn, origin, parent_loknr, merged_into FROM locations WHERE loknr = :l FOR UPDATE"),
                                 {"l": loknr})).mappings().first()
    if loc is None:
        raise EditError("Lokaliteten findes ikke", 404)
    if loc["merged_into"]:
        raise EditError(f"Lokaliteten er lagt sammen med {loc['merged_into']} – ret den i stedet")
    return dict(loc)


def _check_base(cur: dict | None, base_id: int | None) -> None:
    if cur and base_id is not None and cur["id"] != base_id:
        raise EditError("Grænsen er ændret af en anden, mens du rettede den – hent den igen", 409)


async def _new_version(session: AsyncSession, loknr: int, geom_sql: str, args: dict, user_id: int | None, note: str | None) -> int:
    """Luk den gældende grænse og indsæt en ny (portal) version ud fra et SQL-udtryk. Returnerer det nye id."""
    await session.execute(text(f"UPDATE location_shapes SET valid_to = now() WHERE loknr = :l AND {CUR}"), {"l": loknr})
    new_id = (await session.execute(text(f"""INSERT INTO location_shapes (loknr, geom, punkter, source, status, user_id, note)
        SELECT :l, g, ST_NPoints(g), 'portal', 'aktiv', :u, :n FROM (SELECT {geom_sql} AS g) x WHERE NOT ST_IsEmpty(g)
        RETURNING id"""), {**args, "l": loknr, "u": user_id, "n": note})).scalar()
    if new_id is None:
        raise EditError("Grænsen blev tom")
    return new_id


async def warnings(session: AsyncSession, loknr: int) -> list[dict]:
    """Advarsler for den gældende grænse: overlap med naboer, centrum udenfor, uden for forælderen."""
    out = []
    rows = (await session.execute(text(f"""
        WITH me AS (SELECT geom FROM location_shapes WHERE loknr = :l AND {CUR})
        SELECT o.loknr, l.navn,
               ST_Area(ST_Intersection(o.geom, me.geom)::geography) / nullif(least(ST_Area(o.geom::geography), ST_Area(me.geom::geography)), 0) AS andel
        FROM me, location_shapes o JOIN locations l USING (loknr)
        WHERE {cur('o')} AND o.loknr <> :l AND o.geom && me.geom AND ST_Intersects(o.geom, me.geom)
          AND l.loknr IS DISTINCT FROM (SELECT parent_loknr FROM locations WHERE loknr = :l)
          AND l.parent_loknr IS DISTINCT FROM :l"""), {"l": loknr})).mappings().all()
    over = [{"loknr": r["loknr"], "navn": r["navn"], "andel": round(float(r["andel"] or 0), 3)} for r in rows if (r["andel"] or 0) > OVERLAP_WARN]
    if over:
        out.append({"type": "overlap", "tekst": f"Overlapper {len(over)} nabo(er) med mere end {int(OVERLAP_WARN * 100)} %", "naboer": over})
    c = (await session.execute(text(f"""SELECT ST_Contains(s.geom, l.geom) AS inde, ST_Y(ST_PointOnSurface(s.geom)) AS lat,
        ST_X(ST_PointOnSurface(s.geom)) AS lon FROM location_shapes s JOIN locations l USING (loknr)
        WHERE s.loknr = :l AND {cur('s')} AND l.geom IS NOT NULL"""), {"l": loknr})).mappings().first()
    if c and c["inde"] is False:
        out.append({"type": "centrum", "tekst": "Lokalitetens punkt ligger uden for grænsen", "forslag": {"lat": c["lat"], "lon": c["lon"]}})
    p = (await session.execute(text(f"""SELECT ST_CoveredBy(s.geom, ST_Buffer(ps.geom, 0.00001)) FROM locations l
        JOIN location_shapes s ON s.loknr = l.loknr AND {cur('s')}
        JOIN location_shapes ps ON ps.loknr = l.parent_loknr AND {cur('ps')} WHERE l.loknr = :l"""), {"l": loknr})).scalar()
    if p is False:
        out.append({"type": "forælder", "tekst": "Grænsen går uden for forælderens grænse"})
    return out


# ---------------------------------------------------------------- handlinger


async def save_shape(session: AsyncSession, loknr: int, geojson: dict, base_id: int | None, user_id: int, note: str | None = None) -> dict:
    _obs(session)
    await _location(session, loknr)
    cur = await _current(session, loknr)
    _check_base(cur, base_id)
    new_id = await _new_version(session, loknr, _GEOM, {"g": _geojson_in(geojson)}, user_id, note)
    await _log(session, loknr, "grænse", user_id, {"shape_id": cur["id"] if cur else None}, {"shape_id": new_id}, note)
    w = await warnings(session, loknr)
    await _done(session)
    return {"shape_id": new_id, "advarsler": w}


async def _next_loknr(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT nextval('portal_location_seq')"))).scalar()


async def _insert_location(session: AsyncSession, loknr: int, navn: str, parent: int | None, shape_id_sql: str, args: dict,
                           user_id: int, note: str) -> None:
    """Ny portallokalitet med punkt inde i grænsen (ST_PointOnSurface), forælderens lokalafdeling og kommune via punktet."""
    await session.execute(text(f"""
        INSERT INTO locations (loknr, navn, afdeling_id, lat, lon, geom, origin, parent_loknr)
        SELECT :l, :navn, (SELECT afdeling_id FROM locations WHERE loknr = :p), ST_Y(pt), ST_X(pt), pt, 'portal', :p
        FROM (SELECT ST_PointOnSurface({shape_id_sql}) AS pt) x"""), {**args, "l": loknr, "navn": navn, "p": parent})
    from portal.dof.ingest import assign_kommuner  # her for ikke at trække hele indlæsningen ind ved import

    await assign_kommuner(session, [loknr])


async def create(session: AsyncSession, navn: str, geojson: dict, user_id: int, parent_loknr: int | None = None,
                 typer_tekst: str | None = None, beskrivelse: str | None = None) -> dict:
    """Opret en ny lokalitet. Med parent_loknr klippes grænsen til forælderens."""
    _obs(session)
    navn = (navn or "").strip()
    if not 2 <= len(navn) <= 250:
        raise EditError("Navnet skal være 2–250 tegn")
    g = _geojson_in(geojson)
    geom_sql, args = _GEOM, {"g": g}
    if parent_loknr:
        await _location(session, parent_loknr)
        if not await _current(session, parent_loknr):
            raise EditError("Forælderen har ingen grænse at oprette inden i")
        geom_sql = f"""ST_Multi(ST_CollectionExtract(ST_Intersection({_GEOM},
            (SELECT geom FROM location_shapes WHERE loknr = :p AND {CUR})), 3))"""
        args["p"] = parent_loknr
    loknr = await _next_loknr(session)
    await _insert_location(session, loknr, navn, parent_loknr, geom_sql, args, user_id, "opret")
    new_id = await _new_version(session, loknr, geom_sql, args, user_id, "Oprettet i portalen")
    await session.execute(text("""INSERT INTO location_details (loknr, navn, aktiv, kommuner, afdelinger, typer_tekst, beskrivelse, source, updated_at)
        VALUES (:l, :n, true, '{}', '{}', :t, :b, 'portal', now())"""), {"l": loknr, "n": navn, "t": (typer_tekst or None) and typer_tekst[:300],
                                                       "b": (beskrivelse or None) and beskrivelse[:20_000]})
    await _log(session, loknr, "opret", user_id, None, {"shape_id": new_id, "navn": navn, "parent_loknr": parent_loknr})
    w = await warnings(session, loknr)
    await _done(session)
    return {"loknr": loknr, "shape_id": new_id, "advarsler": w}


async def split(session: AsyncSession, loknr: int, line: dict, names: list[str] | None, base_id: int | None, user_id: int) -> dict:
    """Del en lokalitet langs en linje i nye lokaliteter (børn); den oprindelige bliver forælder og beholder sin grænse og
    sine observationer."""
    _obs(session)
    loc = await _location(session, loknr)
    cur = await _current(session, loknr)
    if not cur:
        raise EditError("Lokaliteten har ingen grænse at dele")
    _check_base(cur, base_id)
    if isinstance(line, dict) and line.get("type") == "Feature":
        line = line.get("geometry")
    if not isinstance(line, dict) or line.get("type") != "LineString" or len(line.get("coordinates") or []) < 2:
        raise EditError("Tegn en linje, der krydser lokaliteten")
    parts = (await session.execute(text("""
        SELECT ST_AsGeoJSON((d).geom) FROM (
            SELECT ST_Dump(ST_CollectionExtract(ST_Split(s.geom, ST_SetSRID(ST_GeomFromGeoJSON(:line), 4326)), 3)) AS d
            FROM location_shapes s WHERE s.id = :sid) x
        ORDER BY ST_Area((d).geom) DESC"""), {"line": json.dumps(line), "sid": cur["id"]})).scalars().all()
    if len(parts) < 2:
        raise EditError("Linjen deler ikke lokaliteten – den skal krydse grænsen helt")
    if len(parts) > 20:
        raise EditError("Det giver mere end 20 dele")
    created = []
    for i, part in enumerate(parts):
        navn = ((names or [])[i] if names and i < len(names) and (names[i] or "").strip() else f"{loc['navn']} ({i + 1})").strip()[:250]
        child = await _next_loknr(session)
        args = {"g": part}
        await _insert_location(session, child, navn, loknr, _GEOM, args, user_id, "del")
        sid = await _new_version(session, child, _GEOM, args, user_id, f"Delt fra {loknr}")
        await session.execute(text("INSERT INTO location_details (loknr, navn, aktiv, kommuner, afdelinger, source, updated_at) "
                                   "VALUES (:l, :n, true, '{}', '{}', 'portal', now())"),
                              {"l": child, "n": navn})
        created.append({"loknr": child, "navn": navn, "shape_id": sid})
    await _log(session, loknr, "del", user_id, {"shape_id": cur["id"]}, {"boern": created})
    await _done(session)
    return {"boern": created}


async def merge(session: AsyncSession, keep: int, other: int, base_id: int | None, user_id: int) -> dict:
    """Læg other sammen med keep: keep får foreningen af grænserne, other peger videre (merged_into), og others børn
    flyttes til keep. Observationerne bliver på deres loknr."""
    _obs(session)
    if keep == other:
        raise EditError("Vælg en anden lokalitet")
    await _location(session, keep)
    await _location(session, other)
    cur = await _current(session, keep)
    _check_base(cur, base_id)
    ocur = await _current(session, other)
    geom_sql = f"""ST_Multi(ST_CollectionExtract(ST_Union(ARRAY(
        SELECT geom FROM location_shapes WHERE loknr IN (:k, :o) AND {CUR})), 3))"""
    new_id = await _new_version(session, keep, geom_sql, {"k": keep, "o": other}, user_id, f"Lagt sammen med {other}") \
        if cur or ocur else None
    await session.execute(text(f"UPDATE location_shapes SET valid_to = now() WHERE loknr = :o AND {CUR}"), {"o": other})
    await session.execute(text("UPDATE locations SET merged_into = :k WHERE loknr = :o"), {"k": keep, "o": other})
    moved = (await session.execute(text("UPDATE locations SET parent_loknr = :k WHERE parent_loknr = :o"), {"k": keep, "o": other})).rowcount
    await _log(session, keep, "læg sammen", user_id, {"shape_id": cur["id"] if cur else None}, {"shape_id": new_id, "andre": other})
    await _log(session, other, "læg sammen", user_id, {"shape_id": ocur["id"] if ocur else None}, {"merged_into": keep})
    await _done(session)
    return {"shape_id": new_id, "boern_flyttet": moved}


async def _ancestors(session: AsyncSession, loknr: int) -> list[int]:
    rows = (await session.execute(text("""WITH RECURSIVE a AS (SELECT parent_loknr AS p, 1 AS n FROM locations WHERE loknr = :l
        UNION ALL SELECT l.parent_loknr, a.n + 1 FROM locations l JOIN a ON l.loknr = a.p WHERE a.p IS NOT NULL AND a.n < 50)
        SELECT p FROM a WHERE p IS NOT NULL"""), {"l": loknr})).scalars().all()
    return list(rows)


async def update_info(session: AsyncSession, loknr: int, fields: dict, user_id: int) -> dict:
    """Ret navn (kun portalens egne lokaliteter – DOFbasens navne følger referencedata), typer, beskrivelse og forælder.
    Rettede detaljer får source='portal', så en senere DOFbasen-import ikke overskriver dem."""
    _obs(session)
    loc = await _location(session, loknr)
    before, after = {}, {}
    if "navn" in fields:
        navn = (fields["navn"] or "").strip()
        if loc["origin"] != "portal":
            raise EditError("DOFbasens lokaliteter skifter navn i DOFbasen")
        if not 2 <= len(navn) <= 250:
            raise EditError("Navnet skal være 2–250 tegn")
        await session.execute(text("UPDATE locations SET navn = :n WHERE loknr = :l"), {"n": navn, "l": loknr})
        before["navn"], after["navn"] = loc["navn"], navn
    if "parent_loknr" in fields:
        p = fields["parent_loknr"]
        if p is not None:
            if p == loknr or loknr in await _ancestors(session, p):
                raise EditError("En lokalitet kan ikke ligge inden i sig selv")
            await _location(session, p)
        await session.execute(text("UPDATE locations SET parent_loknr = :p WHERE loknr = :l"), {"p": p, "l": loknr})
        before["parent_loknr"], after["parent_loknr"] = loc["parent_loknr"], p
    detail = {k: fields[k] for k in ("typer_tekst", "beskrivelse") if k in fields}
    if detail:
        old = (await session.execute(text("SELECT typer_tekst, beskrivelse FROM location_details WHERE loknr = :l"), {"l": loknr})).mappings().first()
        t, b = detail.get("typer_tekst", old["typer_tekst"] if old else None), detail.get("beskrivelse", old["beskrivelse"] if old else None)
        await session.execute(text("""INSERT INTO location_details (loknr, navn, aktiv, kommuner, afdelinger, typer_tekst, beskrivelse,
                                                                    source, updated_at)
            VALUES (:l, :n, true, '{}', '{}', :t, :b, 'portal', now())
            ON CONFLICT (loknr) DO UPDATE SET typer_tekst = EXCLUDED.typer_tekst, beskrivelse = EXCLUDED.beskrivelse,
                source = 'portal', updated_at = now()"""),
                              {"l": loknr, "n": after.get("navn", loc["navn"]), "t": ((t or "").strip() or None) and t.strip()[:300],
                               "b": ((b or "").strip() or None) and b.strip()[:20_000]})
        before |= dict(old) if old else {}
        after |= detail
    if not after:
        raise EditError("Intet at ændre")
    await _log(session, loknr, "oplysninger", user_id, before, after)
    await _done(session)
    return {"ok": True}


async def revert(session: AsyncSession, loknr: int, shape_id: int, base_id: int | None, user_id: int) -> dict:
    """Gendan en tidligere version som en ny portalversion."""
    _obs(session)
    await _location(session, loknr)
    cur = await _current(session, loknr)
    _check_base(cur, base_id)
    if not (await session.execute(text("SELECT 1 FROM location_shapes WHERE id = :i AND loknr = :l"), {"i": shape_id, "l": loknr})).scalar():
        raise EditError("Versionen findes ikke", 404)
    new_id = await _new_version(session, loknr, "(SELECT geom FROM location_shapes WHERE id = :sid)", {"sid": shape_id},
                                user_id, f"Gendannet fra version {shape_id}")
    await _log(session, loknr, "gendan", user_id, {"shape_id": cur["id"] if cur else None}, {"shape_id": new_id, "fra": shape_id})
    await _done(session)
    return {"shape_id": new_id, "advarsler": await warnings(session, loknr)}


async def trim_neighbours(session: AsyncSession, loknr: int, neighbours: list[int], user_id: int) -> dict:
    """Fjern denne lokalitets areal fra de valgte naboer (fælles grænse uden overlap). Hver nabo får en ny version."""
    _obs(session)
    await _location(session, loknr)
    if not await _current(session, loknr):
        raise EditError("Lokaliteten har ingen grænse")
    done, skipped = [], []
    for n in dict.fromkeys(neighbours):
        if n == loknr:
            continue
        ncur = await _current(session, n)
        if not ncur:
            continue
        geom_sql = f"""ST_Multi(ST_CollectionExtract(ST_Difference(
            (SELECT geom FROM location_shapes WHERE id = :nid), (SELECT geom FROM location_shapes WHERE loknr = :me AND {CUR})), 3))"""
        empty = (await session.execute(text(f"SELECT ST_IsEmpty({geom_sql})"), {"nid": ncur["id"], "me": loknr})).scalar()
        if empty:
            skipped.append(n)  # naboen ligger helt inde i denne – den røres ikke
            continue
        new_id = await _new_version(session, n, geom_sql, {"nid": ncur["id"], "me": loknr}, user_id, f"Overlap med {loknr} fjernet")
        await _log(session, n, "naboer", user_id, {"shape_id": ncur["id"]}, {"shape_id": new_id, "pga": loknr})
        done.append(n)
    await _done(session)
    return {"rettet": done, "sprunget_over": skipped}


async def delete(session: AsyncSession, loknr: int, user_id: int) -> dict:
    """Slet en portallokalitet uden observationer, feltmeldinger og børn (alt andet kan kun deles eller lægges sammen)."""
    _obs(session)
    loc = await _location(session, loknr)
    if loc["origin"] != "portal":
        raise EditError("DOFbasens lokaliteter slettes ikke her")
    if (await session.execute(text("SELECT EXISTS (SELECT 1 FROM observations WHERE loknr = :l)"), {"l": loknr})).scalar():
        raise EditError("Lokaliteten har observationer – den kan lægges sammen med en anden i stedet")
    if (await session.execute(text("SELECT EXISTS (SELECT 1 FROM locations WHERE parent_loknr = :l)"), {"l": loknr})).scalar():
        raise EditError("Lokaliteten har andre lokaliteter inden i sig")
    if (await session.execute(text("SELECT EXISTS (SELECT 1 FROM locations WHERE merged_into = :l)"), {"l": loknr})).scalar():
        raise EditError("Andre lokaliteter er lagt sammen med denne")
    # Feltmeldinger ligger i brugergruppen – egen sætning (routeren sender den dertil)
    if (await session.execute(text("SELECT EXISTS (SELECT 1 FROM field_reports WHERE loknr = :l)"), {"l": loknr})).scalar():
        raise EditError("Lokaliteten bruges i feltmeldinger")
    await session.execute(text(f"UPDATE location_shapes SET valid_to = now() WHERE loknr = :l AND {CUR}"), {"l": loknr})
    await session.execute(text("DELETE FROM location_details WHERE loknr = :l"), {"l": loknr})
    await session.execute(text("DELETE FROM locations WHERE loknr = :l"), {"l": loknr})
    await _log(session, loknr, "slet", user_id, {"navn": loc["navn"], "parent_loknr": loc["parent_loknr"]}, None)
    await _done(session)
    return {"ok": True}
