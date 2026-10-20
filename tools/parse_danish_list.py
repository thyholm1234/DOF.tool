"""Læser Den Danske Liste (DOF's Sjældenhedsudvalg, PDF med bilag 1–4) og skriver en JSON-fil med kategori pr. art og
underart til portalens kategorier.

    python tools/parse_danish_list.py The_Danish_List.pdf server/portal/seed/danish_list.json

Kræver pdftotext (poppler). Bilag 1 = kategori A, B og C (krydsbare), bilag 2 = D, bilag 3 = E, bilag 4 = F.
Underarter står som forkortede rækker ("A. a. anser") under artens række og udvides til fuldt navn.
Kør med `python -I`, og læs kun PDF'en fra en mappe uden andre .py-filer.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict

APPENDIX = re.compile(r"^APPENDIX (\d): ")
CAT = r"[A-F]\*?(?:\d(?:\.\d)?)?"
ROW = re.compile(rf"^(?:(?:\(X\)|X)\s+)?(?P<cats>(?:{CAT}\s+)+)?(?P<latin>[A-Z][a-z]+ [a-z]+(?: [a-z]+)?|[A-Z]\. (?:[a-z]\. )?[a-z]+(?: / [a-z]+)?(?:\. [a-z]+\.?)?)(?: †)?(?:\s{{2,}}(?P<rest>.*))?$")
FULL = re.compile(r"^[A-Z][a-z]+ [a-z]+$")
ABBR = re.compile(r"^([A-Z])\. (?:([a-z])\. )?(.+)$")
LETTER = re.compile(r"^([A-F])")


def parse(pdf: str) -> dict:
    text = subprocess.run(["pdftotext", "-layout", pdf, "-"], check=True, capture_output=True, text=True).stdout
    appendix, species = 0, None
    taxa: dict[str, dict] = {}   # latin (art eller underart) → {level, danish, rows: [(appendix, cat)]}
    for raw in text.splitlines():
        if m := APPENDIX.match(raw.strip()):
            appendix, species = int(m.group(1)), None
            continue
        if not appendix or not raw.strip() or len(raw) - len(raw.lstrip()) > 30:
            continue  # før bilagene, tomme linjer og ombrudte noter (står langt til højre)
        m = ROW.match(raw.strip())
        if not m:
            continue
        latin, cats = m.group("latin"), (m.group("cats") or "").split()
        chunks = re.split(r"\s{2,}", (m.group("rest") or "").strip()) if m.group("rest") else []
        danish = chunks[0] if chunks else ""
        if FULL.match(latin):
            # Artens egen række (med eller uden kategori). Rækker uden kategori er overskrifter for underarter.
            if len(chunks) < 2 and not cats:
                continue
            species = latin
            t = taxa.setdefault(latin, {"level": "species", "danish": danish, "rows": []})
            t["danish"] = t["danish"] or danish
            for c in cats:
                t["rows"].append((appendix, c))
        else:
            a = ABBR.match(latin)
            tail = a.group(3) if a else ""
            indet = tail.startswith(("ssp.", "indet"))
            # Underartens forbogstav skal passe til artens slægt (bortset fra enkelte slåfejl i PDF'en ved "ssp. indet.")
            if not a or species is None or not (indet or species.startswith(a.group(1))):
                continue
            # "luteus / auratus" (underartsgruppe): første navn bruges, rækken tæller med for arten
            full = f"{species} ssp. indet." if indet else f"{species} {tail.split()[0]}"
            t = taxa.setdefault(full, {"level": "subspecies", "danish": danish, "species": species, "rows": []})
            t["danish"] = t["danish"] or danish
            for c in cats:
                t["rows"].append((appendix, c))
    return taxa


def best(rows: list[tuple[int, str]]) -> str | None:
    """Højeste kategori: bilag 1 går forud for D, E og F. Inden for bilag 1 tæller A, så C (en art der både er B og C,
    fx Stor Hornugle, lever i dag som indført bestand) og til sidst B (kun vild før 1950)."""
    by_app = defaultdict(set)
    for app, c in rows:
        by_app[app].add(LETTER.match(c).group(1))
    if 1 in by_app:
        return "A" if "A" in by_app[1] else "C" if "C" in by_app[1] else "B"
    for app, letter in ((2, "D"), (3, "E"), (4, "F")):
        if app in by_app:
            return letter
    return None


def build(taxa: dict) -> dict:
    species: dict[str, dict] = {}
    for latin, t in taxa.items():
        if t["level"] != "species":
            continue
        rows = list(t["rows"])
        for lat2, t2 in taxa.items():  # underartens rækker tæller med for arten
            if t2["level"] == "subspecies" and t2["species"] == latin:
                rows += t2["rows"]
        cat = best(rows)
        if cat:
            species[latin] = {"danish": t["danish"], "kategori": cat}
    subspecies: dict[str, dict] = {}
    for latin, t in taxa.items():
        if t["level"] != "subspecies" or latin.endswith("ssp. indet."):
            continue
        cat = best(t["rows"])
        sp = species.get(t["species"])
        # Kun underarter hvis krydsbarhed afviger fra artens (fx en D-underart af en A-art)
        if cat and sp and (cat in "ABC") != (sp["kategori"] in "ABC"):
            subspecies[latin] = {"danish": t["danish"], "kategori": cat, "species": t["species"]}
    return {"species": species, "subspecies": subspecies}


if __name__ == "__main__":
    pdf, out = sys.argv[1], sys.argv[2]
    result = build(parse(pdf))
    result["source"] = ("The Danish List: A Checklist of the Birds of Denmark (Olsen, K. & Danish Rarities Committee 2025, "
                        "DOFT 119: 105-112). Bilag 1–4: kategori A–C, D, E, F.")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    from collections import Counter
    print("arter:", dict(Counter(v["kategori"] for v in result["species"].values())), "| underarter:", dict(Counter(v["kategori"] for v in result["subspecies"].values())))
