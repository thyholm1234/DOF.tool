from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from backend.app.db.models import DailySync, SyncSettings
from backend.app.db.session import SessionLocal
from backend.app.services.dof_sync import fetch_observations_for_date
from backend.app.services.dof_enrichment import enrich_observations
from backend.app.services.observation_store import upsert_rows

logger = logging.getLogger(__name__)


async def sync_day(target_date: date) -> int:
    async with SessionLocal() as session:
        try:
            rows = await enrich_observations(await fetch_observations_for_date(target_date))
            processed = await upsert_rows(rows, session)
            summary = session.info.get("observation_upsert_summary", {})
            sync = await session.get(DailySync, target_date)
            if sync is None:
                sync = DailySync(sync_date=target_date)
                session.add(sync)
            sync.status = "ok"
            sync.processed_count = processed
            sync.new_count = summary.get("new_count", 0)
            sync.updated_count = summary.get("updated_count", 0)
            sync.changed_count = summary.get("changed_count", 0)
            sync.error = None
            sync.synced_at = datetime.now(timezone.utc)
            await session.commit()
            return processed
        except Exception as error:
            await session.rollback()
            sync = await session.get(DailySync, target_date)
            if sync is None:
                sync = DailySync(sync_date=target_date)
                session.add(sync)
            sync.status = "error"
            sync.error = str(error)[:2000]
            sync.synced_at = datetime.now(timezone.utc)
            await session.commit()
            raise


async def run_daily_sync() -> None:
    while True:
        target_date = datetime.now(timezone.utc).date()
        try:
            processed = await sync_day(target_date)
            logger.info("Daily observation sync completed for %s: %s rows", target_date, processed)
        except Exception:
            logger.exception("Daily observation sync failed for %s", target_date)

        async with SessionLocal() as session:
            settings = await session.get(SyncSettings, 1)
            interval_seconds = settings.interval_seconds if settings else 60
        await asyncio.sleep(max(10, interval_seconds))