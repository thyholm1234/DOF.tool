from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import ObservationIngest
from backend.app.db.models import Observation, ObservationChange


_TRACKED_FIELDS = (
    "observed_on", "observed_at", "species", "species_latin", "species_code",
    "category", "enriched_class", "remarkable_count", "observer_code",
    "location_name", "location_id", "latitude", "longitude", "dof_afdeling",
    "count", "note", "is_migration", "is_matrikel",
)


async def upsert_rows(rows: list[ObservationIngest], db: AsyncSession) -> int:
    obsids = [row.obsid for row in rows]
    existing_result = await db.execute(select(Observation).where(Observation.obsid.in_(obsids))) if obsids else None
    existing = {item.obsid: item for item in existing_result.scalars().all()} if existing_result else {}
    new_count = 0
    updated_count = 0
    processed = 0
    for row in rows:
        payload = row.model_dump(mode="json")
        values = {
            "observed_on": row.observed_at.date(),
            "observed_at": row.observed_at,
            "species": row.species,
            "species_latin": row.species_latin,
            "species_code": row.species_code,
            "category": row.category,
            "enriched_class": row.enriched_class,
            "remarkable_count": row.remarkable_count,
            "observer_code": row.observer_code,
            "location_name": row.location,
            "location_id": row.location_id,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "dof_afdeling": row.dof_afdeling,
            "count": row.count,
            "note": row.note,
            "is_migration": row.is_migration,
            "is_matrikel": row.is_matrikel,
        }
        previous = existing.get(row.obsid)
        changed_fields = [field for field in _TRACKED_FIELDS if previous is None or getattr(previous, field) != values[field]]
        if previous is None:
            change_type = "new"
            new_count += 1
        elif changed_fields:
            change_type = "updated"
            updated_count += 1
        else:
            change_type = ""
        if change_type:
            db.add(ObservationChange(
                obsid=row.obsid,
                change_type=change_type,
                species=row.species,
                location_name=row.location,
                old_count=previous.count if previous else None,
                new_count=row.count,
                changed_fields=json.dumps(changed_fields),
            ))
        stmt = pg_insert(Observation).values(
            obsid=row.obsid, **values,
            source_payload=json.dumps(payload, ensure_ascii=False),
        ).on_conflict_do_update(
            index_elements=["obsid"],
            set_={**values, "source_payload": json.dumps(payload, ensure_ascii=False)},
        )
        await db.execute(stmt)
        processed += 1
    db.info["observation_upsert_summary"] = {
        "new_count": new_count,
        "updated_count": updated_count,
        "changed_count": new_count + updated_count,
    }
    await db.commit()
    return processed