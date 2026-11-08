"""Regneark til download: CSV (semikolon, UTF-8 med BOM, som Excel på dansk forventer), ODS og XLSX.

ODS skrives direkte (en zip med content.xml), XLSX med openpyxl. Tal gemmes som tal, alt andet som tekst.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Sequence
from xml.sax.saxutils import escape

FORMATS = {"csv": "text/csv; charset=utf-8",
           "ods": "application/vnd.oasis.opendocument.spreadsheet",
           "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # ugyldige i XML


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Tekst, som Excel/LibreOffice læser som en formel, når en CSV åbnes (fx =HYPERLINK(…) eller =WEBSERVICE(…) i en note,
# som en anden bruger har skrevet). Rene tal som "-12,5" er ikke farlige og røres ikke.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"[+-]?\d+([.,]\d+)?")


def csv_cell(v):
    """Værdi til en CSV-celle: None bliver tom, og tekst, der ville blive en formel, får et ' foran (OWASP: CSV injection).
    Brug den for hver celle med indhold fra brugere (navne, noter, lokaliteter)."""
    if v is None:
        return ""
    if isinstance(v, str) and len(v) > 1 and v.startswith(_FORMULA_START) and not _PLAIN_NUMBER.fullmatch(v):
        return "'" + v
    return v


def write(fmt: str, columns: Sequence[str], rows: Iterable[Sequence], sheet: str = "Data") -> bytes:
    return {"csv": to_csv, "ods": to_ods, "xlsx": to_xlsx}[fmt](columns, rows, sheet)


def to_csv(columns: Sequence[str], rows: Iterable[Sequence], sheet: str = "") -> bytes:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(columns)
    for r in rows:
        w.writerow([csv_cell(v) for v in r])
    return buf.getvalue().encode()


def _text_cell(ws, s: str):
    """openpyxl gemmer tekst, der starter med '=', som en formel – en note som '=WEBSERVICE(…)' ville køre, når filen
    åbnes. Cellen tvinges derfor til tekst; indholdet bevares uændret (i modsætning til CSV, se csv_cell)."""
    if not s.startswith("="):
        return s
    from openpyxl.cell import WriteOnlyCell

    cell = WriteOnlyCell(ws, value=s)
    cell.data_type = "s"
    return cell


def to_xlsx(columns: Sequence[str], rows: Iterable[Sequence], sheet: str = "Data") -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet[:31])
    ws.freeze_panes = "A2"
    bold = Font(bold=True)
    head = []
    for c in columns:
        cell = WriteOnlyCell(ws, value=c)
        cell.font = bold
        head.append(cell)
    ws.append(head)
    for r in rows:
        ws.append([v if v is None or _num(v) else _text_cell(ws, _CTRL.sub("", str(v))) for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ods_cell(v) -> str:
    if v is None or v == "":
        return "<table:table-cell/>"
    if _num(v):
        return f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p></table:table-cell>'
    return f'<table:table-cell office:value-type="string"><text:p>{escape(_CTRL.sub("", str(v)))}</text:p></table:table-cell>'


def to_ods(columns: Sequence[str], rows: Iterable[Sequence], sheet: str = "Data") -> bytes:
    body = io.StringIO()
    body.write('<?xml version="1.0" encoding="UTF-8"?>'
               '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
               f'<office:body><office:spreadsheet><table:table table:name="{escape(sheet, {chr(34): "&quot;"})}">')
    for r in [columns, *rows]:
        body.write("<table:table-row>" + "".join(_ods_cell(v) for v in r) + "</table:table-row>")
    body.write("</table:table></office:spreadsheet></office:body></office:document-content>")
    manifest = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{FORMATS["ods"]}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '</manifest:manifest>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), FORMATS["ods"], compress_type=zipfile.ZIP_STORED)  # skal stå først, ukomprimeret
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("content.xml", body.getvalue())
    return buf.getvalue()
