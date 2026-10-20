"""Den Danske Liste som kilde til listekategorier.

Kategorierne A–F følger AERC/SU: A (vild siden 1950), B (kun vild 1800–1949) og C (indført, selvopretholdende) er optaget på
den danske liste og krydsbare; D (tvivl om oprindelse), E (undslupne/udsatte) og F (kun registreret før 1800) er ikke.

To kilder lægges sammen:
  * A, B og C kommer fra Netfugls DK-liste (services/netfugl_list.py). Den følger Sjældenhedsudvalget løbende og bruger
    DOFbasens latinske navne, så den er mere opdateret og matcher portalens arter bedre end PDF'en.
  * D, E og F (og underarter, der afviger fra artens kategori) kommer fra PDF'en 'The Danish List' (seed/danish_list.json,
    lavet af tools/parse_danish_list.py), for arter der ikke står som A, B eller C hos Netfugl. Er de to kilder uenige,
    går Netfugl forud, og uenigheden vises i rapporten.

Resultatet lægges ind som rækker i species_categories – med fuld historik: en række der afløses markeres som fjernet, og
rækker oprettet af moderatorer røres aldrig (de vises som konflikter i rapporten).

Matchning: videnskabeligt navn mod portalens artsliste, ellers dansk navn. En art som A får kun en række, hvis portalen
ellers ville behandle den som ikke-krydsbar ([klammer] om navnet i DOFbasen).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.cache import bump
from portal.models import Setting
from portal.services import netfugl_list

log = logging.getLogger("portal.danish_list")

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "danish_list.json"
SEED_KEY = "categories_danish_list_2025"
NETFUGL_KEY = "netfugl_dk_list"  # senest hentede kopi af Netfugls liste (ellers seed/netfugl_dk.json)
MARK = "Den Danske Liste"
# Arter med en kendt dato for første godkendte fund: før datoen er fundene ikke krydsbare (kategori før), derefter A.
# Dværgsnegås: første godkendte kategori A-fund 7. oktober 2023.
FIRST_ACCEPTED = {"Anser rossii": (date(2023, 10, 7), "E")}


def _norm(name: str | None) -> str:
    return (name or "").strip().strip("[]").strip().lower()


async def netfugl_copy(session: AsyncSession) -> dict:
    """Netfugl-listen: den senest hentede (admin → 'Hent nyeste fra Netfugl'), ellers den medfølgende kopi."""
    row = await session.get(Setting, NETFUGL_KEY)
    return row.value if row is not None and isinstance(row.value, dict) and row.value.get("species") else netfugl_list.load_seed()


def merge(pdf: dict, netfugl: dict) -> dict:
    """Samlet liste: Netfugl for A/B/C, PDF'en for D/E/F. Returnerer {species, subspecies, disagreements, sources}."""
    species = {latin: {"danish": e["danish"], "kategori": e["kategori"], "kilde": "netfugl"} for latin, e in netfugl["species"].items()}
    nf_danish = {_norm(e["danish"]): latin for latin, e in netfugl["species"].items()}
    disagreements = []
    for latin, e in pdf["species"].items():
        twin = latin if latin in species else nf_danish.get(_norm(e["danish"]))  # samme art under et andet latinsk navn
        if twin is not None:
            if species[twin]["kategori"] != e["kategori"]:
                disagreements.append({"latin": twin, "navn": species[twin]["danish"], "netfugl": species[twin]["kategori"], "pdf": e["kategori"]})
            continue
        if e["kategori"] in "DEF":
            species[latin] = {"danish": e["danish"], "kategori": e["kategori"], "kilde": "pdf"}
    # Underarter fra PDF'en gælder kun, hvis arten er på listen (som A, B eller C hos Netfugl)
    subspecies = {}
    for latin, e in pdf["subspecies"].items():
        parent = species.get(e["species"])
        if parent and parent["kategori"] in "ABC":
            subspecies[latin] = e
    return {"species": species, "subspecies": subspecies, "disagreements": disagreements,
            "sources": {"netfugl": {"fetched": netfugl.get("fetched"), "url": netfugl.get("url")}, "pdf": pdf.get("source")}}


async def load(session: AsyncSession) -> dict:
    return merge(json.loads(SEED_FILE.read_text(encoding="utf-8")), await netfugl_copy(session))


async def refresh_netfugl(session: AsyncSession) -> dict:
    """Hent Netfugls liste (ét kald) og gem den; bruges af de følgende planer og importer."""
    data = await netfugl_list.fetch()
    row = await session.get(Setting, NETFUGL_KEY)
    if row is None:
        session.add(Setting(key=NETFUGL_KEY, value=data))
    else:
        row.value = data
    await session.commit()
    return data


async def _portal_species(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT s.artnr, s.navn, s.latin, s.art_type, s.list_artnr, s.navn LIKE '[%' AS bracket,
               (SELECT count(*) FROM species_categories c WHERE c.artnr = s.artnr AND c.removed_at IS NULL) AS n_rows
        FROM species s WHERE NOT s.excluded
    """))).mappings().all()
    return [dict(r) for r in rows]


async def _active_rows(session: AsyncSession) -> dict[int, list[dict]]:
    rows = (await session.execute(text("""
        SELECT id, artnr, kategori, valid_from, note, created_by FROM species_categories WHERE removed_at IS NULL
    """))).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["artnr"], []).append(dict(r))
    return out


def _is_ours(row: dict) -> bool:
    """Rækker der kan afløses automatisk: fra denne import eller fra startlisten (ingen bruger bag)."""
    note = row.get("note") or ""
    return row["created_by"] is None and (note.startswith(MARK) or note.startswith("Startliste"))


async def plan(session: AsyncSession) -> dict:
    """Hvad en import vil gøre – uden at skrive noget. 'changes' er (artnr, kategori, valid_from, note, supersedes)."""
    data = await load(session)
    species = await _portal_species(session)
    by_latin: dict[str, list[dict]] = {}
    by_dansk: dict[str, list[dict]] = {}
    for s in species:
        if s["art_type"] in ("art", "underart"):
            by_latin.setdefault(_norm(s["latin"]), []).append(s)
            by_dansk.setdefault(_norm(s["navn"]), []).append(s)
    active = await _active_rows(session)

    changes, kept, conflicts, unmatched, matched = [], [], [], [], 0
    seen_artnr: set[int] = set()

    def want(artnr: int, kat: str, latin: str, danish: str, bracket: bool) -> None:
        """Opret/afløs rækken for en art eller underart."""
        wanted = [(FIRST_ACCEPTED[latin][1], None, f"{MARK}: ikke krydsbar før første godkendte fund"),
                  ("A", FIRST_ACCEPTED[latin][0], f"{MARK}: kategori A fra første godkendte fund")] \
            if latin in FIRST_ACCEPTED else [(kat, None, f"{MARK}: kategori {kat}")]
        rows = active.get(artnr, [])
        mine = [r for r in rows if _is_ours(r)]
        foreign = [r for r in rows if not _is_ours(r)]
        if foreign:
            conflicts.append({"artnr": artnr, "navn": danish, "latin": latin, "liste": kat,
                              "portal": [{"kategori": r["kategori"], "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None} for r in foreign]})
            return
        # A uden [klammer] er standard og behøver ingen række
        if len(wanted) == 1 and kat == "A" and not bracket:
            for r in mine:  # en tidligere importeret række der ikke længere behøves, afløses
                changes.append({"artnr": artnr, "navn": danish, "latin": latin, "kategori": None, "valid_from": None,
                                "note": None, "supersedes": [r["id"]]})
            return
        same = len(mine) == len(wanted) and all(
            any(m["kategori"] == k and m["valid_from"] == vf for m in mine) for k, vf, _ in wanted)
        if same:
            kept.append(artnr)
            return
        for i, (k, vf, note) in enumerate(wanted):
            changes.append({"artnr": artnr, "navn": danish, "latin": latin, "kategori": k, "valid_from": vf, "note": note,
                            "supersedes": [r["id"] for r in mine] if i == 0 else []})

    for latin, e in data["species"].items():
        cands = [s for s in by_latin.get(_norm(latin), []) if s["art_type"] == "art"] or \
                [s for s in by_dansk.get(_norm(e["danish"]), []) if s["art_type"] == "art"]
        if not cands:
            unmatched.append({"latin": latin, "navn": e["danish"], "kategori": e["kategori"]})
            continue
        matched += 1
        for s in cands[:1]:
            seen_artnr.add(s["artnr"])
            want(s["artnr"], e["kategori"], latin, s["navn"], bool(s["bracket"]))
    for latin, e in data["subspecies"].items():
        cands = [s for s in by_latin.get(_norm(latin), []) if s["art_type"] == "underart"]
        if cands:
            want(cands[0]["artnr"], e["kategori"], latin, cands[0]["navn"], bool(cands[0]["bracket"]))
        else:
            unmatched.append({"latin": latin, "navn": e["danish"], "kategori": e["kategori"], "underart": True})
    # Portalens arter med [klammer], som ikke står på listen: de beholder standarden (kategori D)
    off_list = [{"artnr": s["artnr"], "navn": s["navn"]} for s in species
                if s["art_type"] == "art" and s["bracket"] and s["artnr"] not in seen_artnr]
    summary: dict[str, int] = {}
    for c in changes:
        if c["kategori"]:
            summary[c["kategori"]] = summary.get(c["kategori"], 0) + 1
    return {"matched": matched, "of": len(data["species"]), "changes": changes, "summary": summary, "unchanged": len(kept),
            "conflicts": conflicts, "unmatched": unmatched, "bracket_off_list": off_list, "sources": data["sources"],
            "disagreements": data["disagreements"]}


async def apply(session: AsyncSession, user_id: int | None = None) -> dict:
    """Skriv ændringerne. Idempotent: en ny kørsel uden ændringer i listen gør ingenting."""
    p = await plan(session)
    n = 0
    for c in p["changes"]:
        for rid in c["supersedes"]:
            await session.execute(text("UPDATE species_categories SET removed_at = now(), removed_by = :u WHERE id = :i AND removed_at IS NULL"),
                                  {"u": user_id, "i": rid})
        if c["kategori"]:
            await session.execute(text("""
                INSERT INTO species_categories (artnr, kategori, valid_from, note, created_by) VALUES (:a, :k, :f, :n, :u)
            """), {"a": c["artnr"], "k": c["kategori"], "f": c["valid_from"], "n": c["note"], "u": user_id})
            n += 1
    await session.commit()
    if p["changes"]:
        await bump("lists")
        await bump("reference")
    log.info("Den Danske Liste: %s rækker (%s), %s konflikter, %s uden match", n, p["summary"], len(p["conflicts"]), len(p["unmatched"]))
    return {**{k: v for k, v in p.items() if k != "changes"}, "written": n}


async def seed(session: AsyncSession) -> None:
    """Ved opstart: indlæs listen én gang, når portalens arter findes (og kategoriernes startliste er lagt ind)."""
    if await session.get(Setting, SEED_KEY) is not None:
        return
    if not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM species)"))).scalar():
        return
    report = await apply(session)
    session.add(Setting(key=SEED_KEY, value={"written": report["written"], "summary": report["summary"],
                                             "conflicts": len(report["conflicts"]), "unmatched": len(report["unmatched"])}))
    await session.commit()
