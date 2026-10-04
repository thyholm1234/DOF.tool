from __future__ import annotations

import json

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import ObservationIngest
from backend.app.db.models import Observation


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
            enriched_class=row.enriched_class,
            remarkable_count=row.remarkable_count,
            observer_code=row.observer_code,
            location_name=row.location,
            location_id=row.location_id,
            latitude=row.latitude,
            longitude=row.longitude,
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
                "source_payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        await db.execute(stmt)
        processed += 1
    await db.commit()
    return processed