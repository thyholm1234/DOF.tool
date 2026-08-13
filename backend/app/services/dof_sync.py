from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timezone

import httpx

from backend.app.api.schemas import ObservationIngest
from backend.app.core.config import get_settings


def _parse_date(value: str) -> date | None:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(value: str) -> time | None:
    for fmt in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    return None


def _parse_count(raw: str) -> int | None:
    try:
        value = int((raw or "").strip())
        return value if value >= 0 else None
    except ValueError:
        return None


def _to_ingest_row(row: dict[str, str]) -> ObservationIngest | None:
    obsid = (row.get("Obsid") or "").strip()
    species = (row.get("Artnavn") or "").strip()
    location = (row.get("Loknavn") or "").strip()
    if not obsid or not species or not location:
        return None

    observed_on = _parse_date((row.get("Dato") or "").strip())
    if observed_on is None:
        return None
    observed_time = _parse_time((row.get("Obstidfra") or row.get("Turtidfra") or "").strip())
    if observed_time is None:
        observed_time = time(12, 0)
    observed_at = datetime.combine(observed_on, observed_time, tzinfo=timezone.utc)

    category = (row.get("kategori") or "alm").strip() or "alm"
    adfkode = (row.get("Adfkode") or "").strip().upper()
    return ObservationIngest(
        obsid=obsid,
        observed_at=observed_at,
        species=species,
        location=location,
        category=category,
        count=_parse_count(row.get("Antal") or ""),
        observer_code=(row.get("Obserkode") or "").strip() or None,
        species_code=(row.get("Artnr") or "").strip() or None,
        species_latin=(row.get("Latin") or "").strip() or None,
        location_id=(row.get("Loknr") or "").strip() or None,
        dof_afdeling=(row.get("DOF_afdeling") or "").strip() or None,
        note=(row.get("Fuglnoter") or "").strip() or None,
        is_migration=adfkode == "T",
        is_matrikel=bool((row.get("Turid") or "").strip()),
    )


async def fetch_observations_for_date(target_date: date) -> list[ObservationIngest]:
    settings = get_settings()
    formatted = target_date.strftime("%d-%m-%Y")
    url = (
        f"{settings.dof_excel_base_url}"
        "?design=excel&soeg=soeg&periode=dato"
        f"&dato={formatted}"
        "&obstype=observationer&species=alle&sortering=dato"
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    content = response.content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    items: list[ObservationIngest] = []
    for row in reader:
        parsed = _to_ingest_row(row)
        if parsed is not None:
            items.append(parsed)
    return items
