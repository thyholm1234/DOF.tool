"""Worker: DOFbasen-gateway, planlagte opgaver, arkivimport og beregninger.

Kør præcis én instans (docker compose-servicen `worker`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from sqlalchemy import text

from portal.bootstrap import init_db
from portal.cache import COMMANDS, bump, kv
from portal.db import SessionLocal
from portal.dof import archive, gateway, jobs
from portal.services import areas, datasync, dmi, dof_events, phenology, weather_archive
from portal.timeutil import dk_now, dk_today

log = logging.getLogger("portal.worker")


async def _once(name: str, period_key: str) -> bool:
    """True første gang i en periode (fx dato eller dato+time)."""
    return bool(await kv().set(f"sched:{name}:{period_key}", "1", nx=True, ex=3 * 86400))


async def nightly_compute() -> None:
    async with SessionLocal() as s:
        await phenology.refresh_aggregates(s, dk_today() - timedelta(days=14))
    async with SessionLocal() as s:
        await areas.refresh_all_area_counts(s)
    async with SessionLocal() as s:
        await phenology.recompute_metrics(s)
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM notifications WHERE created_at < now() - interval '90 days'"))
        await s.execute(text("DELETE FROM thread_follows WHERE dato < current_date - 7"))
        await s.execute(text("DELETE FROM activity WHERE created_at < now() - interval '180 days'"))
        await s.commit()
    await bump("lists")


async def refresh_climate() -> None:
    rows = await dmi.climate_months(2000)
    if rows:
        async with SessionLocal() as s:
            await phenology.import_climate(s, rows)
        log.info("DMI klimadata: %s måneder", len(rows))


async def scheduler() -> None:
    await asyncio.sleep(20)
    # Efter opstart: udfyld huller og sørg for klimadata
    await jobs.enqueue_gap_fill()
    async with SessionLocal() as s:
        has_climate = (await s.execute(text("SELECT count(*) FROM climate_month"))).scalar()
        has_metrics = (await s.execute(text("SELECT count(*) FROM pheno_metrics"))).scalar()
    if not has_climate:
        await run_safely("klima", refresh_climate())
    if not has_metrics:
        await kv().rpush(COMMANDS, json.dumps({"cmd": "compute"}))
    async with SessionLocal() as s:
        has_events = (await s.execute(text("SELECT count(*) FROM dof_events"))).scalar()
    if not has_events:
        await run_safely("DOF-ture", dof_events.sync())
    async with SessionLocal() as s:
        await datasync.apply_live_interval((await datasync.get_plan(s, "dof"))["tiers"]["today"])
    while True:
        now = dk_now()
        day = now.date().isoformat()
        hour = f"{day}T{now.hour:02d}"
        try:
            await run_plans()
            if now.minute >= 15 and await _once("forecast", hour):
                await run_safely("prognose", dmi.forecast())
            if now.hour == 2 and now.minute >= 30 and await _once("compute", day):
                await run_safely("natlig beregning", nightly_compute())
            if now.hour == 3 and await _once("reference", day):
                await jobs.enqueue_reference_refresh()
            if now.weekday() == 0 and now.hour == 5 and await _once("climate", day):
                await run_safely("klima", refresh_climate())
            if now.hour % 6 == 1 and now.minute >= 20 and await _once("dof_events", hour):
                await run_safely("DOF-ture", dof_events.sync())
            if now.hour % 6 == 0 and await _once("gap", hour):
                await jobs.enqueue_gap_fill()
        except Exception:  # noqa: BLE001
            log.exception("Planlægger-fejl")
        await asyncio.sleep(30)


DOF_PRIORITY = {"days3": gateway.HIGH, "week": gateway.NORMAL, "month": gateway.NORMAL,
                "year": gateway.BULK, "all": gateway.BULK}


async def run_plans(force: dict[str, list[str]] | None = None) -> dict:
    """Opdateringsplaner: læg de vinduer i kø, hvis interval er gået (eller som er tvunget via 'Kør nu')."""
    out: dict[str, dict] = {"dof": {}, "weather": {}}
    async with SessionLocal() as s:
        due_dof = (force or {}).get("dof") if force else await datasync.due_tiers(s, "dof")
        due_weather = (force or {}).get("weather") if force else await datasync.due_tiers(s, "weather")
        start_year = (await datasync.get_plan(s, "weather"))["start_year"]
    for tier in due_dof or []:
        n = 0
        for kind, params in datasync.dof_tier_jobs(tier):
            if kind == "day":
                await jobs.enqueue_day(date.fromisoformat(params["date"]), DOF_PRIORITY[tier])
            else:
                await jobs.enqueue_month(params["year"], params["month"], DOF_PRIORITY[tier])
            n += 1
        await datasync.mark_run("dof", tier)
        out["dof"][tier] = n
        log.info("Plan DOFbasen '%s': %s jobs i kø", tier, n)
    for tier in due_weather or []:
        n = await weather_archive.enqueue(datasync.tier_months(tier, start_year), only_missing=(tier == "all"))
        await datasync.mark_run("weather", tier)
        out["weather"][tier] = n
        log.info("Plan vejr '%s': %s måneder i kø", tier, n)
    return out


async def run_safely(label: str, coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001
        log.exception("Opgave fejlede: %s", label)


async def command_listener() -> None:
    """Kommandoer fra admin-panelet (API-processen) via Valkey-liste."""
    r = kv()
    while True:
        try:
            item = await r.blpop([COMMANDS], timeout=2)
            if not item:
                continue
            cmd = json.loads(item[1])
            name = cmd.get("cmd")
            await r.hset("worker:last_command", mapping={"cmd": name, "at": dk_now().isoformat()})
            if name == "archive_import":
                await run_safely("arkivimport", archive.run_import())
            elif name == "compute":
                await run_safely("beregning", nightly_compute())
            elif name == "climate":
                await run_safely("klima", refresh_climate())
            elif name == "area_counts":
                async with SessionLocal() as s:
                    scope = areas.parse_scope(cmd["scope"])
                    await areas.refresh_area_counts(s, scope)
                    await s.commit()
            elif name == "events":
                await run_safely("DOF-ture", dof_events.sync())
            elif name == "run_plan":
                await run_safely("plan", run_plans({cmd["source"]: [cmd["tier"]]}))
            elif name == "weather_months":
                await weather_archive.enqueue([tuple(x) for x in cmd["months"]], only_missing=bool(cmd.get("only_missing")))
            elif name == "aggregates":
                async with SessionLocal() as s:
                    await phenology.refresh_aggregates(s, dk_today() - timedelta(days=int(cmd.get("days", 30))))
        except Exception:  # noqa: BLE001
            log.exception("Kommando-fejl")
            await asyncio.sleep(2)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    # Kun én worker kører: låse fra en afbrudt proces er forældede
    await kv().delete("archive:lock", "dof:lock")
    gw = gateway.Gateway(jobs.HANDLERS, jobs.live, jobs.live_enabled)
    tasks = [
        asyncio.create_task(gw.run(), name="gateway"),
        asyncio.create_task(scheduler(), name="scheduler"),
        asyncio.create_task(command_listener(), name="commands"),
        asyncio.create_task(weather_archive.loop(), name="weather"),
    ]
    if await archive.pending_files():
        log.info("Nye arkivfiler fundet – starter import i baggrunden")
        await kv().rpush(COMMANDS, json.dumps({"cmd": "archive_import"}))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
