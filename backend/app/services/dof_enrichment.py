from __future__ import annotations

import html
import csv
import re
import time
import unicodedata
from dataclasses import dataclass, field

import httpx

from backend.app.api.schemas import ObservationIngest


REGION_LISTS = {
    "københavn": "kbh",
    "koebenhavn": "kbh",
    "nordsjælland": "nsjl",
    "nordsjaelland": "nsjl",
    "vestsjælland": "vsjl",
    "vestsjaelland": "vsjl",
    "storstrøm": "ss",
    "storstroem": "ss",
    "bornholm": "b",
    "fyn": "f",
    "sønderjylland": "sdrj",
    "soenderjylland": "sdrj",
    "sydøstjylland": "soej",
    "syoestjylland": "soej",
    "sydvestjylland": "svj",
    "vestjylland": "vj",
    "østjylland": "oej",
    "oestjylland": "oej",
    "nordvestjylland": "nvj",
    "nordjylland": "nj",
}


def normalize_name(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value or ""))
    return " ".join(value.replace("\xa0", " ").casefold().split())


def clean_name(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split()).strip()


def parse_names(html_text: str) -> set[str]:
    names: set[str] = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.I | re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.I | re.S)
        if len(cells) >= 3:
            text = re.sub(r"<[^>]+>", "", cells[2])
            name = normalize_name(clean_name(text))
            if name:
                names.add(name)
    return names


def parse_remarkable(html_text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    row_re = re.compile(r"<tr>\s*<td>\s*(?:<span[^>]*>)?(?P<name>[^<]+).*?</td>\s*<td[^>]*>\s*(?P<count>[0-9][0-9 .,]*)", re.I | re.S)
    for match in row_re.finditer(html_text):
        number = re.sub(r"[^0-9]", "", match.group("count"))
        if number:
            values[normalize_name(clean_name(match.group("name")))] = int(number)
    return values


@dataclass
class DofEnrichment:
    su: set[str] = field(default_factory=set)
    sub: set[str] = field(default_factory=set)
    remarkable: dict[str, int] = field(default_factory=dict)
    remarkable_by_region: dict[str, dict[str, int]] = field(default_factory=dict)

    def classify(self, species: str, category: str | None = None, department: str | None = None) -> tuple[str, int | None]:
        name = normalize_name(species)
        category_name = normalize_name(category)
        if name in self.su or category_name == "su":
            return "SU", self.remarkable.get(name)
        if name in self.sub or category_name == "sub":
            return "SUB", self.remarkable.get(name)
        region_key = next(
            (code for label, code in REGION_LISTS.items() if label in normalize_name(department)),
            None,
        )
        threshold = (
            self.remarkable_by_region.get(region_key or "", {}).get(name)
            or self.remarkable.get(name)
        )
        if threshold is not None or category_name in {"bemaerk", "bemærk"}:
            return "BEMÆRK", threshold
        if category_name in {"faenologi", "fænologi"}:
            return "FÆNOLOGI", None
        return "ALM", None


_CACHE: DofEnrichment | None = None
_CACHE_EXPIRES_AT = 0.0


def invalidate_dof_enrichment_cache() -> None:
    global _CACHE, _CACHE_EXPIRES_AT
    _CACHE = None
    _CACHE_EXPIRES_AT = 0.0


def _load_local_remarkable() -> dict[str, dict[str, int]]:
    from backend.app.services.reference_data import REFERENCE_DATA_DIR, REGION_FILES

    result: dict[str, dict[str, int]] = {}
    for region, filename in REGION_FILES.items():
        path = REFERENCE_DATA_DIR / filename
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as file:
            values = {}
            for row in csv.DictReader(file, delimiter=";"):
                try:
                    values[normalize_name(row.get("artsnavn"))] = int(row.get("bemaerk_antal") or 0)
                except (TypeError, ValueError):
                    continue
            result[region] = values
    return result


async def load_dof_enrichment() -> DofEnrichment:
    global _CACHE, _CACHE_EXPIRES_AT
    if _CACHE is not None and time.monotonic() < _CACHE_EXPIRES_AT:
        return _CACHE
    enrichment = DofEnrichment()
    headers = {"User-Agent": "DOF.tool enrichment/1.0"}
    local_remarkable = _load_local_remarkable()
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        for endpoint, target in (("https://dofbasen.dk/opslag/sudata.php", enrichment.su), ("https://dofbasen.dk/opslag/subdata.php", enrichment.sub)):
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
                target.update(parse_names(response.text))
            except httpx.HTTPError:
                continue
        for region in set(REGION_LISTS.values()):
            if region in local_remarkable:
                enrichment.remarkable_by_region[region] = local_remarkable[region]
                for species, count in local_remarkable[region].items():
                    enrichment.remarkable.setdefault(species, count)
                continue
            try:
                response = await client.get(f"https://dofbasen.dk/opslag/bemaerk.php?list={region}")
                response.raise_for_status()
                regional_values = parse_remarkable(response.text)
                enrichment.remarkable_by_region[region] = regional_values
                for species, count in regional_values.items():
                    enrichment.remarkable.setdefault(species, count)
            except httpx.HTTPError:
                continue
    _CACHE = enrichment
    _CACHE_EXPIRES_AT = time.monotonic() + 3600
    return enrichment


async def enrich_observations(rows: list[ObservationIngest]) -> list[ObservationIngest]:
    enrichment = await load_dof_enrichment()
    result: list[ObservationIngest] = []
    for row in rows:
        classification, threshold = enrichment.classify(
            row.species, row.category, row.dof_afdeling
        )
        result.append(
            row.model_copy(
                update={
                    "enriched_class": classification,
                    "remarkable_count": threshold,
                }
            )
        )
    return result
