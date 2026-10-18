from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import unicodedata
from pathlib import Path

import httpx


REFERENCE_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

REGION_FILES = {
    "kbh": "koebenhavn_bemaerk_parsed.csv",
    "nsjl": "nordsjaelland_bemaerk_parsed.csv",
    "vsjl": "vestsjaelland_bemaerk_parsed.csv",
    "ss": "storstroem_bemaerk_parsed.csv",
    "b": "bornholm_bemaerk_parsed.csv",
    "f": "fyn_bemaerk_parsed.csv",
    "sdrj": "soenderjylland_bemaerk_parsed.csv",
    "soej": "sydoestjylland_bemaerk_parsed.csv",
    "svj": "sydvestjylland_bemaerk_parsed.csv",
    "vj": "vestjylland_bemaerk_parsed.csv",
    "oej": "oestjylland_bemaerk_parsed.csv",
    "nvj": "nordvestjylland_bemaerk_parsed.csv",
    "nj": "nordjylland_bemaerk_parsed.csv",
}


def _clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKC", value).replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def _normalized_content(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lstrip("\ufeff")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip(" \t") for line in value.split("\n")).rstrip("\n")


def _write_if_changed(path: Path, content: str, *, encoding: str = "utf-8") -> bool:
    old = path.read_text(encoding="utf-8-sig" if path.exists() else encoding) if path.exists() else ""
    if _normalized_content(old) == _normalized_content(content):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding, newline="")
    return True


async def fetch_faenologi_csv() -> dict:
    rows: set[tuple[str, str, str, str]] = set()
    urls = (
        "https://dofbasen.dk/opslag/wsdata.php?tid=sommer",
        "https://dofbasen.dk/opslag/wsdata.php?tid=vinter",
    )
    pattern = re.compile(
        r"<tr>\s*<td>(\d+)</td>\s*<td[^>]*>.*?</td>\s*"
        r"<td>(?:<span[^>]*>)?([^<]+)(?:</span>)?</td>\s*"
        r"<td>\(([\d/]+)-([\d/]+)\)</td>\s*</tr>",
        re.IGNORECASE | re.DOTALL,
    )
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls:
            response = await client.get(url, headers={"User-Agent": "DOF.tool/1.0"})
            response.raise_for_status()
            text = response.content.decode("windows-1252", errors="replace")
            for artnr, species, date_from, date_to in pattern.findall(text):
                day_from, month_from = date_from.split("/")
                day_to, month_to = date_to.split("/")
                rows.add((artnr, _clean_text(species), f"{int(day_from):02d}-{int(month_from):02d}", f"{int(day_to):02d}-{int(month_to):02d}"))

    if not rows:
        raise ValueError("DOFbasen returnerede ingen fænologirækker.")
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(("Artnr", "Artnavn", "Datofra", "Datotil"))
    writer.writerows(sorted(rows, key=lambda row: (row[1].casefold(), row[2], row[3])))
    path = REFERENCE_DATA_DIR / "faenologi.csv"
    changed = _write_if_changed(path, output.getvalue(), encoding="utf-8-sig")
    return {"ok": True, "rows": len(rows), "changed": changed, "file": str(path.relative_to(REFERENCE_DATA_DIR.parent))}


async def fetch_bemaerk_csv() -> dict:
    row_pattern = re.compile(
        r"<tr>\s*<td>\s*(?:<span[^>]*>)?(?P<species>[^<]+?)(?:</span>)?\s*</td>\s*"
        r"<td[^>]*\balign=[\"']right[\"'][^>]*>\s*(?P<count>[0-9][0-9 .,]*)\s*</td>",
        re.IGNORECASE | re.DOTALL,
    )
    results = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for region, filename in REGION_FILES.items():
            result = {"file": filename, "rows_total": 0, "numeric_rows": 0, "changed": False, "error": None}
            try:
                response = await client.get(
                    f"https://dofbasen.dk/opslag/bemaerk.php?list={region}",
                    headers={"User-Agent": "DOF.tool/1.0"},
                )
                response.raise_for_status()
                text = response.content.decode(response.encoding or "windows-1252", errors="replace")
                rows = [(_clean_text(match.group("species")), re.sub(r"[^0-9]", "", match.group("count"))) for match in row_pattern.finditer(text)]
                rows = [(species, count) for species, count in rows if species and count]
                result["rows_total"] = len(rows)
                result["numeric_rows"] = len(rows)
                if not rows:
                    result["reason"] = "no-numeric-second-column"
                else:
                    content = "artsnavn;bemaerk_antal\n" + "\n".join(
                        f"{species};{count}" for species, count in sorted(rows, key=lambda row: (row[0].casefold(), int(row[1])))
                    ) + "\n"
                    result["changed"] = _write_if_changed(REFERENCE_DATA_DIR / filename, content)
            except (httpx.HTTPError, UnicodeError, ValueError) as error:
                result["error"] = str(error)
            results[region] = result
    return {"ok": True, "results": results, "changed": any(item["changed"] for item in results.values())}


def reference_data_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(REFERENCE_DATA_DIR.glob("*.csv")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()