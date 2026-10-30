"""Parsing af DOFbasens CSV-udtræk (search_result1.php?design=excel og arkivfilerne)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Iterator

COLUMNS = [
    "Dato", "Turtidfra", "Turtidtil", "Loknr", "Loknavn", "Artnr", "Artnavn", "Latin", "Sortering",
    "Antal", "Koen", "Adfkode", "Adfbeskrivelse", "Alderkode", "Dragtkode", "Dragtbeskrivelse",
    "Obserkode", "Fornavn", "Efternavn", "Obser_by", "Medobser", "Turnoter", "Fuglnoter", "Metode",
    "Obstidfra", "Obstidtil", "Hemmelig", "Kvalitet", "Turid", "Obsid", "DOF_afdeling",
    "lok_laengdegrad", "lok_breddegrad", "obs_laengdegrad", "obs_breddegrad", "radius",
    "obser_laengdegrad", "obser_breddegrad",
]

# Windows-1252-tegn der optræder når DOFbasen leverer latin-1
_SMART = str.maketrans({
    "\x80": "€", "\x82": ",", "\x84": "„", "\x85": "…", "\x86": "†", "\x87": "‡", "\x8b": "‹",
    "\x8c": "Œ", "\x91": "‘", "\x92": "’", "\x93": "“", "\x94": "”", "\x96": "–", "\x97": "—",
    "\x99": "™", "\x9b": "›", "\x9c": "œ", "\x9f": "Ÿ",
})



def decode(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text.translate(_SMART)


def _float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value.replace(".", "").replace(",", ".")))
        except ValueError:
            return None


def _date(value: str) -> date | None:
    value = (value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _time(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:5] if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", value) else None


def _clean(value: str | None, limit: int | None = None) -> str | None:
    value = (value or "").strip()
    if not value or value == "-":
        return None
    return value[:limit] if limit else value


@dataclass(slots=True)
class Row:
    obsid: int
    turid: int | None
    dato: date
    artnr: int
    artnavn: str
    latin: str | None
    sortering: int | None
    antal: int
    koen: str | None
    adfkode: str | None
    adftekst: str | None
    alderkode: str | None
    dragtkode: str | None
    dragttekst: str | None
    obserkode: str | None
    fornavn: str | None
    efternavn: str | None
    obser_by: str | None
    medobser: str | None
    turnoter: str | None
    fuglnoter: str | None
    metode: str | None
    obstidfra: str | None
    obstidtil: str | None
    turtidfra: str | None
    turtidtil: str | None
    hemmelig: int
    kvalitet: int | None
    loknr: int | None
    loknavn: str | None
    afdelinger: list[str] = field(default_factory=list)
    lok_lat: float | None = None
    lok_lon: float | None = None
    obs_lat: float | None = None
    obs_lon: float | None = None

    @property
    def time(self) -> str | None:
        return self.obstidfra or self.turtidfra or self.obstidtil or self.turtidtil


def to_row(rec: dict[str, str]) -> Row | None:
    obsid = _int(rec.get("Obsid"))
    dato = _date(rec.get("Dato", ""))
    artnr = _int(rec.get("Artnr"))
    if obsid is None or dato is None or artnr is None:
        return None
    afd = [a.strip() for a in (rec.get("DOF_afdeling") or "").split("|") if a.strip()]
    obserkode = _clean(rec.get("Obserkode"), 16)
    return Row(
        obsid=obsid,
        turid=_int(rec.get("Turid")),
        dato=dato,
        artnr=artnr,
        artnavn=(rec.get("Artnavn") or "").strip().strip("[]"),
        latin=_clean(rec.get("Latin"), 200),
        sortering=_int(rec.get("Sortering")),
        antal=max(_int(rec.get("Antal")) or 0, 0),
        koen=_clean(rec.get("Koen"), 4),
        adfkode=_clean(rec.get("Adfkode"), 8),
        adftekst=_clean(rec.get("Adfbeskrivelse"), 120),
        alderkode=_clean(rec.get("Alderkode"), 8),
        dragtkode=_clean(rec.get("Dragtkode"), 8),
        dragttekst=_clean(rec.get("Dragtbeskrivelse"), 120),
        obserkode=obserkode.upper() if obserkode else None,
        fornavn=_clean(rec.get("Fornavn"), 100),
        efternavn=_clean(rec.get("Efternavn"), 100),
        obser_by=_clean(rec.get("Obser_by"), 100),
        medobser=_clean(rec.get("Medobser")),
        turnoter=_clean(rec.get("Turnoter")),
        fuglnoter=_clean(rec.get("Fuglnoter")),
        metode=_clean(rec.get("Metode"), 60),
        obstidfra=_time(rec.get("Obstidfra")),
        obstidtil=_time(rec.get("Obstidtil")),
        turtidfra=_time(rec.get("Turtidfra")),
        turtidtil=_time(rec.get("Turtidtil")),
        hemmelig=1 if (rec.get("Hemmelig") or "0").strip() not in ("", "0") else 0,
        kvalitet=_int(rec.get("Kvalitet")),
        loknr=_int(rec.get("Loknr")),
        loknavn=_clean(rec.get("Loknavn"), 250),
        afdelinger=afd,
        lok_lat=_float(rec.get("lok_breddegrad")),
        lok_lon=_float(rec.get("lok_laengdegrad")),
        obs_lat=_float(rec.get("obs_breddegrad")),
        obs_lon=_float(rec.get("obs_laengdegrad")),
    )


def iter_records(text: str | io.TextIOBase) -> Iterator[dict[str, str]]:
    stream = io.StringIO(text) if isinstance(text, str) else text
    # DOFbasen har enkelte NUL-tegn i fritekst (fx fuglenoter i gamle observationer); Postgres kan ikke gemme dem i tekst
    reader = csv.reader((line.replace("\x00", "") for line in stream), delimiter=";")
    header = next(reader, None)
    if not header:
        return
    header = [h.strip().lstrip("﻿") for h in header]
    if "Obsid" not in header:
        # DOFbasen svarer med HTML ved fejl/overbelastning
        raise ValueError("Uventet svar fra DOFbasen (ingen Obsid-kolonne)")
    width = len(header)
    for values in reader:
        if len(values) < width:
            continue
        yield dict(zip(header, values))


def parse_rows(records: Iterable[dict[str, str]]) -> list[Row]:
    """Parser og samler dubletter (samme obsid i flere lokalafdelinger)."""
    by_id: dict[int, Row] = {}
    for rec in records:
        row = to_row(rec)
        if row is None:
            continue
        existing = by_id.get(row.obsid)
        if existing is None:
            by_id[row.obsid] = row
            continue
        for afd in row.afdelinger:
            if afd not in existing.afdelinger:
                existing.afdelinger.append(afd)
        existing.antal = max(existing.antal, row.antal)
    return list(by_id.values())


def parse_text(text: str) -> list[Row]:
    return parse_rows(iter_records(text))
