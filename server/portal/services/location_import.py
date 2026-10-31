"""Import af lokaliteter fra DOFbasen (docs/lokaliteter-plan.md, trin 2).

Gangen: zip'en fra tools/dofbasen_lokaliteter/hent.py uploades i bidder (api) → workeren validerer den og lægger alle
lokaliteter i staging (location_import_rows) → en ændringsrapport viser, hvad der er nyt, ændret, forsvundet og i konflikt
med portalens egne rettelser → admin vælger, hvad der skal anvendes. Portalens egne grænser overskrives aldrig stille.

Zip'en stoles ikke på: kun kendte filnavne, ingen symlinks, grænser for størrelse og komprimering (zip-bombe), SHA-256 fra
manifestet, og hver lokalitet valideres (tal, tekstlængder, geometri), før den bruges.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump
from portal.config import get_settings
from portal.dbgroups import OBSERVATIONS
from portal.models import LocationImport

SCHEMA = 1
MAX_UPLOAD = 2 * 1024 ** 3
MAX_MEMBERS = 120_000
MAX_MEMBER_BYTES = 64 * 1024 ** 2
MAX_TOTAL_BYTES = 6 * 1024 ** 3
MAX_RATIO = 200
MAX_POINTS = 50_000
BATCH = 500
ALLOWED = re.compile(r"^(?:manifest\.json|index\.json|typer\.json|tjek\.json|lokaliteter/\d{3}/\d{1,9}\.json"
                     r"|raw/poplok/\d{3}/\d{1,9}\.html|raw/wfs/[a-z_]{1,30}_\d{4}\.json)$")
RECORD = re.compile(r"^lokaliteter/\d{3}/(\d{1,9})\.json$")
GROUPS = ("nye_lokaliteter", "detaljer", "nye_graenser", "aendrede_graenser", "fjernede_graenser")
SAMPLE = 15


class ImportError_(Exception):
    """Zip'en eller en lokalitet kan ikke bruges (beskeden vises i admin)."""


def root() -> Path:
    p = Path(get_settings().location_import_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def zip_path(import_id: int) -> Path:
    return root() / f"{import_id}.zip"


def part_path(import_id: int) -> Path:
    return root() / f"{import_id}.zip.part"


# ---------------------------------------------------------------- zip og lokaliteter (rene funktioner – testes uden database)


def open_zip(path: Path) -> tuple[zipfile.ZipFile, dict, list[zipfile.ZipInfo]]:
    """Åbn og kontrollér zip'en. Returnerer (zip, manifest, lokalitetsfilerne)."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ImportError_("Filen er ikke en gyldig zip") from exc
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ImportError_(f"Zip'en har for mange filer ({len(infos)})")
    total = 0
    records = []
    for info in infos:
        name = info.filename
        if info.is_dir():
            continue
        if (info.external_attr >> 16) and stat.S_ISLNK(info.external_attr >> 16):
            raise ImportError_(f"Symbolsk link i zip'en: {name}")
        if not ALLOWED.match(name):
            raise ImportError_(f"Uventet fil i zip'en: {name[:120]}")
        if info.file_size > MAX_MEMBER_BYTES:
            raise ImportError_(f"{name} er for stor")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ImportError_(f"{name} er mistænkeligt stærkt komprimeret")
        total += info.file_size
        if RECORD.match(name):
            records.append(info)
    if total > MAX_TOTAL_BYTES:
        raise ImportError_("Zip'en fylder for meget udpakket")
    try:
        manifest = json.loads(zf.read("manifest.json"))
    except KeyError as exc:
        raise ImportError_("manifest.json mangler – lav zip'en med hent.py pak") from exc
    if manifest.get("schema") != SCHEMA:
        raise ImportError_(f"Ukendt skemaversion {manifest.get('schema')} (forventet {SCHEMA})")
    if not isinstance(manifest.get("filer"), dict):
        raise ImportError_("manifest.json mangler listen over filer og kontrolsummer")
    if not records:
        raise ImportError_("Zip'en indeholder ingen lokaliteter")
    return zf, manifest, records


def _num(v, lo: float, hi: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _text(v, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("tekstfelt har forkert type")
    return v[:limit]


def _texts(v, limit: int, n: int = 20) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("listefelt har forkert type")
    return [x[:limit] for x in v[:n]]


def clean_border(g) -> tuple[str | None, int, dict | None]:
    """(GeoJSON-tekst, antal punkter, DOFbasens metadata) eller (None, 0, None). ValueError ved ugyldig geometri."""
    if not g:
        return None, 0, None
    if not isinstance(g, dict) or g.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("grænsen er ikke et polygon")
    polys = [g.get("coordinates")] if g["type"] == "Polygon" else g.get("coordinates")
    if not isinstance(polys, list) or not polys:
        raise ValueError("grænsen har ingen koordinater")
    points = 0
    for poly in polys:
        if not isinstance(poly, list) or not poly:
            raise ValueError("tomt polygon")
        for ring in poly:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError("en ring har færre end 4 punkter")
            for pt in ring:
                if not (isinstance(pt, list) and len(pt) >= 2 and _num(pt[0], -180, 180) and _num(pt[1], -90, 90)):
                    raise ValueError("ugyldigt koordinat")
            points += len(ring)
    if points > MAX_POINTS:
        raise ValueError(f"grænsen har for mange punkter ({points})")
    meta = {k: g.get(k) for k in ("oprettet", "opdateret", "accepteret") if g.get(k) is not None}
    return json.dumps({"type": g["type"], "coordinates": g["coordinates"]}), points, meta or None


def clean_record(rec, loknr_from_name: int) -> dict:
    """Valider og normalisér én lokalitet fra zip'en. ValueError, hvis den ikke kan bruges."""
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        raise ValueError("forkert skema")
    loknr = rec.get("loknr")
    if not isinstance(loknr, int) or isinstance(loknr, bool) or loknr != loknr_from_name or not 0 < loknr < 100_000_000:
        raise ValueError("loknr passer ikke med filnavnet")
    lokid = rec.get("lokid")
    if lokid is not None and (not isinstance(lokid, int) or isinstance(lokid, bool)):
        raise ValueError("lokid er ikke et tal")
    navn = _text(rec.get("navn"), 250)
    if not navn:
        raise ValueError("navn mangler")
    c = rec.get("center") or {}
    center = {"lat": c["lat"], "lon": c["lon"]} if _num(c.get("lat"), -90, 90) and _num(c.get("lon"), -180, 180) else None
    iba = rec.get("iba") or {}
    dofb = rec.get("dofbasen") or {}
    felter = rec.get("felter") or {}
    if not isinstance(felter, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in felter.items()):
        raise ValueError("felter har forkert type")
    geojson, points, meta = clean_border(rec.get("graense"))
    hentet = rec.get("hentet") or {}
    return {
        "loknr": loknr, "lokid": lokid, "navn": navn, "aktiv": rec.get("aktiv") is not False,
        "vis": rec.get("vis") if isinstance(rec.get("vis"), int) else None, "center": center,
        "kommuner": _texts(rec.get("kommuner"), 80), "afdelinger": _texts(rec.get("lokalafdelinger"), 120),
        "region": _text(rec.get("region"), 80), "typer_tekst": _text(rec.get("typer_tekst"), 300),
        "beskrivelse": _text(rec.get("beskrivelse"), 20_000),
        "iba_id": iba.get("id") if isinstance(iba, dict) and isinstance(iba.get("id"), int) else None,
        "iba_navn": _text(iba.get("navn"), 200) if isinstance(iba, dict) else None,
        "dofbasen": {k: v for k, v in dofb.items() if k in ("arter", "obsdage", "observationer") and (v is None or isinstance(v, int))}
        if isinstance(dofb, dict) else None,
        "felter": {k[:120]: v[:20_000] for k, v in list(felter.items())[:40]},
        "fetched_at": _text(hentet.get("side") if isinstance(hentet, dict) else None, 40),
        "har_side": bool(felter),
        "_geojson": geojson, "_punkter": points, "_meta": meta,
    }


def read_records(path: Path, progress=None) -> tuple[dict, list[dict], list[list]]:
    """Læs og valider alle lokaliteter (kører i en tråd). Returnerer (manifest, lokaliteter, afviste [loknr/fil, grund])."""
    zf, manifest, infos = open_zip(path)
    sums = manifest["filer"]
    out, rejected = [], []
    with zf:
        for i, info in enumerate(infos):
            data = zf.read(info)
            want = sums.get(info.filename)
            if want is None or hashlib.sha256(data).hexdigest() != want:
                rejected.append([info.filename, "kontrolsummen passer ikke med manifestet"])
                continue
            try:
                out.append(clean_record(json.loads(data), int(RECORD.match(info.filename).group(1))))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                rejected.append([info.filename, str(exc)[:200]])
            if progress and i % 2000 == 0:
                progress(i, len(infos))
    return manifest, out, rejected


# ---------------------------------------------------------------- upload (i bidder, i rækkefølge)


async def create(session: AsyncSession, user_id: int, filename: str, size: int) -> LocationImport:
    if not filename.lower().endswith(".zip"):
        raise ImportError_("Vælg zip-filen fra hent.py pak")
    if not 0 < size <= MAX_UPLOAD:
        raise ImportError_("Filen har en ugyldig størrelse (højst 2 GB)")
    imp = LocationImport(filename=filename[:300], size=size, status="uploader", uploaded_by=user_id)
    session.add(imp)
    await session.commit()
    part_path(imp.id).unlink(missing_ok=True)
    return imp


async def write_chunk(imp: LocationImport, offset: int, stream) -> int:
    """Skriv næste bid. Bidderne skal komme i rækkefølge; ved et afbrud fortsættes fra den størrelse, serveren svarer."""
    if imp.status != "uploader":
        raise ImportError_("Importen er ikke længere i upload-fasen")
    part = part_path(imp.id)
    have = part.stat().st_size if part.exists() else 0
    if offset != have:
        raise ImportError_(f"Serveren har {have} bytes")
    size = have
    with part.open("ab") as fh:
        async for chunk in stream:
            size += len(chunk)
            if size > imp.size:
                raise ImportError_("Uploaden er større end angivet")
            fh.write(chunk)
    return size


async def finish(session: AsyncSession, imp: LocationImport) -> None:
    part = part_path(imp.id)
    if not part.exists() or part.stat().st_size != imp.size:
        raise ImportError_("Uploaden er ikke færdig")
    try:
        await asyncio.to_thread(open_zip, part)  # hurtig kontrol, før workeren får den
    except ImportError_:
        part.unlink(missing_ok=True)
        raise
    part.replace(zip_path(imp.id))
    imp.status = "validerer"
    await session.commit()


# ---------------------------------------------------------------- staging og rapport (workeren)

_INSERT = text("""
    INSERT INTO location_import_rows (import_id, loknr, data, geom, punkter, dof_meta)
    VALUES (:i, :l, CAST(:d AS jsonb),
            CASE WHEN CAST(:g AS text) IS NULL THEN NULL ELSE ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:g AS text)), 4326)) END,
            :p, CAST(:m AS jsonb))
""")

# Den gældende grænse og den seneste DOFbasen-version pr. lokalitet (portalens rettelser sammenlignes med den)
_CUR = """
    cur AS (SELECT loknr, source, geom FROM location_shapes WHERE valid_to IS NULL AND status = 'aktiv'),
    lastdof AS (SELECT DISTINCT ON (loknr) loknr, geom FROM location_shapes WHERE source = 'dofbasen' ORDER BY loknr, id DESC)
"""
_SAME = "ST_AsBinary({a}) = ST_AsBinary({b})"
# Kategorier for staging-rækker med grænse (r = staging, c = gældende, d = seneste DOFbasen-version)
_SHAPE_KIND = f"""CASE
    WHEN c.loknr IS NULL THEN 'ny'
    WHEN c.source = 'dofbasen' AND {_SAME.format(a='c.geom', b='r.geom')} THEN 'uaendret'
    WHEN c.source = 'dofbasen' THEN 'aendret'
    WHEN d.loknr IS NOT NULL AND {_SAME.format(a='d.geom', b='r.geom')} THEN 'portal'
    ELSE 'konflikt' END"""
_DETAIL_COLS = ("lokid", "navn", "aktiv", "vis", "typer_tekst", "beskrivelse", "region", "kommuner", "afdelinger", "iba_id",
                "iba_navn", "dofbasen", "felter")


def _detail_json(alias: str) -> str:
    """De sammenlignelige detaljefelter som jsonb (staging: data-kolonnen; location_details: kolonnerne)."""
    if alias == "r":
        return "jsonb_build_object(" + ", ".join(f"'{c}', r.data->'{c}'" for c in _DETAIL_COLS) + ")"
    return "jsonb_build_object(" + ", ".join(f"'{c}', to_jsonb(x.{c})" for c in _DETAIL_COLS) + ")"


async def _samples(session: AsyncSession, sql: str, args: dict) -> list[dict]:
    return [dict(r) for r in (await session.execute(text(sql + f" LIMIT {SAMPLE}"), args)).mappings()]


async def classify(session: AsyncSession, import_id: int) -> None:
    """Sæt hver grænses kategori mod portalens gældende grænse én gang (rapport og Anvend læser den)."""
    await session.execute(text(f"""
        WITH {_CUR},
        k AS (SELECT r.loknr, {_SHAPE_KIND} AS kind FROM location_import_rows r LEFT JOIN cur c USING (loknr)
              LEFT JOIN lastdof d USING (loknr) WHERE r.import_id = :i AND r.geom IS NOT NULL)
        UPDATE location_import_rows t SET kind = k.kind FROM k WHERE t.import_id = :i AND t.loknr = k.loknr"""), {"i": import_id})


async def build_report(session: AsyncSession, import_id: int) -> dict:
    a = {"i": import_id}
    base = "WITH rr AS (SELECT r.* FROM location_import_rows r WHERE r.import_id = :i AND r.geom IS NOT NULL)"
    shapes = dict((await session.execute(text(f"{base} SELECT kind, count(*) FROM rr GROUP BY kind"), a)).all())
    counts = (await session.execute(text(f"""
        SELECT count(*) AS lokaliteter, count(*) FILTER (WHERE r.geom IS NOT NULL) AS med_graense,
               count(*) FILTER (WHERE (r.data->>'har_side')::boolean) AS med_side,
               count(*) FILTER (WHERE l.loknr IS NULL) AS nye_lokaliteter,
               count(*) FILTER (WHERE l.loknr IS NOT NULL AND l.navn IS DISTINCT FROM r.data->>'navn') AS andet_navn,
               count(*) FILTER (WHERE x.loknr IS NULL AND (r.data->>'har_side')::boolean) AS nye_detaljer,
               count(*) FILTER (WHERE x.loknr IS NOT NULL AND (r.data->>'har_side')::boolean
                                AND {_detail_json('x')} IS DISTINCT FROM {_detail_json('r')}) AS aendrede_detaljer
        FROM location_import_rows r LEFT JOIN locations l USING (loknr) LEFT JOIN location_details x USING (loknr)
        WHERE r.import_id = :i"""), a)).mappings().first()
    removed = (await session.execute(text(f"""
        WITH {_CUR} SELECT count(*) FROM cur c WHERE c.source = 'dofbasen'
          AND NOT EXISTS (SELECT 1 FROM location_import_rows r WHERE r.import_id = :i AND r.loknr = c.loknr AND r.geom IS NOT NULL)"""), a)).scalar()
    name = "coalesce(l.navn, r.data->>'navn') AS navn"
    samples = {
        "nye_lokaliteter": await _samples(session, """SELECT r.loknr, r.data->>'navn' AS navn FROM location_import_rows r
            LEFT JOIN locations l USING (loknr) WHERE r.import_id = :i AND l.loknr IS NULL ORDER BY r.loknr""", a),
        "andet_navn": await _samples(session, """SELECT r.loknr, l.navn AS portal, r.data->>'navn' AS dofbasen FROM location_import_rows r
            JOIN locations l USING (loknr) WHERE r.import_id = :i AND l.navn IS DISTINCT FROM r.data->>'navn' ORDER BY r.loknr""", a),
        **{k: await _samples(session, f"""{base} SELECT rr.loknr, {name.replace('r.data', 'rr.data')}, rr.punkter FROM rr
            LEFT JOIN locations l USING (loknr) WHERE rr.kind = :k ORDER BY rr.loknr""", {**a, "k": k})
           for k in ("ny", "aendret", "konflikt")},
        "fjernede_graenser": await _samples(session, f"""WITH {_CUR} SELECT c.loknr, l.navn FROM cur c LEFT JOIN locations l USING (loknr)
            WHERE c.source = 'dofbasen' AND NOT EXISTS (SELECT 1 FROM location_import_rows r WHERE r.import_id = :i AND r.loknr = c.loknr
              AND r.geom IS NOT NULL) ORDER BY c.loknr""", a),
    }
    return {**dict(counts), "graenser": {"nye": shapes.get("ny", 0), "aendrede": shapes.get("aendret", 0),
                                         "uaendrede": shapes.get("uaendret", 0), "konflikter": shapes.get("konflikt", 0),
                                         "portalrettelser_bevares": shapes.get("portal", 0), "fjernede": removed},
            "eksempler": samples}


async def stage(session: AsyncSession, import_id: int) -> dict:
    """Valider zip'en, læg lokaliteterne i staging og byg ændringsrapporten (workeren)."""
    imp = await session.get(LocationImport, import_id)
    if imp is None or imp.status != "validerer":
        return {}
    try:
        await jobstatus.step("Læser og validerer zip'en", 0, 3)
        manifest, rows, rejected = await asyncio.to_thread(read_records, zip_path(import_id))
        await session.execute(text("DELETE FROM location_import_rows WHERE import_id = :i"), {"i": import_id})
        for n in range(0, len(rows), BATCH):
            await jobstatus.step(f"Indlæser lokaliteter ({n}/{len(rows)})", 1, 3)
            await session.execute(_INSERT, [{"i": import_id, "l": r["loknr"], "p": r["_punkter"], "g": r["_geojson"],
                                             "m": json.dumps(r["_meta"]) if r["_meta"] else None,
                                             "d": json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False)}
                                            for r in rows[n:n + BATCH]])
        # Ugyldige polygoner (fx selvskæring) repareres; de tælles i rapporten
        repaired = (await session.execute(text("""
            UPDATE location_import_rows SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
            WHERE import_id = :i AND geom IS NOT NULL AND NOT ST_IsValid(geom)"""), {"i": import_id})).rowcount
        await jobstatus.step("Sammenligner med portalens lokaliteter", 2, 3)
        # Statistik for den netop fyldte tabel – ellers tror planlæggeren, den har 1 række, og sammenligningen bliver
        # en nested loop over 20.000 × 20.000 grænser (minutter i stedet for sekunder)
        await session.execute(text("ANALYZE location_import_rows"))
        await classify(session, import_id)
        report = await build_report(session, import_id)
        imp.manifest = {k: v for k, v in manifest.items() if k != "filer"}
        imp.report = {**report, "afviste": len(rejected), "afviste_eksempler": rejected[:SAMPLE], "repareret": repaired}
        imp.status, imp.error = "klar", None
        await session.commit()
        return imp.report
    except Exception as exc:
        await session.rollback()
        imp = await session.get(LocationImport, import_id)
        imp.status, imp.error = "fejl", str(exc).splitlines()[0][:500] if str(exc) else type(exc).__name__
        await session.commit()
        raise


# ---------------------------------------------------------------- anvend


async def apply(session: AsyncSession, import_id: int, groups: list[str], conflicts: str) -> dict:
    """Anvend de valgte grupper i én transaktion. conflicts: 'behold' (portalens grænse beholdes; DOFbasens gemmes som
    historik) eller 'dofbasen' (DOFbasens grænse bliver gældende)."""
    session.info["home"] = OBSERVATIONS  # locations/kommuner er referencedata: skrives i observationsgruppen (se dbrouter)
    imp = await session.get(LocationImport, import_id)
    if imp is None or imp.status != "anvender":
        return {}
    a = {"i": import_id}
    done: dict[str, int] = {}
    try:
        if "nye_lokaliteter" in groups:
            await jobstatus.step("Nye lokaliteter", 0, 4)
            new = (await session.execute(text("""
                INSERT INTO locations (loknr, navn, afdeling_id, lat, lon, geom)
                SELECT r.loknr, r.data->>'navn',
                       (SELECT a.id FROM afdelinger a WHERE a.navn = r.data->'afdelinger'->>0 LIMIT 1),
                       (r.data->'center'->>'lat')::float, (r.data->'center'->>'lon')::float,
                       CASE WHEN r.data->'center' IS NOT NULL AND r.data->'center' <> 'null'::jsonb
                            THEN ST_SetSRID(ST_MakePoint((r.data->'center'->>'lon')::float, (r.data->'center'->>'lat')::float), 4326) END
                FROM location_import_rows r WHERE r.import_id = :i AND NOT EXISTS (SELECT 1 FROM locations l WHERE l.loknr = r.loknr)
                ON CONFLICT (loknr) DO NOTHING RETURNING loknr"""), a)).scalars().all()
            if new:
                from portal.dof.ingest import assign_kommuner  # her for ikke at trække hele indlæsningen ind ved import

                await assign_kommuner(session, list(new))
            done["nye_lokaliteter"] = len(new)
        if "detaljer" in groups:
            await jobstatus.step("Detaljer (typer, beskrivelser, kommuner, IBA)", 1, 4)
            # Portalens egne detaljer (source=portal) overskrives ikke; rowcount tæller nye og opdaterede
            done["detaljer"] = (await session.execute(text("""
                INSERT INTO location_details (loknr, lokid, navn, aktiv, vis, typer_tekst, beskrivelse, region, kommuner, afdelinger,
                                              iba_id, iba_navn, dofbasen, felter, source, import_id, fetched_at, updated_at)
                SELECT r.loknr, (r.data->>'lokid')::int, r.data->>'navn', (r.data->>'aktiv')::boolean, (r.data->>'vis')::smallint,
                       r.data->>'typer_tekst', r.data->>'beskrivelse', r.data->>'region',
                       ARRAY(SELECT jsonb_array_elements_text(r.data->'kommuner')), ARRAY(SELECT jsonb_array_elements_text(r.data->'afdelinger')),
                       (r.data->>'iba_id')::int, r.data->>'iba_navn', r.data->'dofbasen', r.data->'felter', 'dofbasen', :i,
                       (r.data->>'fetched_at')::timestamptz, now()
                FROM location_import_rows r WHERE r.import_id = :i AND (r.data->>'har_side')::boolean
                ON CONFLICT (loknr) DO UPDATE SET lokid = EXCLUDED.lokid, navn = EXCLUDED.navn, aktiv = EXCLUDED.aktiv, vis = EXCLUDED.vis,
                    typer_tekst = EXCLUDED.typer_tekst, beskrivelse = EXCLUDED.beskrivelse, region = EXCLUDED.region,
                    kommuner = EXCLUDED.kommuner, afdelinger = EXCLUDED.afdelinger, iba_id = EXCLUDED.iba_id, iba_navn = EXCLUDED.iba_navn,
                    dofbasen = EXCLUDED.dofbasen, felter = EXCLUDED.felter, import_id = EXCLUDED.import_id,
                    fetched_at = EXCLUDED.fetched_at, updated_at = now()
                WHERE location_details.source = 'dofbasen'"""), a)).rowcount
        await jobstatus.step("Grænser", 2, 4)
        kinds = {"nye_graenser": ["ny"], "aendrede_graenser": ["aendret"]}
        take = [k for g, ks in kinds.items() if g in groups for k in ks] + (["konflikt"] if conflicts == "dofbasen" else [])
        sel = "WITH rr AS (SELECT r.loknr, r.kind FROM location_import_rows r WHERE r.import_id = :i AND r.geom IS NOT NULL)"
        current = list((await session.execute(text(f"{sel} SELECT loknr FROM rr WHERE kind = ANY(:k)"), {**a, "k": take})).scalars()) if take else []
        history = list((await session.execute(text(f"{sel} SELECT loknr FROM rr WHERE kind = 'konflikt'"), a)).scalars()) \
            if conflicts == "behold" else []
        if current:
            await session.execute(text("""UPDATE location_shapes SET valid_to = now()
                WHERE valid_to IS NULL AND status = 'aktiv' AND loknr = ANY(:l)"""), {"l": current})
            await session.execute(text("""INSERT INTO location_shapes (loknr, geom, punkter, source, status, dof_meta, import_id)
                SELECT loknr, geom, punkter, 'dofbasen', 'aktiv', dof_meta, :i FROM location_import_rows
                WHERE import_id = :i AND loknr = ANY(:l)"""), {**a, "l": current})
        if history:  # portalens grænse beholdes; DOFbasens nye version gemmes som afsluttet historik (til næste sammenligning)
            await session.execute(text("""INSERT INTO location_shapes (loknr, geom, punkter, source, status, dof_meta, import_id, valid_to, note)
                SELECT loknr, geom, punkter, 'dofbasen', 'aktiv', dof_meta, :i, now(), 'DOFbasens version – portalens grænse beholdt'
                FROM location_import_rows WHERE import_id = :i AND loknr = ANY(:l)"""), {**a, "l": history})
        done["graenser"], done["konflikter_beholdt"] = len(current), len(history)
        if "fjernede_graenser" in groups:
            await jobstatus.step("Fjernede grænser", 3, 4)
            done["fjernede_graenser"] = (await session.execute(text("""
                UPDATE location_shapes s SET valid_to = now(), note = 'Ikke længere hos DOFbasen'
                WHERE s.source = 'dofbasen' AND s.valid_to IS NULL AND s.status = 'aktiv'
                  AND NOT EXISTS (SELECT 1 FROM location_import_rows r WHERE r.import_id = :i AND r.loknr = s.loknr AND r.geom IS NOT NULL)"""),
                a)).rowcount
        imp.status, imp.applied, imp.applied_at, imp.error = "anvendt", done, datetime.now(timezone.utc), None
        await session.execute(text("DELETE FROM location_import_rows WHERE import_id = :i"), a)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        imp = await session.get(LocationImport, import_id)
        imp.status, imp.error = "klar", f"Anvend fejlede: {str(exc).splitlines()[0][:400]}"
        await session.commit()
        raise
    zip_path(import_id).unlink(missing_ok=True)
    await session.execute(text("ANALYZE location_shapes"))  # ny statistik efter mange nye grænseversioner
    await session.commit()
    await bump("locations")
    return done


async def discard(session: AsyncSession, imp: LocationImport) -> None:
    """Slet en import, der ikke er anvendt (staging og fil)."""
    await session.execute(text("DELETE FROM location_import_rows WHERE import_id = :i"), {"i": imp.id})
    await session.delete(imp)
    await session.commit()
    zip_path(imp.id).unlink(missing_ok=True)
    part_path(imp.id).unlink(missing_ok=True)


async def list_imports(session: AsyncSession, limit: int = 20) -> list[LocationImport]:
    return list((await session.execute(select(LocationImport).order_by(LocationImport.id.desc()).limit(limit))).scalars())


def out(imp: LocationImport) -> dict:
    return {"id": imp.id, "filename": imp.filename, "size": imp.size, "status": imp.status, "manifest": imp.manifest,
            "report": imp.report, "applied": imp.applied, "error": imp.error,
            "created_at": imp.created_at.isoformat() if imp.created_at else None,
            "applied_at": imp.applied_at.isoformat() if imp.applied_at else None}
