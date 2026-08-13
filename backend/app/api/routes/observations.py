from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import (
    DofSyncRequest,
    LegacyObservationUpdateRequest,
    ObservationAlert,
    ObservationIngest,
    ObservationThreadDetail,
    ObservationThreadSummary,
    UserPreferencesPayload,
)
from backend.app.db.models import Observation, UserPreference
from backend.app.db.session import get_db
from backend.app.services.dof_sync import fetch_observations_for_date

router = APIRouter(prefix="/observations", tags=["observations"])


def _normalize_categories(raw: str | None) -> set[str]:
    if not raw:
        return {"SU", "SUB", "bemaerk"}
    return {item.strip() for item in raw.split(",") if item.strip()}


def _thread_id(species: str, location_id: str | None, location_name: str) -> str:
    location_part = location_id or location_name.lower().replace(" ", "-")
    return f"{species.lower().replace(' ', '-')}-{location_part}"


def legacy_row_to_ingest(row: dict) -> ObservationIngest | None:
    obsid = str(row.get("Obsid") or row.get("obsid") or "").strip()
    species = str(row.get("Artnavn") or row.get("species") or "").strip()
    location = str(row.get("Loknavn") or row.get("location") or "").strip()
    date_raw = str(row.get("Dato") or row.get("date") or "").strip()
    if not obsid or not species or not location or not date_raw:
        return None

    parsed_date: datetime | None = None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(date_raw, fmt)
            break
        except ValueError:
            continue
    if parsed_date is None:
        return None

    observed_at = parsed_date.replace(tzinfo=timezone.utc)
    category = str(row.get("kategori") or row.get("category") or "alm")
    antal = row.get("Antal") or row.get("count")
    count: int | None
    try:
        count = int(str(antal).strip()) if antal is not None and str(antal).strip() else None
    except ValueError:
        count = None

    adfkode = str(row.get("Adfkode") or "").strip().upper()
    return ObservationIngest(
        obsid=obsid,
        observed_at=observed_at,
        species=species,
        location=location,
        category=category,
        count=count,
        observer_code=str(row.get("Obserkode") or row.get("observer_code") or "").strip() or None,
        species_code=str(row.get("Artnr") or row.get("species_code") or "").strip() or None,
        species_latin=str(row.get("Latin") or row.get("species_latin") or "").strip() or None,
        location_id=str(row.get("Loknr") or row.get("location_id") or "").strip() or None,
        dof_afdeling=str(row.get("DOF_afdeling") or row.get("dof_afdeling") or "").strip() or None,
        note=str(row.get("Fuglnoter") or row.get("note") or "").strip() or None,
        is_migration=adfkode == "T",
        is_matrikel=bool(str(row.get("Turid") or "").strip()),
    )


async def upsert_rows(rows: list[ObservationIngest], db: AsyncSession) -> int:
    processed = 0
    for row in rows:
        payload = row.model_dump(mode="json")
        stmt = pg_insert(Observation).values(
            obsid=row.obsid,
            observed_on=row.observed_at.date(),
            observed_at=row.observed_at,
            species=row.species,
            species_latin=row.species_latin,
            species_code=row.species_code,
            category=row.category,
            observer_code=row.observer_code,
            location_name=row.location,
            location_id=row.location_id,
            dof_afdeling=row.dof_afdeling,
            count=row.count,
            note=row.note,
            is_migration=row.is_migration,
            is_matrikel=row.is_matrikel,
            source_payload=json.dumps(payload, ensure_ascii=False),
        ).on_conflict_do_update(
            index_elements=["obsid"],
            set_={
                "observed_on": row.observed_at.date(),
                "observed_at": row.observed_at,
                "species": row.species,
                "species_latin": row.species_latin,
                "species_code": row.species_code,
                "category": row.category,
                "observer_code": row.observer_code,
                "location_name": row.location,
                "location_id": row.location_id,
                "dof_afdeling": row.dof_afdeling,
                "count": row.count,
                "note": row.note,
                "is_migration": row.is_migration,
                "is_matrikel": row.is_matrikel,
                "source_payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        await db.execute(stmt)
        processed += 1
    await db.commit()
    return processed


@router.post("/ingest", response_model=dict)
async def ingest_observations(
    rows: list[ObservationIngest],
    db: AsyncSession = Depends(get_db),
) -> dict:
    processed = await upsert_rows(rows, db)
    return {"status": "ok", "processed": processed}


@router.post("/legacy-update", response_model=dict)
async def ingest_legacy_observations(
    payload: LegacyObservationUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    normalized = [item for item in (legacy_row_to_ingest(row) for row in payload.rows) if item]
    if not normalized:
        raise HTTPException(status_code=400, detail="Ingen gyldige observationsrækker modtaget.")
    processed = await upsert_rows(normalized, db)
    return {"status": "ok", "processed": processed}


@router.post("/sync/dof", response_model=dict)
async def sync_from_dof(
    payload: DofSyncRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.target_date:
        parsed = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(payload.target_date, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise HTTPException(status_code=400, detail="Ugyldigt datoformat for target_date.")
        target = parsed
    else:
        target = datetime.now(tz=timezone.utc).date()

    rows = await fetch_observations_for_date(target)
    processed = await upsert_rows(rows, db)
    return {"status": "ok", "target_date": target.isoformat(), "processed": processed}


@router.post("/preferences", response_model=dict)
async def set_observation_preferences(
    payload: UserPreferencesPayload,
    db: AsyncSession = Depends(get_db),
) -> dict:
    pref = await db.get(UserPreference, payload.user_id)
    if pref is None:
        pref = UserPreference(user_id=payload.user_id)
        db.add(pref)

    pref.observer_code = payload.observer_code
    pref.display_name = payload.display_name
    pref.afdelinger = json.dumps(payload.afdelinger, ensure_ascii=False)
    pref.categories = ",".join(payload.categories)
    pref.exclude_species = json.dumps(payload.exclude_species, ensure_ascii=False)
    pref.min_count_default = payload.min_count_default
    pref.quiet_hours = payload.quiet_hours

    await db.commit()
    return {"status": "ok", "user_id": payload.user_id}


@router.get("/alerts", response_model=list[ObservationAlert])
async def get_rare_observation_alerts(
    user_id: str = Query(..., min_length=2),
    days: int = Query(default=2, ge=1, le=14),
    db: AsyncSession = Depends(get_db),
) -> list[ObservationAlert]:
    pref = await db.get(UserPreference, user_id)
    categories = _normalize_categories(pref.categories if pref else None)
    min_count = pref.min_count_default if pref else 1
    exclude_species = set()
    if pref and pref.exclude_species:
        exclude_species = set(json.loads(pref.exclude_species))

    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Observation)
        .where(Observation.observed_at >= since)
        .order_by(Observation.observed_at.desc())
    )
    rows = result.scalars().all()
    alerts: list[ObservationAlert] = []
    for row in rows:
        if row.category not in categories:
            continue
        if row.species in exclude_species:
            continue
        if (row.count or 0) < min_count:
            continue
        alerts.append(
            ObservationAlert(
                obsid=row.obsid,
                species=row.species,
                category=row.category,
                location=row.location_name,
                count=row.count,
                observed_at=row.observed_at,
            )
        )
    return alerts


@router.get("/threads", response_model=list[ObservationThreadSummary])
async def get_threads_for_day(
    day: date = Query(default_factory=date.today),
    db: AsyncSession = Depends(get_db),
) -> list[ObservationThreadSummary]:
    result = await db.execute(
        select(Observation)
        .where(Observation.observed_on == day)
        .order_by(Observation.observed_at.desc())
    )
    rows = result.scalars().all()
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        grouped[_thread_id(row.species, row.location_id, row.location_name)].append(row)

    summaries: list[ObservationThreadSummary] = []
    for thread_id, items in grouped.items():
        latest = max(items, key=lambda item: item.observed_at)
        summaries.append(
            ObservationThreadSummary(
                thread_id=thread_id,
                day=day.isoformat(),
                species=latest.species,
                location=latest.location_name,
                category=latest.category,
                latest_observed_at=latest.observed_at,
                observations=len(items),
            )
        )
    return sorted(summaries, key=lambda item: item.latest_observed_at, reverse=True)


@router.get("/thread/{day}/{thread_id}", response_model=ObservationThreadDetail)
async def get_thread_detail(
    day: date,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
) -> ObservationThreadDetail:
    result = await db.execute(
        select(Observation)
        .where(Observation.observed_on == day)
        .order_by(Observation.observed_at.asc())
    )
    rows = result.scalars().all()
    items = []
    for row in rows:
        if _thread_id(row.species, row.location_id, row.location_name) == thread_id:
            items.append(
                ObservationAlert(
                    obsid=row.obsid,
                    species=row.species,
                    category=row.category,
                    location=row.location_name,
                    count=row.count,
                    observed_at=row.observed_at,
                )
            )
    if not items:
        raise HTTPException(status_code=404, detail="Thread ikke fundet.")
    return ObservationThreadDetail(thread_id=thread_id, day=day.isoformat(), items=items)


@router.get("/summary")
async def observation_summary(
    db: AsyncSession = Depends(get_db),
) -> dict:
    total_obs = await db.scalar(select(func.count(Observation.id)))
    unique_species = await db.scalar(select(func.count(func.distinct(Observation.species))))
    unique_observers = await db.scalar(
        select(func.count(func.distinct(Observation.observer_code))).where(
            Observation.observer_code.is_not(None)
        )
    )
    return {
        "observations": total_obs or 0,
        "species": unique_species or 0,
        "observers": unique_observers or 0,
    }
