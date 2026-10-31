"""Artikler → Admin → Emneord: gem mange rettelser på én gang, og eksport/import som CSV (fx til klassificering med en AI).

Eksporten har én række pr. emneord med id, navn, slags, artnr, artsnavn, skjult, antal artikler og op til tre eksempeltitler.
Importen læser en CSV (komma, semikolon eller tabulator) eller JSON med de samme kolonner og returnerer kun de rækker, der
afviger fra det, der står nu – de gemmes ikke, men lægges ind i admin-tabellen som ugemte ændringer, så de kan gennemses først.
Rækker kendes på id (ellers på det nøjagtige navn). En art kan angives med artnr eller artsnavn.
"""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import ArtTag
from portal.services import article_species
from portal.services.article_edit import TAG_KINDS, Invalid, apply_tag, reindex_tag_articles

MAX_CHANGES = 5000
COLUMNS = ["id", "navn", "slags", "artnr", "art", "skjult", "artikler", "eksempler"]
KIND_ALIASES = {"år": "aar", "aar": "aar", "emne": "emne", "emner": "emne", "sted": "sted", "steder": "sted", "art": "art", "arter": "art",
                "publikation": "publikation", "publikationer": "publikation", "type": "type", "typer": "type"}
COL_ALIASES = {"id": "id", "navn": "navn", "name": "navn", "emneord": "navn", "slags": "slags", "kind": "slags", "type": "slags",
               "artnr": "artnr", "art": "art", "artsnavn": "art", "species": "art", "skjult": "skjult", "hidden": "skjult"}
TRUE, FALSE = {"ja", "j", "x", "1", "true", "sand", "yes", "y"}, {"nej", "n", "0", "false", "falsk", "no", ""}


async def save_many(session: AsyncSession, changes: list[dict]) -> int:
    """Gem en liste af rettelser ({id, navn?, kind?, artnr?, hidden?}) i én transaktion. Alt eller intet. Returnerer antal emneord."""
    if len(changes) > MAX_CHANGES:
        raise Invalid(f"Højst {MAX_CHANGES} ændringer ad gangen")
    ids = {int(c["id"]) for c in changes}
    tags = {t.id: t for t in (await session.execute(select(ArtTag).where(ArtTag.id.in_(ids or {0})))).scalars()}
    if missing := ids - tags.keys():
        raise Invalid(f"{len(missing)} emneord findes ikke længere (fx id {min(missing)}) – genindlæs siden")
    names: dict[str, int] = {}
    for c in changes:  # navnesammenfald inden for samme omgang
        navn = (c.get("navn") or tags[int(c["id"])].navn).strip().lower()
        if navn in names and names[navn] != int(c["id"]):
            raise Invalid(f"To emneord kan ikke begge hedde «{c.get('navn') or navn}» – brug «Flet»")
        names[navn] = int(c["id"])
    reindex: set[int] = set()
    try:
        # To trin, så to emneord kan bytte navn uden at ramme det unikke indeks undervejs
        for c in changes:
            t = tags[int(c["id"])]
            if c.get("navn") and c["navn"].strip().lower() != t.navn_lc:
                t.navn_lc = f"#omdøbes:{t.id}#"
        await session.flush()
        for c in changes:
            t = tags[int(c["id"])]
            if await apply_tag(session, t, navn=c.get("navn"), kind=c.get("kind"), artnr=c.get("artnr"), hidden=c.get("hidden"), batch_ids=ids):
                reindex.add(t.id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await bump("articles")
    await reindex_tag_articles(session, reindex)
    return len(changes)


async def export_rows(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT t.id, t.navn, t.kind, t.artnr, s.navn AS art, t.hidden, count(x.article_id) AS n,
               (SELECT string_agg(title, ' | ') FROM (SELECT a.title FROM art_article_tags y JOIN art_articles a ON a.id = y.article_id
                 WHERE y.tag_id = t.id ORDER BY a.dato DESC NULLS LAST LIMIT 3) e) AS eksempler
        FROM art_tags t LEFT JOIN art_article_tags x ON x.tag_id = t.id LEFT JOIN species s ON s.artnr = t.artnr
        GROUP BY t.id, s.navn ORDER BY lower(t.navn)"""))).mappings().all()
    return [dict(r) for r in rows]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([r["id"], r["navn"], r["kind"], r["artnr"] or "", r["art"] or "", "ja" if r["hidden"] else "nej", r["n"], r["eksempler"] or ""])
    return "﻿" + buf.getvalue()  # BOM: Excel åbner den som UTF-8


class Semicolon(csv.excel):
    delimiter = ";"


def _records(data: bytes) -> list[dict]:
    raw = data.decode("utf-8-sig", "replace").strip()
    if raw.startswith("```"):  # svar kopieret fra en chat
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip() if "\n" in raw else ""
    if raw.startswith(("[", "{")):
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            raise Invalid(f"Ugyldig JSON: {exc}") from exc
        obj = obj.get("emneord", obj.get("rows", [])) if isinstance(obj, dict) else obj
        if not isinstance(obj, list) or not all(isinstance(x, dict) for x in obj):
            raise Invalid("JSON skal være en liste af objekter")
        return obj
    try:
        dialect = csv.Sniffer().sniff(raw.split("\n", 1)[0], delimiters=";,\t")
    except csv.Error:
        dialect = Semicolon
    return list(csv.DictReader(io.StringIO(raw), dialect=dialect))


def _norm(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        col = COL_ALIASES.get(str(k or "").strip().lower())
        if col:
            out[col] = "" if v is None else str(v).strip()
    return out


async def parse_import(session: AsyncSession, data: bytes) -> dict:
    """Læs en eksport, der er rettet til. Returnerer {"changes": [...], "warnings": [...], "rows": n}; intet gemmes."""
    recs = _records(data)
    if len(recs) > MAX_CHANGES * 2:
        raise Invalid("Filen har for mange rækker")
    current = {t.id: t for t in (await session.execute(select(ArtTag))).scalars()}
    by_name = {t.navn_lc: t for t in current.values()}
    matcher = await article_species.load_matcher(session)
    species = {r[0] for r in (await session.execute(text("SELECT artnr FROM species"))).all()}
    changes, warnings = [], []
    for i, rec in enumerate(recs, 2):  # linje 1 er overskrifterne
        r = _norm(rec)
        if not r:
            continue
        t = current.get(int(r["id"])) if r.get("id", "").isdigit() else None
        if t is None and r.get("navn"):
            t = by_name.get(r["navn"].lower())
        if t is None:
            warnings.append(f"Linje {i}: emneordet {r.get('id') or r.get('navn') or '?'} findes ikke – sprunget over")
            continue
        c: dict = {}
        if r.get("navn") and r["navn"] != t.navn and r.get("id", "").isdigit():  # navnet kan kun rettes, når rækken kendes på id
            c["navn"] = r["navn"][:120]
        if r.get("slags"):
            kind = KIND_ALIASES.get(r["slags"].lower())
            if kind is None or kind not in TAG_KINDS:
                warnings.append(f"Linje {i} ({t.navn}): ukendt slags «{r['slags']}» – sprunget over")
                continue
            if kind != t.kind:
                c["kind"] = kind
        kind = c.get("kind", t.kind)
        artnr = None
        if r.get("artnr"):
            if not r["artnr"].isdigit() or int(r["artnr"]) not in species:
                warnings.append(f"Linje {i} ({t.navn}): ukendt artnr {r['artnr']}")
            else:
                artnr = int(r["artnr"])
        elif r.get("art"):
            artnr = matcher.tag_species(r["art"])
            if artnr is None:
                warnings.append(f"Linje {i} ({t.navn}): arten «{r['art']}» kendes ikke – angiv artnr")
        if kind == "art" and artnr is None and t.artnr is None:
            warnings.append(f"Linje {i} ({t.navn}): slags «art» uden artnr – emneordet kobles ikke til en art")
        if kind == "art" and artnr is not None and artnr != t.artnr:
            c["artnr"] = artnr
        if "skjult" in r:
            v = r["skjult"].lower()
            if v in TRUE or v in FALSE:
                if (v in TRUE) != t.hidden:
                    c["hidden"] = v in TRUE
            else:
                warnings.append(f"Linje {i} ({t.navn}): skjult skal være ja eller nej, ikke «{r['skjult']}»")
        if c:
            changes.append({"id": t.id, **c})
    if len({c["id"] for c in changes}) != len(changes):
        seen: dict[int, dict] = {}
        for c in changes:
            seen.setdefault(c["id"], {}).update(c)
        warnings.append("Nogle emneord stod der flere gange – den sidste række gælder")
        changes = list(seen.values())
    return {"changes": changes, "warnings": warnings[:200], "rows": len(recs)}
