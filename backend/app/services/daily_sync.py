from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from backend.app.db.models import DailySync
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
            sync = await session.get(DailySync, target_date)
            if sync is None:
                sync = DailySync(sync_date=target_date)
                session.add(sync)
            sync.status = "ok"
            sync.processed_count = processed
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

        tomorrow = target_date + timedelta(days=1)
        next_run = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
        await asyncio.sleep(max(60, (next_run - datetime.now(timezone.utc)).total_seconds()))