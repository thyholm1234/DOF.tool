"""Netfugls DK-liste (https://www.netfugl.dk/artslister/dk): alle arter i kategori A, B og C.

Netfugl følger Sjældenhedsudvalgets beslutninger løbende og bruger de latinske navne som DOFbasen, så listen er mere
opdateret end PDF'en 'The Danish List' og matcher portalens arter bedre. Her læses siden (én tabel med SU-mærke, dansk
navn, latinsk navn og kategori). D, E og F står ikke på siden – de kommer fra PDF'en (se services/danish_list.py).
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

import httpx

from portal.config import get_settings

URL = "https://www.netfugl.dk/artslister/dk"
SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "netfugl_dk.json"
MIN_SPECIES = 450  # værn: en side med færre rækker er ikke den liste, vi forventer
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def parse(page: str) -> dict:
    """{latinsk navn: {danish, kategori, su}} for rækkerne i tabellen. Kaster ValueError, hvis siden ikke ligner listen."""
    species: dict[str, dict] = {}
    for tr in _ROW.findall(page):
        cells = [_text(c) for c in _CELL.findall(tr)]
        if len(cells) == 4 and cells[3] in ("A", "B", "C") and re.fullmatch(r"[A-Z][a-z]+ [a-z]+(?: [a-z]+)?", cells[2]):
            species[cells[2]] = {"danish": cells[1], "kategori": cells[3], "su": cells[0] == "*"}
    if len(species) < MIN_SPECIES:
        raise ValueError(f"Kun {len(species)} arter læst – siden har ændret format")
    return species


async def fetch() -> dict:
    """Hent siden (ét kald) og læs den. Returnerer {fetched, url, species}."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
                                 headers={"User-Agent": get_settings().dof_user_agent}) as client:
        resp = await client.get(URL)
        resp.raise_for_status()
    return {"fetched": date.today().isoformat(), "url": URL, "species": parse(resp.text)}


def load_seed() -> dict:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))
