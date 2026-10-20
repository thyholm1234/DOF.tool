"""Referencedata: arter (SU/SUB), bemærk-tærskler, fænologivinduer, afdelinger og kommuner.

Seed-filerne (fra DOF.not/Boligbirding) indlæses ved første opstart, så alt virker uden at
kontakte DOFbasen. Derefter holder gatewayen dem ajour med ét HTML-kald ad gangen.
"""

from __future__ import annotations

import csv
import html
import json
import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump, worker_command
from portal.config import SEED_DIR
from portal.models import Afdeling, BemaerkThreshold, Kommune, PhenologyWindow, Setting, Species
from portal.timeutil import dk_today

AFDELINGER = [
    (574, "DOF København", "koebenhavn", "kbh"),
    (575, "DOF Nordsjælland", "nordsjaelland", "nsjl"),
    (576, "DOF Vestsjælland", "vestsjaelland", "vsjl"),
    (577, "DOF Storstrøm", "storstroem", "ss"),
    (578, "DOF Bornholm", "bornholm", "b"),
    (579, "DOF Fyn", "fyn", "f"),
    (580, "DOF Sønderjylland", "soenderjylland", "sdrj"),
    (581, "DOF Sydvestjylland", "sydvestjylland", "svj"),
    (582, "DOF Sydøstjylland", "sydoestjylland", "soej"),
    (583, "DOF Vestjylland", "vestjylland", "vj"),
    (584, "DOF Østjylland", "oestjylland", "oej"),
    (585, "DOF Nordvestjylland", "nordvestjylland", "nvj"),
    (586, "DOF Nordjylland", "nordjylland", "nj"),
]
BEMAERK_LIST_BY_AFDELING = {afd_id: code for afd_id, _, _, code in AFDELINGER}


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value or ""))
    value = unicodedata.normalize("NFKC", value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def base_name(navn: str) -> str:
    return navn.strip("[]").split("(")[0].split(",")[0].strip()


def is_listable_name(navn: str) -> bool:
    """Ubestemte ('Albatros sp.', 'X/Y') og hybrider tæller ikke. 'sp.' skal stå som ord – ellers rammes '(ssp. …)' i
    alle underartsnavne. Klippedue (tamdue) er DOFbasens eneste Columba livia og står på Den Danske Liste (kategori C)."""
    n = navn.lower()
    return not (re.search(r"\bsp\.", n) or "/" in n or " x " in n or "hybrid" in n or "ubestemt" in n)


# ---------------------------------------------------------------- seed


async def seed_reference(session: AsyncSession) -> None:
    if not (await session.execute(select(func.count()).select_from(Afdeling))).scalar():
        await session.execute(insert(Afdeling).values([
            {"id": i, "navn": n, "slug": s} for i, n, s, _ in AFDELINGER
        ]).on_conflict_do_nothing())

    if not (await session.execute(select(func.count()).select_from(Species))).scalar():
        await _seed_species(session)

    if not (await session.execute(select(func.count()).select_from(BemaerkThreshold))).scalar():
        rows = []
        for afd_id, _, slug, _ in AFDELINGER:
            path = SEED_DIR / "bemaerk" / f"{slug}_bemaerk_parsed.csv"
            if not path.exists():
                continue
            with path.open(encoding="utf-8-sig") as f:
                for rec in csv.DictReader(f, delimiter=";"):
                    navn = (rec.get("artsnavn") or "").strip().strip("[]")
                    try:
                        rows.append({"afdeling_id": afd_id, "artnavn": navn, "antal": int(rec["bemaerk_antal"])})
                    except (KeyError, ValueError, TypeError):
                        continue
        await _insert_chunks(session, BemaerkThreshold, rows)

    if not (await session.execute(select(func.count()).select_from(PhenologyWindow))).scalar():
        rows = []
        with (SEED_DIR / "faenologi.csv").open(encoding="utf-8-sig") as f:
            for rec in csv.DictReader(f, delimiter=";"):
                if rec.get("Artnavn") and rec.get("Datofra") and rec.get("Datotil"):
                    rows.append({"artnavn": rec["Artnavn"].strip().strip("[]"), "fra": rec["Datofra"], "til": rec["Datotil"]})
        await _insert_chunks(session, PhenologyWindow, rows)

    if not (await session.execute(select(func.count()).select_from(Kommune))).scalar():
        await _seed_kommuner(session)

    await session.commit()
    await bump("reference")


async def _insert_chunks(session: AsyncSession, model, rows: list[dict], size: int = 2000) -> None:
    for i in range(0, len(rows), size):
        await session.execute(insert(model).values(rows[i:i + size]).on_conflict_do_nothing())


async def _seed_species(session: AsyncSession) -> None:
    latin: dict[int, str] = {}
    with (SEED_DIR / "arter_dof_content.csv").open(encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f, delimiter=","):
            try:
                latin[int(rec["Artsid"])] = rec.get("Latin") or ""
            except (KeyError, ValueError):
                continue
    rows = []
    with (SEED_DIR / "arter_filter_klassificeret.csv").open(encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f, delimiter=";"):
            try:
                artnr = int(rec["artsid"])
            except (KeyError, ValueError):
                continue
            navn = (rec.get("artsnavn") or "").strip().strip("[]")
            klass = (rec.get("klassifikation") or "Alm").strip() or "Alm"
            rows.append({
                "artnr": artnr, "navn": navn, "latin": latin.get(artnr), "klass": klass,
                "art_type": "art" if is_listable_name(navn) else "ub", "list_artnr": None, "excluded": False,
            })
    await _insert_chunks(session, Species, rows)
    await resolve_list_species(session)


async def resolve_list_species(session: AsyncSession) -> None:
    """Sæt list_artnr: arter tæller som sig selv, underarter som arten (eller sig selv, hvis arten ikke findes i
    DOFbasen, fx Klippedue (tamdue)), resten tæller ikke. [Klammer] om navnet gør stadig fundet ikke-krydsbart."""
    species = (await session.execute(select(Species.artnr, Species.navn, Species.art_type))).all()
    by_name = {navn.strip("[]"): artnr for artnr, navn, typ in species if typ == "art" and "(" not in navn}
    for artnr, navn, typ in species:
        target = None
        if typ in ("art", "underart") and is_listable_name(navn):
            target = by_name.get(base_name(navn), artnr if "(" not in navn or typ == "underart" else None)
        await session.execute(text("UPDATE species SET list_artnr = :t WHERE artnr = :a AND list_artnr IS DISTINCT FROM :t"),
                              {"t": target, "a": artnr})


async def _seed_kommuner(session: AsyncSession) -> None:
    dof_ids: dict[str, int] = {}
    with (SEED_DIR / "kommuner.csv").open(encoding="utf-8") as f:
        for rec in csv.DictReader(f, delimiter=";"):
            navn = rec["navn"].strip().replace("Århus", "Aarhus")
            dof_ids[navn] = int(rec["id"])
    data = json.loads((SEED_DIR / "kommuner.geojson").read_text(encoding="utf-8"))
    parts: dict[str, tuple[str, list[dict]]] = {}
    for feat in data["features"]:
        props = feat["properties"]
        kode = props["KOMKODE"]
        parts.setdefault(kode, (props["KOMNAVN"], []))[1].append(feat["geometry"])
    for kode, (navn, geoms) in parts.items():
        await session.execute(text("""
            INSERT INTO kommuner (kode, navn, dof_id, geom, geom_simple)
            SELECT :kode, :navn, :dof, g, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology(g, 0.002)), 3))
            FROM (SELECT ST_Multi(ST_CollectionExtract(ST_Union(ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(x), 4326)))), 3)) AS g
                  FROM unnest(CAST(:geoms AS text[])) AS x) s
            ON CONFLICT (kode) DO NOTHING
        """), {"kode": kode, "navn": navn, "dof": dof_ids.get(navn), "geoms": [json.dumps(g) for g in geoms]})


# ---------------------------------------------------------------- parsere til DOFbasen-sider

_TR = re.compile(r"<tr([^>]*)>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)


def parse_artdata(html_text: str, list_kind: str) -> list[dict]:
    """artdata.php?list=art|hybrid|ub|andre → [{artnr, navn, klass, art_type}]"""
    out = []
    for attrs, body in _TR.findall(html_text):
        tds = _TD.findall(body)
        if len(tds) != 4:
            continue
        nr = clean_text(tds[0])
        if not nr.isdigit():
            continue
        classes = " ".join(re.findall(r'class=["\']([^"\']+)["\']', attrs + body, re.IGNORECASE))
        klass = "SU" if re.search(r"\bsu\b", classes) else "SUB" if re.search(r"\bsubart\b", classes) else None
        navn = clean_text(tds[2])
        art_type = {"art": "art", "hybrid": "hybrid", "ub": "ub", "andre": "andre"}[list_kind]
        if art_type == "art" and "(" in navn:
            art_type = "underart"
        out.append({"artnr": int(nr), "navn": navn, "latin": clean_text(tds[3]) or None, "klass": klass, "art_type": art_type})
    return out


def parse_status_list(html_text: str) -> set[int]:
    """sudata.php / subdata.php → artsnumre."""
    ids = set()
    for _, body in _TR.findall(html_text):
        tds = _TD.findall(body)
        if tds and clean_text(tds[0]).isdigit():
            ids.add(int(clean_text(tds[0])))
    return ids


_BEMAERK_ROW = re.compile(
    r"<tr>\s*<td>\s*(?:<span[^>]*>)?(?P<art>[^<]+?)(?:</span>)?\s*</td>\s*"
    r"<td[^>]*\balign=[\"']right[\"'][^>]*>\s*(?P<n>[0-9][0-9 .,]*)\s*</td>",
    re.IGNORECASE | re.DOTALL,
)


def parse_bemaerk(html_text: str) -> dict[str, int]:
    out = {}
    for m in _BEMAERK_ROW.finditer(html_text):
        n = re.sub(r"[^\d]", "", m.group("n"))
        if n:
            out[clean_text(m.group("art")).strip("[]")] = int(n)
    return out


_WS_ROW = re.compile(
    r"<tr><td>(\d+)</td><td[^>]*>.*?</td><td>(?:<span[^>]*>)?([^<]+)(?:</span>)?</td><td>\(([\d/]+)-([\d/]+)\)</td></tr>",
    re.IGNORECASE | re.DOTALL,
)


def parse_wsdata(html_text: str) -> list[tuple[str, str, str]]:
    def ddmm(s: str) -> str:
        d, m = s.split("/")
        return f"{int(d):02d}-{int(m):02d}"

    return [(clean_text(navn).strip("[]"), ddmm(a), ddmm(b)) for _, navn, a, b in _WS_ROW.findall(html_text)]


# ---------------------------------------------------------------- anvend hentede data


async def apply_artdata(session: AsyncSession, rows: list[dict]) -> int:
    for r in rows:
        stmt = insert(Species).values(
            artnr=r["artnr"], navn=r["navn"], latin=r["latin"], klass=r["klass"] or "Alm",
            art_type=r["art_type"], excluded=False,
        )
        update = {"navn": stmt.excluded.navn, "art_type": stmt.excluded.art_type,
                  "latin": func.coalesce(stmt.excluded.latin, Species.latin)}
        if r["klass"]:
            update["klass"] = stmt.excluded.klass
        await session.execute(stmt.on_conflict_do_update(index_elements=["artnr"], set_=update))
    await resolve_list_species(session)
    await session.commit()
    await bump("reference")
    return len(rows)


async def apply_status(session: AsyncSession, klass: str, ids: set[int]) -> int:
    if not ids:
        return 0
    if klass == "SU":
        await session.execute(text("UPDATE species SET klass = 'SU' WHERE artnr = ANY(:ids)"), {"ids": list(ids)})
    else:
        await session.execute(text("UPDATE species SET klass = 'SUB' WHERE artnr = ANY(:ids) AND klass <> 'SU'"), {"ids": list(ids)})
    await session.commit()
    await bump("reference")
    return len(ids)


async def apply_bemaerk(session: AsyncSession, afdeling_id: int, thresholds: dict[str, int]) -> int:
    if not thresholds:
        return 0
    await session.execute(delete(BemaerkThreshold).where(BemaerkThreshold.afdeling_id == afdeling_id))
    await _insert_chunks(session, BemaerkThreshold, [
        {"afdeling_id": afdeling_id, "artnavn": k, "antal": v} for k, v in thresholds.items()
    ])
    await session.commit()
    await bump("reference")
    return len(thresholds)


async def apply_windows(session: AsyncSession, season: str, rows: list[tuple[str, str, str]]) -> int:
    """wsdata sommer/vinter. Vinduer fra begge sider gemmes samlet; den ene side erstattes ad gangen."""
    if not rows:
        return 0
    names = sorted({r[0] for r in rows})
    await session.execute(delete(PhenologyWindow).where(PhenologyWindow.artnavn.in_(names)))
    await _insert_chunks(session, PhenologyWindow, [{"artnavn": n, "fra": a, "til": b} for n, a, b in rows])
    await session.commit()
    await bump("reference")
    return len(rows)


LIST_FIX_KEY = "list_species_v2"


async def fix_list_species(session: AsyncSession) -> None:
    """Én gang: underarter fik ved en fejl 'tæller ikke' ('sp.' ramte '(ssp. …)'). Sæt list_artnr igen og byg de
    forudberegnede tal (Arter pr. år og lokalitetsoversigten) for alle år."""
    if await session.get(Setting, LIST_FIX_KEY) is not None or not (await session.execute(select(func.count()).select_from(Species))).scalar():
        return
    await resolve_list_species(session)
    session.add(Setting(key=LIST_FIX_KEY, value={"at": datetime.now(timezone.utc).isoformat()}))
    await session.commit()
    for v in ("reference", "lists", "regions", "locyear", "feed"):
        await bump(v)
    first = (await session.execute(text("SELECT extract(year FROM min(dato))::int FROM observations"))).scalar()
    if first:
        await worker_command("regions", from_year=None)
        await worker_command("location_years", years=list(range(first, dk_today().year + 1)))
