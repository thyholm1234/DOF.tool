from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.observations import legacy_row_to_ingest, upsert_rows
from backend.app.api.schemas import DofSyncRequest
from backend.app.db.session import get_db
from backend.app.services.dof_sync import fetch_observations_for_date

router = APIRouter(tags=["legacy-compat"])


@router.post("/api/update")
async def legacy_update(rows: list[dict], db: AsyncSession = Depends(get_db)) -> dict:
    normalized = [item for item in (legacy_row_to_ingest(row) for row in rows) if item]
    if not normalized:
        raise HTTPException(status_code=400, detail="Ingen gyldige observationsrækker modtaget.")
    processed = await upsert_rows(normalized, db)
    return {"status": "ok", "processed": processed}


@router.post("/api/request_sync")
async def legacy_request_sync(
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
