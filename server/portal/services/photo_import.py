"""Import af fotoarkivet fra netfugl.dk (docs/netfugl-afloesning-spec.md §4).

Kilden er en mappe på serveren (``photo_import_dir``/<undermappe>, med ``netfugl-fotos/<id>/{meta.json,foto.*}``) eller en eller flere zip-filer
(``Fotos-*.zip``). Importen gennemløber fotomapperne i id-orden, én transaktion pr. foto, og kan køres igen og igen: et foto genkendes på
``netfugl_id``, og et fingeraftryk (sha256 af meta.json) afgør, om der er noget nyt. Fotos der er rettet i DOF.tool (``locally_edited``)
overskrives ikke, medmindre importen beder om det. Importen sender aldrig notifikationer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump, worker_command
from portal.config import get_settings
from portal.db import SessionLocal
from portal.models import GalBird, GalComment, GalCountry, GalGroup, GalImport, GalPhoto
from portal.services import article_import, content_media, content_render, imagemeta, photo_codes, photo_photographers
from portal.timeutil import TZ

log = logging.getLogger("portal.photos.import")
ImportAbort = article_import.ImportAbort
MAX_LOG = 2000
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".tif", ".tiff", ".bmp")  # alt gemmes som WebP
MEMBER = re.compile(r"^(?:.*/)?(\d+)/(meta\.json|foto\.(?:jpg|jpeg|png|webp|gif|avif|tiff?|bmp))$", re.IGNORECASE)
TAGS = photo_codes.TAGS
LOK_KM = 5.0


def prepare_photo_image(data: bytes) -> tuple[dict, content_media.Prepared]:
    """Det CPU-tunge ved et foto (EXIF, WebP-konvertering, dekodning til varianter). Synkron, så den kan køre i en tråd."""
    prepared = content_media.prepare_webp(data)
    variants = prepared.make_variants()  # også varianterne laves her i puljen – ellers kører de bagefter ét ad gangen i store_image
    prepared.make_variants = lambda: variants
    return imagemeta.read_camera(data), prepared


# ---------------------------------------------------------------- kilder

class Source:
    """ids() → sorterede foto-id'er; meta(id) → bytes; photo(id, navn) → bytes; names(id) → filnavne i fotomappen."""

    rejected: list[str]

    def ids(self) -> list[int]: ...
    def names(self, pid: int) -> list[str]: ...
    def read(self, pid: int, name: str) -> bytes: ...
    def fingerprint_extra(self, pid: int) -> str:
        return ""


class DirSource(Source):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.rejected = []
        self._ids = sorted(int(p.name) for p in self.root.iterdir() if p.is_dir() and p.name.isdigit() and (p / "meta.json").is_file())

    def ids(self):
        return self._ids

    def _dir(self, pid: int) -> Path:
        d = (self.root / str(pid)).resolve()
        if not d.is_relative_to(self.root) or d.is_symlink():
            raise ImportAbort(f"Usikker sti for foto {pid}")
        return d

    def names(self, pid):
        return sorted(p.name for p in self._dir(pid).iterdir() if p.is_file() and not p.is_symlink() and (p.name == "meta.json" or p.name.startswith("foto.")))

    def read(self, pid, name):
        p = (self._dir(pid) / name).resolve()
        if not p.is_relative_to(self.root) or p.is_symlink() or not p.is_file():
            raise ImportAbort(f"Usikker fil {pid}/{name}")
        if p.stat().st_size > article_import.MAX_MEMBER_BYTES:
            raise ImportAbort(f"{pid}/{name} er for stor")
        return p.read_bytes()


class ZipSource(Source):
    def __init__(self, paths: list[Path]):
        self.zips: list[zipfile.ZipFile] = []
        self.members: dict[int, dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]]] = {}
        self.rejected = []
        for path in paths:
            try:
                zf = zipfile.ZipFile(path)
            except zipfile.BadZipFile as exc:
                raise ImportAbort(f"{path.name} er ikke en gyldig zip") from exc
            self.zips.append(zf)
            infos = zf.infolist()
            if len(infos) > article_import.MAX_MEMBERS:
                raise ImportAbort(f"{path.name} har for mange filer")
            for info in infos:
                if info.is_dir():
                    continue
                norm = article_import.safe_name(info.filename)
                if norm is None or article_import.is_symlink(info) or info.file_size > article_import.MAX_MEMBER_BYTES:
                    self.rejected.append(info.filename)
                    continue
                m = MEMBER.match(norm)
                if m:
                    self.members.setdefault(int(m.group(1)), {})[m.group(2)] = (zf, info)
        self._ids = sorted(i for i, mm in self.members.items() if "meta.json" in mm)

    def ids(self):
        return self._ids

    def names(self, pid):
        return sorted(self.members[pid])

    def read(self, pid, name):
        zf, info = self.members[pid][name]
        return article_import.read_member(zf, info)


def find_source(rel: str | None, import_dir: Path | None = None) -> Source:
    """Kilden for en sti under photo_import_dir (mappe eller zip-fil)."""
    base_root = (import_dir or Path(get_settings().photo_import_dir)).resolve()
    base = (base_root / (rel or "")).resolve()
    if not base.is_relative_to(base_root):
        raise ImportAbort("Stien ligger uden for importmappen")
    if base.is_file() and base.suffix.lower() == ".zip":
        return ZipSource([base])
    if not base.is_dir():
        raise ImportAbort(f"Findes ikke: {rel}")
    if (base / "netfugl-fotos").is_dir():
        return DirSource(base / "netfugl-fotos")
    if any(p.is_dir() and p.name.isdigit() for p in base.iterdir()):
        return DirSource(base)
    zips = sorted(p for p in base.glob("*.zip") if p.is_file())
    if zips:
        return ZipSource(zips)
    raise ImportAbort("Fandt hverken netfugl-fotos/, fotomapper eller zip-filer i mappen")


def list_folders(import_dir: Path | None = None) -> list[dict]:
    """Undermapper (og zip-filer) i importmappen, til valget i admin."""
    root = (import_dir or Path(get_settings().photo_import_dir))
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.name == "uploads":
            continue
        if p.is_dir():
            zips = list(p.glob("*.zip"))
            n = sum(1 for q in (p / "netfugl-fotos" if (p / "netfugl-fotos").is_dir() else p).iterdir() if q.is_dir() and q.name.isdigit()) \
                if (p / "netfugl-fotos").is_dir() or any(q.is_dir() and q.name.isdigit() for q in p.iterdir()) else 0
            out.append({"sti": p.name, "slags": "mappe", "fotos": n, "zip": len(zips)})
        elif p.suffix.lower() == ".zip":
            out.append({"sti": p.name, "slags": "zip", "fotos": None, "zip": 1})
    return out


# ---------------------------------------------------------------- kørsel

@dataclass
class Run:
    stats: dict[str, int] = field(default_factory=lambda: {
        "fotos_i_kilden": 0, "oprettet": 0, "opdateret": 0, "uaendret": 0, "sprunget_lokalt": 0, "fejl": 0, "billeder_genbrugt": 0, "billeder_erstattet": 0,
        "fugle_uden_artnr": 0, "ukendte_koder": 0, "kommentarer": 0, "afvist": 0, "fotografer": 0, "grupper": 0})
    log: list[dict] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    codes: photo_codes.Codes | None = None
    species_latin: dict[str, int] = field(default_factory=dict)
    species_navn: dict[str, int] = field(default_factory=dict)
    photographers: dict[int, int] = field(default_factory=dict)
    countries: dict[tuple, int] = field(default_factory=dict)
    loks: dict[str, list[tuple[int, float | None, float | None]]] = field(default_factory=dict)
    groups: list[list[int]] = field(default_factory=list)
    prep: dict[int, asyncio.Future] = field(default_factory=dict)  # forberedte billeder (prepare_photo_image) for fotos længere fremme

    def snapshot(self) -> tuple:
        """Opslagene, der kan pege på rækker oprettet i det aktuelle fotos transaktion (fotografer, lande, koder, grupper)."""
        c = self.codes
        return (dict(self.photographers), dict(self.countries), len(self.groups),
                (dict(c.by_text), set(c.used), len(c.created)) if c else None)

    def restore(self, snap: tuple) -> None:
        """Efter rollback: glem det, transaktionen oprettede – ellers peger næste foto på en fotograf, der ikke findes."""
        self.photographers, self.countries = snap[0], snap[1]
        del self.groups[snap[2]:]
        if snap[3] and self.codes:
            self.codes.by_text, self.codes.used = snap[3][0], snap[3][1]
            del self.codes.created[snap[3][2]:]

    def note(self, level: str, msg: str, no: int | None = None) -> None:
        if len(self.log) < MAX_LOG:
            self.log.append({"netfugl_id": no, "niveau": level, "besked": msg})
        elif len(self.log) == MAX_LOG:
            self.log.append({"netfugl_id": None, "niveau": "info", "besked": "Loggen er afkortet"})


async def _load_species(session: AsyncSession, run: Run) -> None:
    rows = (await session.execute(text("SELECT artnr, navn, latin, art_type FROM species WHERE NOT excluded AND art_type IN ('art', 'underart') "
                                       "ORDER BY (art_type = 'art') DESC, sortering NULLS LAST"))).all()
    for artnr, navn, latin, _ in rows:
        if latin:
            run.species_latin.setdefault(latin.casefold().strip(), artnr)
        run.species_navn.setdefault((navn or "").casefold().strip(), artnr)


def find_artnr(run: Run, art: dict, underart: dict | None) -> int | None:
    latin = (art.get("latin") or "").casefold().strip()
    if underart and underart.get("latin") and latin:
        if (n := run.species_latin.get(f"{latin} {underart['latin'].casefold().strip()}")) is not None:
            return n
    if latin and (n := run.species_latin.get(latin)) is not None:
        return n
    navn = re.sub(r"\s*\(ssp\..*\)\s*$", "", art.get("dansk") or "").casefold().strip()
    return run.species_navn.get(navn)


async def _country(session: AsyncSession, run: Run, land: dict | None) -> int | None:
    if not land or not land.get("navn"):
        return None
    key = (land.get("netfugl_id"), land["navn"])
    if key in run.countries:
        return run.countries[key]
    q = select(GalCountry)
    q = q.where(GalCountry.netfugl_id == land["netfugl_id"]) if land.get("netfugl_id") is not None else q.where(GalCountry.navn == land["navn"])
    c = (await session.execute(q.limit(1))).scalar_one_or_none()
    if c is None:
        c = GalCountry(navn=land["navn"][:120], netfugl_id=land.get("netfugl_id"), vp="indenfor vp" in land["navn"].casefold())
        session.add(c)
        await session.flush()
    run.countries[key] = c.id
    return c.id


def _km(a_lat, a_lon, b_lat, b_lon) -> float:
    dy = (a_lat - b_lat) * 111.2
    dx = (a_lon - b_lon) * 111.2 * math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot(dx, dy)


async def find_loknr(session: AsyncSession, run: Run, navn: str | None, coord: dict | None) -> int | None:
    """DOFbasen-lokalitet med præcis samme navn (og, hvis koordinat findes, inden for 5 km); ellers None."""
    if not navn:
        return None
    key = re.sub(r"\s+", " ", navn.casefold()).strip()
    if key not in run.loks:
        run.loks[key] = [(r[0], r[1], r[2]) for r in (await session.execute(text(
            "SELECT loknr, lat, lon FROM locations WHERE lower(navn) = :n AND merged_into IS NULL"), {"n": key})).all()]
    cands = run.loks[key]
    if coord:
        near = [(c[0], _km(coord["lat"], coord["lon"], c[1], c[2])) for c in cands if c[1] is not None and c[2] is not None]
        near = [x for x in near if x[1] <= LOK_KM]
        return min(near, key=lambda x: x[1])[0] if near else None
    return cands[0][0] if len(cands) == 1 else None


def iso_date(v: str | None) -> date | None:
    try:
        return date.fromisoformat((v or "")[:10])
    except ValueError:
        return None


def iso_dt(v: str | None) -> datetime | None:
    d = iso_date(v)
    return datetime(d.year, d.month, d.day, 12, tzinfo=TZ) if d else None


def _clean_html(html: str | None) -> tuple[str | None, str | None]:
    """(ren tekst, sanitiseret HTML) af en beskrivelse fra arkivet."""
    if not html or not html.strip():
        return None, None
    safe = content_render.sanitize(html, "kommentar", set())
    plain = content_render.to_text(safe).strip()
    return (plain or None), (safe or None)


async def _import_comments(session: AsyncSession, photo: GalPhoto, comments: list[dict], run: Run) -> int:
    flat = article_import.flatten_comments(comments)
    existing = {c.archive_key: c for c in (await session.execute(select(GalComment).where(GalComment.photo_id == photo.id, GalComment.archive_key.isnot(None)))).scalars()}
    seen: set[str] = set()
    ids: dict[int, int] = {}
    for c, top in flat:
        key = f"nf:{c.get('id')}" if c.get("id") is not None else "nfh:" + article_import.comment_key(photo.netfugl_id or 0, c.get("forfatter") or "", c.get("dato") or "", c.get("tekst_html") or "", 0)[:30]
        seen.add(key)
        parent_id = ids.get(id(top)) if top is not None else None
        row = existing.get(key)
        if row is None:
            safe = content_render.sanitize(c.get("tekst_html") or "", "kommentar", set())  # arkivets kommentarer er HTML, ikke markdown
            body = content_render.to_text(safe).strip()
            row = GalComment(photo_id=photo.id, parent_id=parent_id, user_id=None, archive_author=(c.get("forfatter") or "Ukendt")[:160],
                             archive_netfugl_user=c.get("forfatter_id"), archive_key=key, body_md=body,
                             body_html=safe, created_at=article_import.parse_dato(c.get("dato"), photo.published_at))
            session.add(row)
            await session.flush()
            run.stats["kommentarer"] += 1
        ids[id(c)] = row.id
    for key, row in existing.items():
        if key not in seen and row.user_id is None:
            await session.delete(row)
    await session.flush()
    return (await session.execute(text("SELECT count(*) FROM gal_comments WHERE photo_id = :p AND status = 'synlig' AND NOT intern"), {"p": photo.id})).scalar() or 0


def _strip_nul(v):
    if isinstance(v, str):
        return v.replace("\x00", "")
    if isinstance(v, dict):
        return {k: _strip_nul(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_strip_nul(x) for x in v]
    return v


async def import_photo(session: AsyncSession, run: Run, src: Source, pid: int) -> str:
    """Importér ét foto. Returnerer 'oprettet', 'opdateret', 'uaendret' eller 'sprunget_lokalt'."""
    opts = run.options
    raw = src.read(pid, "meta.json")
    meta = _strip_nul(json.loads(raw))  # fx kameramodeller fra EXIF fyldt op med NUL – Postgres afviser \u0000 i tekst og JSON
    if meta.get("schema_version") != 1:
        raise ValueError(f"schema_version {meta.get('schema_version')} understøttes ikke")
    if int(meta.get("id", -1)) != pid:
        raise ValueError(f"meta.json har id {meta.get('id')}, men mappen hedder {pid}")
    if not meta.get("fugle"):
        raise ValueError("meta.json har ingen fugle")
    fp = hashlib.sha256(raw).hexdigest()
    photo = (await session.execute(select(GalPhoto).where(GalPhoto.netfugl_id == pid))).scalar_one_or_none()
    if photo is not None and photo.source_hash == fp and not opts.get("force"):
        return "uaendret"
    if photo is not None and photo.locally_edited and not opts.get("overwrite_local"):
        return "sprunget_lokalt"
    if opts.get("dry_run"):
        return "opdateret" if photo is not None else "oprettet"
    created = photo is None
    if created:
        photo = GalPhoto(netfugl_id=pid, source="netfugl", status="publiceret")
        session.add(photo)
    # fotograf
    f = meta.get("fotograf") or {}
    nf_user = f.get("netfugl_bruger_id")
    if nf_user is not None and nf_user in run.photographers:
        photo.photographer_id = run.photographers[nf_user]
    else:
        ph = await photo_photographers.find_or_create(session, f.get("navn") or "Ukendt fotograf", netfugl_bruger_id=nf_user)
        photo.photographer_id = ph.id
        if nf_user is not None:
            if nf_user not in run.photographers:
                run.stats["fotografer"] += 1
            run.photographers[nf_user] = ph.id
        photo.user_id = ph.user_id
    plain, safe = _clean_html(meta.get("beskrivelse_html"))
    koord = meta.get("koordinat")
    photo.titel = (meta.get("titel") or None) and meta["titel"][:200]
    photo.beskrivelse_md, photo.beskrivelse_html = plain, safe
    photo.dato = iso_date(meta.get("dato"))
    photo.land_id = await _country(session, run, meta.get("land"))
    photo.lokalitet = (meta.get("lokalitet") or None) and meta["lokalitet"][:250]
    photo.loknr = await find_loknr(session, run, photo.lokalitet, koord)
    photo.lat, photo.lon = (koord["lat"], koord["lon"]) if koord else (None, None)
    photo.tags = sorted({TAGS[t] for t in meta.get("emneord") or [] if t in TAGS})
    kam = dict(meta.get("kamera") or {})
    rett = meta.get("rettighed") or {}
    photo.rettighed = rett.get("id") if rett.get("id") in (1, 2, 3) else None
    photo.status = "publiceret"
    photo.published_at = iso_dt(meta.get("publiceret")) or iso_dt(meta.get("uploadet")) or datetime.now(TZ)
    photo.uploaded_at = iso_dt(meta.get("uploadet")) or photo.published_at
    photo.views_imported, photo.likes_imported = int(meta.get("visninger") or 0), int(meta.get("likes") or 0)
    photo.source_hash = fp
    photo.locally_edited = False
    photo.updated_at = datetime.now(TZ)
    await session.flush()
    # billedet
    fname = next((n for n in src.names(pid) if n.startswith("foto.") and Path(n).suffix.lower() in IMAGE_EXT), None)
    if fname is None:
        raise ValueError("billedfilen mangler")
    if (task := run.prep.pop(pid, None)) is not None:
        exif, prepared = await task  # allerede (ved at blive) lavet i puljen, mens de foregående fotos blev skrevet til DB
    else:
        data = src.read(pid, fname)
        exif, prepared = await asyncio.to_thread(prepare_photo_image, data)
    if not kam.get("model"):
        kam = {**exif, **{k: v for k, v in kam.items() if v}}
    photo.kamera = {k: v for k, v in kam.items() if v}
    before = photo.media_id
    m = await content_media.store_image(session, prepared.data, filename=f"netfugl-{pid}{Path(fname).suffix}", owner_kind="foto", owner_id=photo.id,
                                        credit=(f.get("navn") or "")[:160], webp=True, prepared=prepared)
    if before == m.id:
        run.stats["billeder_genbrugt"] += 1
    elif before is not None:
        # Det nye billede (uanset filtype) erstatter det gamle: den gamle række og dens filer (jpg/webp + varianter) slettes
        photo.media_id = m.id
        await session.flush()
        if (old := await session.get(content_media.ContentMedia, before)) is not None:
            await content_media.delete_media(session, old)
        run.stats["billeder_erstattet"] += 1
    photo.media_id, photo.width, photo.height = m.id, m.width or meta.get("bredde"), m.height or meta.get("hoejde")
    # fugle
    await session.execute(text("DELETE FROM gal_birds WHERE photo_id = :p"), {"p": photo.id})
    for pos, b in enumerate(meta["fugle"][:10]):
        art = b.get("art") or {}
        artnr = find_artnr(run, art, b.get("underart"))
        if artnr is None:
            run.stats["fugle_uden_artnr"] += 1
        n_before = len(run.codes.created)
        session.add(GalBird(photo_id=photo.id, pos=pos, artnr=artnr, netfugl_art_id=art.get("netfugl_id"), navn=(art.get("dansk") or art.get("latin") or "Ukendt")[:200],
                            latin=(art.get("latin") or None) and art["latin"][:200],
                            adfaerd=run.codes.code(session, "adfaerd", b.get("adfaerd")), koen=run.codes.code(session, "koen", b.get("koen")),
                            alder=run.codes.code(session, "alder", b.get("alder")), dragt=run.codes.code(session, "dragt", b.get("dragt"))))
        for kind, txt in run.codes.created[n_before:]:
            run.stats["ukendte_koder"] += 1
            run.note("info", f"Ny kode oprettet: {kind} «{txt}»", pid)
    await session.flush()
    if opts.get("import_comments", True):
        photo.comments = await _import_comments(session, photo, meta.get("kommentarer") or [], run)
    if meta.get("gruppe"):
        run.groups.append([pid, *[int(x) for x in meta["gruppe"]]])
    return "oprettet" if created else "opdateret"


async def build_groups(session: AsyncSession, run: Run) -> None:
    """Saml fotos med ``gruppe: [...]`` i samme gal_groups (første id = cover)."""
    for ids in run.groups:
        photos = list((await session.execute(select(GalPhoto).where(GalPhoto.netfugl_id.in_(ids)))).scalars())
        if len(photos) < 2:
            continue
        gid = next((p.group_id for p in photos if p.group_id), None)
        first = next((p for i in ids for p in photos if p.netfugl_id == i), photos[0])
        if gid is None:
            art = (await session.execute(select(GalBird.artnr).where(GalBird.photo_id == first.id, GalBird.pos == 0))).scalar_one_or_none()
            g = GalGroup(photographer_id=first.photographer_id, artnr=art, dato=first.dato, cover_photo_id=first.id)
            session.add(g)
            await session.flush()
            gid = g.id
            run.stats["grupper"] += 1
        for p in photos:
            p.group_id = gid
    await session.flush()


async def recent(session: AsyncSession, limit: int = 20) -> list[GalImport]:
    return list((await session.execute(select(GalImport).order_by(GalImport.id.desc()).limit(limit))).scalars())


async def create_folder_import(session: AsyncSession, user_id: int, sti: str, options: dict) -> GalImport:
    """Opret en import fra en mappe/zip under photo_import_dir (stien valideres med det samme)."""
    src = find_source(sti)
    imp = GalImport(kilde="mappe" if not sti.lower().endswith(".zip") else "zip-fil", sti=sti[:300], filename=sti[:300], uploaded_by=user_id, status="uploader",
                    size=0, stats={"fotos_i_kilden": len(src.ids())})
    session.add(imp)
    await session.flush()
    return imp


# ---------------------------------------------------------------- job

async def run_import(import_id: int) -> None:
    """Worker-kommando ``photos_import``."""
    async with SessionLocal() as s:
        imp = await s.get(GalImport, import_id)
        if imp is None:
            return
        imp.status, imp.started_at = "koerer", datetime.now(TZ)
        run = Run(options=dict(imp.options or {}))
        rel, kilde = imp.sti, imp.kilde
        await s.commit()
        status = "faerdig"
        try:
            await _run(s, imp, run, rel)
        except ImportAbort as exc:
            run.note("fejl", str(exc))
            status = "fejl"
        except Exception as exc:  # noqa: BLE001 – status skal ende som fejl, ikke hænge på «kører»
            log.exception("Fotoimport %s fejlede", import_id)
            run.note("fejl", f"{exc.__class__.__name__}: {exc}")
            status = "fejl"
        await s.rollback()
        imp = await s.get(GalImport, import_id)
        imp.status, imp.stats, imp.log, imp.finished_at = status, run.stats, run.log, datetime.now(TZ)
        if status == "faerdig" and kilde == "zip" and not run.options.get("keep_files") and not run.options.get("dry_run"):
            (Path(get_settings().photo_import_dir) / (rel or "")).unlink(missing_ok=True)
        await s.commit()
        await bump("photos")
        if status == "faerdig" and not run.options.get("dry_run"):
            await worker_command("photos_link_taxa")  # Netfugls artsnumre → World Bird List (krydslistemodulet)


async def _run(s: AsyncSession, imp: GalImport, run: Run, rel: str | None) -> None:
    src = find_source(rel)
    run.stats["fotos_i_kilden"] = len(src.ids())
    run.stats["afvist"] = len(src.rejected)
    for r in src.rejected[:20]:
        run.note("advarsel", f"Afvist (usikker sti, symlink eller størrelse): {r}")
    run.codes = await photo_codes.Codes.load(s)
    await s.commit()
    await _load_species(s, run)
    import_id = imp.id
    ids = src.ids()
    limit = run.options.get("limit")
    if limit:
        ids = ids[: int(limit)]
    known = {}
    if not run.options.get("dry_run"):
        known = {r[0]: (r[1], r[2]) for r in (await s.execute(select(GalPhoto.netfugl_id, GalPhoto.source_hash, GalPhoto.locally_edited)
                                                              .where(GalPhoto.netfugl_id.isnot(None)))).all()}
    threads = max(1, int(run.options.get("threads") or min(8, max(2, (os.cpu_count() or 2) - 1))))
    pool = ThreadPoolExecutor(threads, thread_name_prefix="fotoimport")
    ahead = threads * 2  # så mange fotos forberedes forud
    try:
        await _import_all(s, imp, run, src, ids, known, pool, ahead, import_id)
    finally:
        for t in run.prep.values():
            t.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    if not run.options.get("dry_run"):
        await build_groups(s, run)
        m = await photo_photographers.match_all(s)
        run.note("info", f"Fotografmatch: {m['auto']} automatisk, {m['tvetydig']} tvetydige, {m['ingen']} uden match")
        await s.commit()


def _prefetch(run: Run, src: Source, pid: int, known: dict, pool: ThreadPoolExecutor) -> None:
    """Start billedarbejdet for et foto, hvis det skal importeres (nyt eller ændret). Filerne læses her; selve arbejdet sker i puljen.
    Fejl her ignoreres: import_photo læser og melder dem selv, når fotoet kommer til."""
    try:
        raw = src.read(pid, "meta.json")
        old = known.get(pid)
        if old is not None and not run.options.get("force"):
            if old[0] == hashlib.sha256(raw).hexdigest():
                return  # uændret
            if old[1] and not run.options.get("overwrite_local"):
                return  # rettet lokalt og sprunget over
        fname = next((n for n in src.names(pid) if n.startswith("foto.") and Path(n).suffix.lower() in IMAGE_EXT), None)
        if fname is None:
            return
        fut = asyncio.get_running_loop().run_in_executor(pool, prepare_photo_image, src.read(pid, fname))
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())  # en aldrig brugt fejl skal ikke blive til en advarsel
        run.prep[pid] = fut
    except Exception:  # noqa: BLE001
        return


async def _import_all(s: AsyncSession, imp: GalImport, run: Run, src: Source, ids: list[int], known: dict, pool: ThreadPoolExecutor,
                      ahead: int, import_id: int) -> None:
    nxt = 0
    for n, pid in enumerate(ids, 1):
        if not run.options.get("dry_run"):
            while nxt < min(len(ids), n - 1 + ahead):
                _prefetch(run, src, ids[nxt], known, pool)
                nxt += 1
        if n % 25 == 1 or n == len(ids):
            await jobstatus.step(f"Foto {pid} ({n} af {len(ids)})", n, len(ids))
        snap = run.snapshot()
        try:
            result = await import_photo(s, run, src, pid)
            await s.commit()
            run.stats[result] += 1
        except Exception as exc:  # noqa: BLE001 – ét dårligt foto må ikke stoppe resten
            await s.rollback()
            run.restore(snap)
            run.stats["fejl"] += 1
            run.note("fejl", f"{exc.__class__.__name__}: {exc}", pid)
            log.warning("Foto %s kunne ikke importeres: %s", pid, exc)
        if n % 100 == 0:
            cur = await s.get(GalImport, import_id)
            cur.stats, cur.log = dict(run.stats), list(run.log)
            await s.commit()
