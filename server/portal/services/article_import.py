"""Import af artikelarkivet (netfugl.dk) fra en zip (docs/artikler-modul-spec.md §4).

Zip'en læses direkte med ``zipfile`` (ingen fuld udpakning). Importen gennemløber artikelmapperne (``NNNN/meta.json``) og bruger
ikke ``_index.json``, som kan være ufuldstændig. Den kan køres igen og igen: en artikel genkendes på ``netfugl_id``, og et
fingeraftryk (CRC'er af artikelmappens filer) afgør, om der overhovedet er noget nyt. Artikler en redaktør har rettet
(``locally_edited``) overskrives ikke, medmindre importen beder om det. Importen sender aldrig notifikationer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
import re
import stat as statmod
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import bump
from portal.config import get_settings
from portal.db import SessionLocal
from portal.models import ArtArticle, ArtArticleTag, ArtComment, ArtImport, ArtTag, ContentMedia
from portal.services import article_settings, article_species, content_media, content_render
from portal.timeutil import TZ

log = logging.getLogger("portal.articles.import")

MAX_MEMBERS = 200_000
MAX_MEMBER_BYTES = 300 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024 * 1024
MAX_RATIO = 100
MAX_LOG = 2000
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ARTICLE_DIR = re.compile(r"^(\d{4})/(.+)$")


class ImportAbort(Exception):
    """Zip'en kan ikke importeres (usikker eller ødelagt)."""


# ---------------------------------------------------------------- zip

def safe_name(name: str) -> str | None:
    """Medlemmets normaliserede sti, eller None hvis den er usikker (zip-slip)."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    if ".." in name.split("/"):  # tjek før normalisering: «0005/../../x» ville ellers blive til et harmløst «x»
        return None
    return posixpath.normpath(name)


def is_symlink(info: zipfile.ZipInfo) -> bool:
    return statmod.S_ISLNK(info.external_attr >> 16)


@dataclass
class Archive:
    zf: zipfile.ZipFile
    root: str
    articles: dict[int, dict[str, zipfile.ZipInfo]]
    rejected: list[str] = field(default_factory=list)
    ignored: int = 0
    other: dict[str, zipfile.ZipInfo] = field(default_factory=dict)  # _index.json m.m. i roden


def allowed_member(rel: str) -> bool:
    """Filer under en artikelmappe som importeres (rel er stien under NNNN/)."""
    if rel in ("meta.json", "artikel.md", "kommentarer.json"):
        return True
    top, _, rest = rel.partition("/")
    if not rest or "/" in rest:
        return False
    ext = Path(rest).suffix.lower()
    if top == "billeder":
        return ext in IMAGE_EXT
    if top == "filer":
        return ext in content_media.FILE_TYPES
    return False


def open_archive(path: Path) -> Archive:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ImportAbort("Filen er ikke en gyldig zip") from exc
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ImportAbort(f"Zip'en har for mange filer ({len(infos)} > {MAX_MEMBERS})")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise ImportAbort("Zip'en er for stor udpakket (over 20 GB)")
    names = [(i, safe_name(i.filename.rstrip("/"))) for i in infos]
    # Roden: mappen med MANIFEST.md / manifest.json – ellers den første mappe der indeholder NNNN/meta.json
    root = None
    for i, n in names:
        if n and posixpath.basename(n).lower() in ("manifest.md", "manifest.json") and n.count("/") <= 1:
            root = posixpath.dirname(n)
            break
    if root is None:
        for i, n in names:
            m = n and re.search(r"(^|.*/)(\d{4})/meta\.json$", n)
            if m:
                root = m.group(1).rstrip("/")
                break
    if root is None:
        raise ImportAbort("Fandt hverken MANIFEST.md eller artikelmapper (NNNN/meta.json) i zip'en")
    prefix = root + "/" if root else ""
    arc = Archive(zf=zf, root=root, articles={})
    for info, norm in names:
        if info.is_dir():
            continue
        if norm is None or is_symlink(info):
            arc.rejected.append(info.filename)
            continue
        if not norm.startswith(prefix):
            arc.ignored += 1
            continue
        rel = norm[len(prefix):]
        if rel.startswith("Turrapporter/"):
            arc.ignored += 1  # følger i en senere omgang
            continue
        if "/" not in rel:
            arc.other[rel] = info
            continue
        m = ARTICLE_DIR.match(rel)
        if not m or not allowed_member(m.group(2)):
            arc.ignored += 1
            continue
        if info.file_size > MAX_MEMBER_BYTES or (info.file_size > (1 << 20) and info.file_size > MAX_RATIO * max(1, info.compress_size)):
            arc.rejected.append(f"{info.filename} (størrelse/komprimeringsforhold)")
            continue
        arc.articles.setdefault(int(m.group(1)), {})[m.group(2)] = info
    return arc


def read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Læs et medlem, men aldrig mere end ``MAX_MEMBER_BYTES`` (headerens størrelse kan lyve)."""
    with zf.open(info) as fh:
        data = fh.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise ImportAbort(f"{info.filename} er for stor")
    return data


def fingerprint(members: dict[str, zipfile.ZipInfo]) -> str:
    h = hashlib.sha1()
    for rel in sorted(members):
        i = members[rel]
        h.update(f"{rel}\0{i.CRC}\0{i.file_size}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------- tekst

def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("**", "").replace("__", "")).strip().casefold()


IMG = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)')
LINK = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
NETFUGL_URL = re.compile(r"^https?://(?:www\.)?netfugl\.dk/artikler/(\d+)(?:/[^#?]*)?(?:[#?].*)?$", re.I)
REL_ARTICLE = re.compile(r"^(?:\.\./|\./)?(\d{4})/artikel\.md(?:#.*)?$")


def rewrite_markdown(md: str, title: str, relmap: dict[str, int], cover_rel: str | None = None, gone: frozenset[str] | set[str] = frozenset()) -> tuple[str, list[str]]:
    """Gør arkivets markdown portabel: titel og forsidebillede fjernes øverst, billeder og filer peger på ``media:<id>``, og
    links til andre artikler bliver ``netfugl:<nr>``. Billeder på netfugl.dk, der ikke findes mere (``gone``), bliver til deres billedtekst i kursiv.
    Returnerer (tekst, advarsler)."""
    warn: list[str] = []
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and re.match(r"^#\s+", lines[i]):
        i += 1
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and _norm_title(lines[j]) == _norm_title(title):
            i = j + 1
    body = "\n".join(lines[i:]).strip("\n")

    def lookup(path: str) -> int | None:
        if path in relmap:  # hentede eksterne billeder er nøglet på deres adresse
            return relmap[path]
        p = posixpath.normpath(unquote(path).lstrip("./")) if not path.startswith(("..", "http")) else None
        return relmap.get(p) if p else None

    if cover_rel:
        first = re.match(r"\s*!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*\n?", body)
        if first and unquote(first.group(1)).lstrip("./") == cover_rel:
            body = body[first.end():].lstrip("\n")

    def img(m: re.Match) -> str:
        if m.group(2) in gone:
            return f"*{m.group(1).strip()}*" if m.group(1).strip() else ""
        mid = lookup(m.group(2))
        if mid is None:
            if not re.match(r"https?://|/media/|media:", m.group(2)):
                warn.append(f"Billede ikke fundet i zip'en: {m.group(2)}")
            return m.group(0)
        cap = f' "{m.group(3)}"' if m.group(3) else ""
        return f"![{m.group(1)}](media:{mid}{cap})"

    def link(m: re.Match) -> str:
        target = m.group(2)
        mid = lookup(target)
        if mid is not None:
            return f"[{m.group(1)}](media:{mid})"
        if (r := REL_ARTICLE.match(target)) or (r := NETFUGL_URL.match(target)):
            return f"[{m.group(1)}](netfugl:{int(r.group(1))})"
        return m.group(0)

    return LINK.sub(link, IMG.sub(img, body)), warn


TAG_TYPES = {"nyhed", "artikel", "kort nyt"}
TAG_PUBLICATIONS = {"pandion", "dof nyt", "fugle og natur", "doft", "dof-nyt"}


def classify_tag(name: str, species_exact: dict[str, int]) -> tuple[str, int | None]:
    """(kind, artnr) for et emneord, første gang det ses (§4.6). Admin kan rette det bagefter."""
    n = name.strip().lower()
    if re.fullmatch(r"\d{4}", n):
        return "aar", None
    if n in TAG_TYPES:
        return "type", None
    if n in TAG_PUBLICATIONS:
        return "publikation", None
    if n in species_exact:
        return "art", species_exact[n]
    return "emne", None


def ocr_plain(ocr: str) -> str:
    """OCR-tekst til søgning: uden titel, kildenote (filnavnet) og «## Side N»-overskrifter."""
    lines = [ln for ln in ocr.replace("\r\n", "\n").split("\n") if not re.match(r"^(#{1,3}\s|>\s)", ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def comment_key(article_no: int, author: str, date: str, text_: str, occurrence: int) -> str:
    return hashlib.sha1(f"{article_no}|{author}|{date}|{text_}|{occurrence}".encode()).hexdigest()


def flatten_comments(comments: list[dict]) -> list[tuple[dict, dict | None]]:
    """[(kommentar, forælder)] – netfugl har to niveauer; dybere svar hægtes på hovedkommentaren."""
    out: list[tuple[dict, dict | None]] = []

    def walk(items: list[dict], top: dict | None) -> None:
        for c in items or []:
            out.append((c, top))
            walk(c.get("svar") or [], top or c)

    walk(comments, None)
    return out


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}
DANISH_DATE = re.compile(r"(\d{1,2})\.?\s+([a-zæøå]{3,})\.?\s+(\d{4})(?:\s+kl\.?\s*(\d{1,2})[.:](\d{2}))?", re.I)


def parse_danish_date(value: str | None) -> datetime | None:
    """«9. maj 2001 kl. 20:27», «1. okt. 2008» → dansk lokaltid. Bruges, når arkivets ISO-dato mangler."""
    m = DANISH_DATE.search(value or "")
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return None
    try:
        return datetime(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=TZ)
    except ValueError:
        return None


def article_date(meta: dict) -> tuple[datetime, str | None]:
    """(dato, advarsel). ISO-datoen, ellers den danske tekstdato, ellers uploaddatoen – aldrig bare «nu» uden en advarsel."""
    if meta.get("dato"):
        return parse_dato(meta["dato"]), None
    if d := parse_danish_date(meta.get("dato_original")):
        return d, None
    up = (meta.get("uploadet_af") or {}).get("dato")
    if up:
        return parse_dato(up), "Datoen mangler i arkivet – uploaddatoen er brugt"
    return datetime.now(TZ), "Datoen mangler i arkivet – importtidspunktet er brugt"


def parse_dato(value: str | None, fallback: datetime | None = None) -> datetime:
    if value:
        try:
            d = datetime.fromisoformat(value)
            return d.replace(tzinfo=TZ) if d.tzinfo is None else d
        except ValueError:
            pass
    return fallback or datetime.now(TZ)


# ---------------------------------------------------------------- kørsel

@dataclass
class Run:
    stats: dict[str, int] = field(default_factory=lambda: {
        "artikler_i_zip": 0, "oprettet": 0, "opdateret": 0, "uaendret": 0, "sprunget_lokalt": 0, "fejl": 0, "billeder": 0, "pdf": 0,
        "filer": 0, "kommentarer": 0, "afvist": 0, "hentede_billeder": 0, "billeder_findes_ikke": 0})
    log: list[dict] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    tags_by_id: dict[int, int] = field(default_factory=dict)
    tags_by_name: dict[str, int] = field(default_factory=dict)
    http: httpx.AsyncClient | None = None

    def note(self, level: str, msg: str, no: int | None = None) -> None:
        if len(self.log) < MAX_LOG:
            self.log.append({"netfugl_id": no, "niveau": level, "besked": msg})
        elif len(self.log) == MAX_LOG:
            self.log.append({"netfugl_id": None, "niveau": "info", "besked": "Loggen er afkortet"})


async def _tag_id(session: AsyncSession, run: Run, matcher, t: dict) -> int:
    name = str(t.get("navn") or "").strip()[:120]
    tid = t.get("netfugl_tag_id")
    if tid is not None and tid in run.tags_by_id:
        return run.tags_by_id[tid]
    if name.lower() in run.tags_by_name:
        return run.tags_by_name[name.lower()]
    row = None
    if tid is not None:
        row = (await session.execute(select(ArtTag).where(ArtTag.netfugl_tag_id == tid))).scalar_one_or_none()
    if row is None:
        row = (await session.execute(select(ArtTag).where(ArtTag.navn_lc == name.lower()))).scalar_one_or_none()
    if row is None:
        kind, artnr = classify_tag(name, matcher.exact)
        row = ArtTag(navn=name, navn_lc=name.lower(), netfugl_tag_id=tid, kind=kind, artnr=artnr, hidden=kind == "type")
        session.add(row)
        await session.flush()
    elif row.netfugl_tag_id is None and tid is not None:
        row.netfugl_tag_id = tid
    if tid is not None:
        run.tags_by_id[tid] = row.id
    run.tags_by_name[name.lower()] = row.id
    return row.id


# Fejl i selve filen (ødelagt billede, afkortet zip-medlem, for stor). Lager- og databasefejl er ikke med: så fejler artiklen
# og prøves igen ved næste import, i stedet for at blive gemt uden filerne.
BAD_FILE = (ValueError, IndexError, OSError, EOFError, SyntaxError, zipfile.BadZipFile, ImportAbort)


async def _store_media(session: AsyncSession, run: Run, arc: Archive, art: ArtArticle, meta: dict, members: dict[str, zipfile.ZipInfo],
                       user_id: int | None) -> tuple[dict[str, int], str | None, str, list[str]]:
    """Gem billeder og vedhæftninger. Returnerer (sti → medie-id, forsidebillede-sti, OCR-tekst, fejl)."""
    relmap: dict[str, int] = {}
    errors: list[str] = []
    cover_rel: str | None = None
    ocr_parts: list[str] = []
    zf = arc.zf
    listed = {b.get("fil"): b for b in meta.get("billeder") or [] if b.get("fil")}
    for n, rel in enumerate(sorted(r for r in members if r.startswith("billeder/"))):
        b = listed.get(rel, {})
        try:
            m = await content_media.store_image(session, read_member(zf, members[rel]), filename=rel.split("/", 1)[1], owner_kind="artikel",
                                                owner_id=art.id, user_id=user_id, alt=b.get("alt") or "", original_url=b.get("original_url"), sort=n)
            relmap[rel] = m.id
            run.stats["billeder"] += 1
            if b.get("forsidebillede") and cover_rel is None:
                cover_rel = rel
        except content_media.MediaError as exc:
            errors.append(f"{rel}: {exc}")
        except BAD_FILE as exc:  # én ødelagt fil må ikke stoppe artiklen; den springes over med en advarsel
            log.warning("Artikel %s: %s sprunget over: %s", art.netfugl_id, rel, exc)
            errors.append(f"{rel}: sprunget over ({exc.__class__.__name__}: {exc})")
    files = {f.get("fil"): f for f in meta.get("vedhaeftede_filer") or [] if f.get("fil")}
    for n, rel in enumerate(sorted(r for r in members if r.startswith("filer/") and not r.endswith(".ocr.md"))):
        f = files.get(rel, {})
        ocr = None
        if f.get("tekst_fil") and f["tekst_fil"] in members:
            ocr = read_member(zf, members[f["tekst_fil"]]).decode("utf-8", "replace")
        elif rel + ".ocr.md" in members:
            ocr = read_member(zf, members[rel + ".ocr.md"]).decode("utf-8", "replace")
        elif (alt := rel.rsplit(".", 1)[0] + ".ocr.md") in members:
            ocr = read_member(zf, members[alt]).decode("utf-8", "replace")
        try:
            m = await content_media.store_file(session, read_member(zf, members[rel]), filename=rel.split("/", 1)[1], owner_kind="artikel",
                                               owner_id=art.id, user_id=user_id, title=f.get("titel") or "", text_md=ocr,
                                               text_source=f.get("tekst_kilde") or ("ocr" if ocr else None), original_url=f.get("original_url"), sort=n)
            relmap[rel] = m.id
            run.stats["pdf" if m.kind == "pdf" else "filer"] += 1
            if ocr:
                ocr_parts.append(ocr_plain(ocr))
        except content_media.MediaError as exc:
            errors.append(f"{rel}: {exc}")
        except BAD_FILE as exc:  # én ødelagt fil må ikke stoppe artiklen; den springes over med en advarsel
            log.warning("Artikel %s: %s sprunget over: %s", art.netfugl_id, rel, exc)
            errors.append(f"{rel}: sprunget over ({exc.__class__.__name__}: {exc})")
    return relmap, cover_rel, "\n\n".join(ocr_parts), errors


EXTERNAL_HOSTS = {"netfugl.dk", "www.netfugl.dk"}
MAX_EXTERNAL_PER_ARTICLE = 30


def external_images(md: str) -> list[str]:
    """Adresser på netfugl.dk-billeder i teksten (de ligger ikke i zip'en, men skal ikke hentes fra netfugl.dk ved hver visning)."""
    out: list[str] = []
    for m in IMG.finditer(md):
        u = m.group(2)
        try:
            host = urlsplit(u).hostname or ""
        except ValueError:
            continue
        if u.startswith(("http://", "https://")) and host.lower() in EXTERNAL_HOSTS and u not in out:
            out.append(u)
    return out[:MAX_EXTERNAL_PER_ARTICLE]


async def _fetch_external(session: AsyncSession, run: Run, art: ArtArticle, md: str, user_id: int | None, start: int) -> tuple[dict[str, int], set[str], list[str], bool]:
    """Hent netfugl.dk-billeder, som artiklen henviser til, og gem dem som egne medier.
    Returnerer (adresse → medie-id, adresser der ikke findes mere (404/410), noter, om der var midlertidige fejl der bør prøves igen)."""
    got: dict[str, int] = {}
    gone: set[str] = set()
    errors: list[str] = []
    transient = False
    if run.http is None or not run.options.get("fetch_images", True):
        return got, gone, errors, transient
    for n, url in enumerate(external_images(md), start):
        try:
            resp = await run.http.get(url)
            if resp.status_code in (404, 410):
                gone.add(url)
                await asyncio.sleep(0.2)
                continue
            if resp.status_code != 200 or len(resp.content) > content_media.MAX_IMAGE:
                raise content_media.MediaError(f"HTTP {resp.status_code}")
            m = await content_media.store_image(session, resp.content, filename=Path(urlsplit(url).path).name or "billede.jpg", owner_kind="artikel",
                                                owner_id=art.id, user_id=user_id, original_url=url, sort=n)
            got[url] = m.id
            run.stats["hentede_billeder"] += 1
        except (content_media.MediaError, httpx.HTTPError) as exc:
            errors.append(f"Kunne ikke hente {url}: {exc}")
            transient = True
        await asyncio.sleep(0.2)  # høflig over for netfugl.dk
    if gone:
        run.stats["billeder_findes_ikke"] = run.stats.get("billeder_findes_ikke", 0) + len(gone)
        errors.append(f"{len(gone)} billede(r) findes ikke længere hos netfugl.dk – billedteksten er bevaret")
    return got, gone, errors, transient


async def _import_comments(session: AsyncSession, article_no: int, art: ArtArticle, comments: list[dict], run: Run) -> None:
    flat = flatten_comments(comments)
    existing = {c.archive_key: c for c in (await session.execute(select(ArtComment).where(ArtComment.article_id == art.id, ArtComment.archive_key.isnot(None)))).scalars()}
    seen: set[str] = set()
    ids: dict[int, int] = {}  # id() af hovedkommentarens dict → rækkens id
    occurrences: dict[tuple, int] = {}
    for c, top in flat:
        text_ = c.get("tekst_md") or c.get("tekst_html") or ""
        sig = (c.get("forfatter") or "", c.get("dato") or "", text_)
        occ = occurrences[sig] = occurrences.get(sig, -1) + 1
        key = comment_key(article_no, *sig, occ)
        seen.add(key)
        parent_id = ids.get(id(top)) if top is not None else None
        row = existing.get(key)
        if row is None:
            html = content_render.render(text_, "kommentar").html
            row = ArtComment(article_id=art.id, parent_id=parent_id, user_id=None, archive_author=(c.get("forfatter") or "Ukendt")[:160],
                             archive_netfugl_user=c.get("forfatter_id"), archive_key=key, body_md=text_, body_html=html,
                             created_at=parse_dato(c.get("dato"), art.dato))
            session.add(row)
            await session.flush()
            run.stats["kommentarer"] += 1
        ids[id(c)] = row.id
    for key, row in existing.items():
        if key not in seen:
            await session.delete(row)


async def import_article(session: AsyncSession, run: Run, arc: Archive, no: int, members: dict[str, zipfile.ZipInfo], matcher, hosts: set[str],
                         user_id: int | None, import_id: int) -> str:
    """Importér én artikel. Returnerer 'oprettet', 'opdateret', 'uaendret' eller 'sprunget_lokalt'."""
    opts = run.options
    if "meta.json" not in members:
        raise ValueError("meta.json mangler")
    meta = json.loads(read_member(arc.zf, members["meta.json"]))
    if meta.get("schema_version") != 1:
        raise ValueError(f"schema_version {meta.get('schema_version')} understøttes ikke")
    if int(meta.get("id", -1)) != no:
        raise ValueError(f"meta.json har id {meta.get('id')}, men mappen hedder {no:04d}")
    md_raw = read_member(arc.zf, members["artikel.md"]).decode("utf-8", "replace") if "artikel.md" in members else ""
    comments = json.loads(read_member(arc.zf, members["kommentarer.json"])) if "kommentarer.json" in members else []
    fp = fingerprint(members)
    art = (await session.execute(select(ArtArticle).where(ArtArticle.netfugl_id == no))).scalar_one_or_none()
    if art is not None and art.source_hash == fp and not opts.get("force"):
        return "uaendret"
    if opts.get("dry_run"):
        return "opdateret" if art is not None else "oprettet"
    created = art is None
    protect = art is not None and art.locally_edited and not opts.get("overwrite_local")
    title = str(meta.get("titel") or f"Artikel {no}").strip()[:300]
    if created:
        dato, _ = article_date(meta)
        art = ArtArticle(netfugl_id=no, title=title, dato=dato, source="netfugl", status="publiceret" if opts.get("publish", True) else "kladde",
                         published_at=dato if opts.get("publish", True) else None, import_id=import_id)
        session.add(art)
        await session.flush()
    relmap, cover_rel, ocr, errors = await _store_media(session, run, arc, art, meta, members, user_id)
    fetched: dict[str, int] = {}
    gone: set[str] = set()
    fetch_failed = False
    if not protect:
        fetched, gone, fetch_errors, fetch_failed = await _fetch_external(session, run, art, md_raw, user_id, len(relmap))
        errors += fetch_errors
        relmap.update(fetched)
    if not protect:
        body_md, warnings = rewrite_markdown(md_raw, title, relmap, cover_rel, gone)
        media = {r["id"]: content_render.Media.from_row(r) for r in (await session.execute(text(
            "SELECT id, kind, storage_key, medium_key, thumb_key, width, height, alt, caption, credit, filename, size, pages "
            "FROM content_media WHERE owner_kind = 'artikel' AND owner_id = :i"), {"i": art.id})).mappings()}
        rendered = content_render.render(body_md, "artikel", media=media, embed_hosts=hosts)
        pdf_ids = [i for i, m in media.items() if m.kind == "pdf"]
        art.title, art.slug = title, content_render.slugify(title)
        art.dato, date_warning = article_date(meta)
        if date_warning:
            warnings.append(date_warning)
        art.authors = [{"navn": a.get("navn"), "netfugl_id": a.get("netfugl_forfatter_id")} for a in meta.get("forfattere") or [] if a.get("navn")]
        art.uploaded_by, art.last_edited_netfugl = meta.get("uploadet_af"), meta.get("sidst_redigeret")
        art.body_md, art.body_html, art.body_text = body_md, rendered.html, rendered.text
        art.render_version = content_render.RENDER_VERSION
        art.ocr_text = ocr or None
        art.kind = "pdf" if pdf_ids and len(rendered.text) < 200 else "artikel"
        art.source_url, art.views_imported = meta.get("kilde_url"), int(meta.get("antal_laest_ved_hentning") or 0)
        cover = relmap.get(cover_rel) if cover_rel else None
        art.cover_media_id = cover or min((relmap[r] for r in relmap if r.startswith("billeder/")), default=None) or min(fetched.values(), default=None)
        art.import_errors = [*(meta.get("fejl") or []), *errors, *warnings]
        art.import_id = import_id
        await session.flush()
        tag_ids = {await _tag_id(session, run, matcher, t) for t in meta.get("emneord") or [] if t.get("navn")}
        await session.execute(delete(ArtArticleTag).where(ArtArticleTag.article_id == art.id))
        session.add_all(ArtArticleTag(article_id=art.id, tag_id=t) for t in sorted(tag_ids))
        await session.flush()
    elif errors:
        art.import_errors = [*art.import_errors, *errors]
    if opts.get("import_comments", True):
        await _import_comments(session, no, art, comments, run)
    art.source_hash = None if fetch_failed else fp  # mislykkede hentninger prøves igen ved næste import
    await session.flush()
    if not protect:
        await article_species.index_article(session, art.id, matcher)
    if errors:
        run.note("advarsel", "; ".join(errors[:5]), no)
    return "sprunget_lokalt" if protect else ("oprettet" if created else "opdateret")


async def recent(session: AsyncSession, limit: int = 20) -> list[ArtImport]:
    return list((await session.execute(select(ArtImport).order_by(ArtImport.id.desc()).limit(limit))).scalars())


async def run_import(import_id: int) -> None:
    """Worker-kommando ``articles_import``."""
    async with SessionLocal() as s:
        imp = await s.get(ArtImport, import_id)
        if imp is None:
            return
        path = Path(get_settings().article_import_dir) / f"{import_id}.zip"
        imp.status, imp.started_at = "koerer", datetime.now(TZ)
        run = Run(options=dict(imp.options or {}))
        await s.commit()
        status = "faerdig"
        try:
            await _run(s, imp, run, path)
        except ImportAbort as exc:
            run.note("fejl", str(exc))
            status = "fejl"
        except Exception as exc:  # noqa: BLE001 – status skal ende som fejl, ikke hænge på «kører»
            log.exception("Artikelimport %s fejlede", import_id)
            run.note("fejl", f"{exc.__class__.__name__}: {exc}")
            status = "fejl"
        await s.rollback()
        imp = await s.get(ArtImport, import_id)
        imp.status, imp.stats, imp.log, imp.finished_at = status, run.stats, run.log, datetime.now(TZ)
        if status == "faerdig" and not run.options.get("keep_zip") and not run.options.get("dry_run"):
            path.unlink(missing_ok=True)
        await s.commit()
        await bump("articles")


async def _run(s: AsyncSession, imp: ArtImport, run: Run, path: Path) -> None:
    if not path.is_file():
        raise ImportAbort(f"Zip-filen findes ikke på serveren ({path}) – mappen skal være et volume, som api og worker deler")
    arc = open_archive(path)
    run.stats["afvist"] = len(arc.rejected)
    for r in arc.rejected[:20]:
        run.note("advarsel", f"Afvist (usikker sti eller størrelse): {r}")
    if "manifest.json" in arc.other:
        try:
            kilde = str(json.loads(read_member(arc.zf, arc.other["manifest.json"])).get("kilde") or "")
        except (ValueError, AttributeError, ImportAbort):
            kilde = ""
        if "dklist" in kilde:  # turrapport-arkivet har samme mappenavne (NNNN/meta.json) men et andet format
            raise ImportAbort("Zip'en indeholder turrapporter (dklist.netfugl.dk), ikke artikler – importér netfugl-artikler.zip/Artikler.zip i stedet")
    nos = sorted(arc.articles)
    run.stats["artikler_i_zip"] = len(nos)
    if "_index.json" in arc.other:
        try:
            idx = json.loads(read_member(arc.zf, arc.other["_index.json"]))
            run.note("info", f"_index.json nævner {len(idx)} artikler, zip'en har {len(nos)} artikelmapper (mapperne bruges)")
        except (ValueError, ImportAbort):
            pass
    hosts = set((await article_settings.get(s))["embed_hosts"])
    matcher = await article_species.load_matcher(s, fresh=True)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "DOF.tool/1.0 (artikelimport)"}) as http:
        run.http = http
        await _loop(s, imp, run, arc, nos, matcher, hosts)


async def _loop(s: AsyncSession, imp: ArtImport, run: Run, arc: Archive, nos: list[int], matcher, hosts: set[str]) -> None:
    # Læs værdierne før løkken: efter s.rollback() er imp udløbet, og adgang til felterne giver MissingGreenlet i async
    user_id, import_id = imp.uploaded_by, imp.id
    for n, no in enumerate(nos, 1):
        if n % 10 == 1 or n == len(nos):
            await jobstatus.step(f"Artikel {no:04d} ({n} af {len(nos)})", n, len(nos))
        try:
            result = await import_article(s, run, arc, no, arc.articles[no], matcher, hosts, user_id, import_id)
            await s.commit()
            run.stats[result] += 1
        except Exception as exc:  # noqa: BLE001 – én dårlig artikel må ikke stoppe resten
            await s.rollback()
            run.stats["fejl"] += 1
            run.note("fejl", f"{exc.__class__.__name__}: {exc}", no)
            log.warning("Artikel %s kunne ikke importeres: %s", no, exc)
        if n % 25 == 0:
            cur = await s.get(ArtImport, import_id)
            cur.stats, cur.log = dict(run.stats), list(run.log)
            await s.commit()
