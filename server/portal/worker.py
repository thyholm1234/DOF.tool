"""Worker: DOFbasen-gateway, planlagte opgaver, arkivimport og beregninger.

Kør præcis én instans (docker compose-servicen `worker`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from portal import jobstatus
from portal.bootstrap import build_background_indexes, init_db
from portal.cache import COMMANDS, bump, kv
from portal.config import get_settings
from portal.db import SessionLocal
from portal.dbops import sync_reference
from portal.dof import archive, gateway, jobs
from portal.services import (areas, backup, datasync, dku_candidates, dmi, dof_events, features, imagemeta, locfinds, locyear, netfugl,
                             phenology, regions, schedule, seasons, speechmodel, storage, weather_archive)
from portal.timeutil import dk_now, dk_today

log = logging.getLogger("portal.worker")


AGG_MARK = "agg:last"


async def _aggregate_since(s) -> date:
    """Seneste 14 dage + alle måneder hentet fra DOFbasen siden sidste aggregering (første gang: alt)."""
    since = dk_today() - timedelta(days=14)
    last = await kv().get(AGG_MARK)
    if last:
        q = text("SELECT min(make_date(year, month, 1)) FROM month_sync WHERE fetched_at > :t")
        first = (await s.execute(q, {"t": datetime.fromisoformat(last)})).scalar()
    else:
        first = (await s.execute(text("SELECT min(dato) FROM observations"))).scalar()
    return min(since, first) if first else since


async def nightly_compute() -> None:
    async with SessionLocal() as s:
        mark = dk_now().isoformat()
        since = await _aggregate_since(s)
        await jobstatus.step(f"Dagsaggregater fra {since}", 0, 5)
        await phenology.refresh_aggregates(s, since)
    await kv().set(AGG_MARK, mark)
    await jobstatus.step("Lokale tal (DK, afdelinger, kommuner)", 1, 5)
    async with SessionLocal() as s:
        await areas.refresh_all_area_counts(s)
    await jobstatus.step("Fænologi", 2, 5)
    async with SessionLocal() as s:
        await phenology.recompute_metrics(s)
        await seasons.refresh(s)
    await jobstatus.step("Arter pr. år", 3, 5)
    async with SessionLocal() as s:
        await regions.ensure(s, since.year)

    await jobstatus.step("Oprydning", 4, 5)
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM notifications WHERE created_at < now() - interval '90 days'"))
        await s.execute(text("DELETE FROM thread_follows WHERE dato < current_date - 7"))
        await s.execute(text("DELETE FROM activity WHERE created_at < now() - interval '180 days'"))
        # En position, appen ikke har opdateret i 30 dage (fx afinstalleret), gemmes ikke længere
        await s.execute(text("DELETE FROM user_locations WHERE updated_at < now() - interval '30 days'"))
        await s.commit()
    await bump("lists")


async def refresh_climate() -> None:
    rows = await dmi.climate_months(2000)
    if rows:
        async with SessionLocal() as s:
            await phenology.import_climate(s, rows)
        log.info("DMI klimadata: %s måneder", len(rows))


async def _exists(s, sql: str) -> bool:
    return bool((await s.execute(text(f"SELECT EXISTS ({sql})"))).scalar())


async def _once(key: str, fn) -> None:
    """Kør fn én gang for altid. Fejler den, slettes mærket, så den prøves igen ved næste start."""
    if not await kv().set(key, "1", nx=True):
        return
    try:
        await fn()
    except Exception:
        await kv().delete(key)
        log.exception("Engangsopgaven %s fejlede", key)


async def _strip_old_media() -> None:
    n = await asyncio.to_thread(imagemeta.strip_tree, Path(get_settings().media_dir))
    log.info("Metadata fjernet fra %s eksisterende billeder", n)


async def _move_media_to_s3() -> None:
    log.info("Flyttede %s filer fra disken til objektlageret", await storage.migrate_local())


async def startup_tasks() -> None:
    """Efter opstart: udfyld huller og sæt de beregninger i gang, der mangler."""
    await jobs.enqueue_gap_fill()
    async with SessionLocal() as s:
        has_climate = await _exists(s, "SELECT 1 FROM climate_month")
        has_metrics = await _exists(s, "SELECT 1 FROM pheno_metrics")
        has_regions = await _exists(s, "SELECT 1 FROM region_year_species")
        has_reach = await _exists(s, "SELECT 1 FROM region_reach WHERE year = 0")
        has_seasons = await _exists(s, "SELECT 1 FROM species_season")
        has_days = await _exists(s, "SELECT 1 FROM species_day")
        has_events = await _exists(s, "SELECT 1 FROM dof_events")
        has_kryds = await _exists(s, "SELECT 1 FROM nf_auto_lists")
        netfugl_on = (await features.load(s)).get("netfugl") != "off"
        await datasync.apply_live_interval((await datasync.get_plan(s, "dof"))["tiers"]["today"])
    if not has_climate:
        await run_safely("climate", refresh_climate())
    if not has_metrics or not await kv().exists("pheno:overview"):
        await rpush_cmd("compute")
    elif not has_regions:
        await rpush_cmd("regions")
    if not has_reach:
        await rpush_cmd("gaps")
    if has_days and not has_seasons:
        await rpush_cmd("seasons")
    if not has_events:
        await run_safely("events", dof_events.sync())
    if netfugl_on and not has_kryds:  # fx lige efter skiftet fra nf_auto til nf_auto_lists
        await rpush_cmd("netfugl_kryds")
    # Billeder uploadet før metadata blev fjernet ved upload, og filer fra før objektlageret
    await _once("once:media_meta_v1", _strip_old_media)
    if storage.enabled():
        await _once("once:media_s3_v1", _move_media_to_s3)
    async with SessionLocal() as s:
        if not await locfinds.has_data(s):
            # Ellers er alle arter lokale hits, indtil lokalafdelingens dag i måneden kommer
            await locfinds.enqueue(s)


async def scheduler() -> None:
    await asyncio.sleep(20)
    try:
        await startup_tasks()
    except Exception:  # noqa: BLE001 – planlæggeren skal køre alligevel
        log.exception("Opstartsopgaver fejlede")
    while True:
        try:
            await run_plans()
            async with SessionLocal() as s:
                due = await schedule.due(s)
                flags = await features.load(s)
            for name in due:
                await run_scheduled(name, flags)
            await locfinds_schedule()
        except Exception:  # noqa: BLE001
            log.exception("Planlægger-fejl")
        await asyncio.sleep(30)


async def run_scheduled(name: str, flags: dict) -> None:
    """Én planlagt opgave (intervaller under Admin → Drift, services/schedule.py)."""
    if name.startswith("netfugl_") and (flags.get("netfugl") == "off" or flags.get(name) == "off"):
        return
    if name == "forecast":
        await run_safely("forecast", dmi.forecast())
    elif name == "compute":
        await run_safely("compute", nightly_compute())
        await run_safely("gaps", gaps(None))
    elif name == "location_years":
        await run_safely("location_years", location_years(None))
    elif name == "reference":
        await jobs.enqueue_reference_refresh()
    elif name == "reference_sync":
        try:  # kører hver time og skal ikke fylde i jobhistorikken, men må heller ikke stoppe resten af runden
            await sync_reference()
        except Exception:  # noqa: BLE001
            log.exception("Kopiering af referencedata fejlede")
    elif name == "climate":
        await run_safely("climate", refresh_climate())
    elif name == "events":
        await run_safely("events", dof_events.sync())
    elif name == "gap_fill":
        await jobs.enqueue_gap_fill()
    elif name == "netfugl_kryds":
        await run_safely("netfugl_kryds", netfugl_kryds())
    elif name == "netfugl_rss":
        await run_safely("netfugl_rss", netfugl_rss(None))
    elif name == "speech_model":
        await speechmodel.request()
    elif name == "backup":
        async with SessionLocal() as s:
            cfg = await backup.load(s)
        if backup.configured(cfg):  # ellers springes den planlagte kørsel stille over
            await run_safely("backup", backup.to_sftp(cfg))


async def run_backup() -> None:
    async with SessionLocal() as s:
        cfg = await backup.load(s)
    if not backup.configured(cfg):
        raise RuntimeError("SFTP-backup er ikke sat op (Admin → Drift → Backup)")
    await backup.to_sftp(cfg)


async def locfinds_schedule() -> None:
    """Fund pr. lokalitet: de lokalafdelinger hvis dag i måneden er nået, lægges i kø hos kommando-lytteren."""
    async with SessionLocal() as s:
        cfg = (await schedule.load(s))["loc_finds"]
        if not cfg["on"]:
            return
        due = await locfinds.due(s, cfg["at"])
        if due:
            await locfinds.enqueue(s, due)


async def netfugl_kryds() -> None:
    async with SessionLocal() as s:
        n = await netfugl.refresh_auto(s)
    await jobstatus.step(f"{n} automatiske kryds")


async def netfugl_rss(feed: int | None) -> None:
    async with SessionLocal() as s:
        r = await netfugl.fetch_feeds(s, feed)
    await jobstatus.step(f"{r['ok']} af {r['feeds']} kilder hentet")


DOF_PRIORITY = {"days3": gateway.HIGH, "week": gateway.NORMAL, "month": gateway.NORMAL,
                "year": gateway.BULK, "all": gateway.BULK}


async def run_plans(force: dict[str, list[str]] | None = None) -> dict:
    """Opdateringsplaner: læg de vinduer i kø, hvis interval er gået (eller som er tvunget via 'Kør nu')."""
    out: dict[str, dict] = {"dof": {}, "weather": {}}
    async with SessionLocal() as s:
        due_dof = force.get("dof") if force else await datasync.due_tiers(s, "dof")
        due_weather = force.get("weather") if force else await datasync.due_tiers(s, "weather")
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


async def run_safely(name: str, coro) -> None:
    """Kør en opgave med status i admin-panelet (jobstatus); fejl logges men stopper ikke workeren."""
    try:
        async with jobstatus.track(name):
            await coro
    except Exception:  # noqa: BLE001
        log.exception("Opgave fejlede: %s", jobstatus.LABELS.get(name, name))


async def rpush_cmd(name: str) -> None:
    await kv().rpush(COMMANDS, json.dumps({"cmd": name}))
    await jobstatus.queued(name)


async def compute_seasons() -> None:
    async with SessionLocal() as s:
        await seasons.refresh(s)


async def area_counts(scope_ref: str) -> None:
    await jobstatus.step(scope_ref)
    async with SessionLocal() as s:
        await areas.refresh_area_counts(s, areas.parse_scope(scope_ref))
        await s.commit()


async def loc_finds(afdelinger: list[int] | None) -> None:
    """Estimerede fund pr. lokalitet for de givne lokalafdelinger (None = alle), derefter områdetallene."""
    async with SessionLocal() as s:
        ids = afdelinger if afdelinger is not None else [b["id"] for b in await locfinds.batches(s)]
        names = {b["id"]: b["navn"] for b in await locfinds.batches(s)}
        for i, afd in enumerate(ids):
            await jobstatus.step(names.get(afd, str(afd)), i, len(ids))
            await locfinds.run(s, afd)
        await jobstatus.step("Områdetal", len(ids), len(ids))
        await areas.refresh_region_finds(s)
        await s.commit()


async def region_years(from_year: int | None) -> None:
    async with SessionLocal() as s:
        await regions.refresh(s, from_year)


async def gaps(from_year: int | None) -> None:
    async with SessionLocal() as s:
        if from_year is None and await s.scalar(text("SELECT EXISTS (SELECT 1 FROM region_reach WHERE year = 0)")):
            await regions.ensure_reach(s, dk_today().year - 1)
        else:
            await regions.refresh_reach(s, from_year)


async def location_years(years: list[int] | None) -> None:
    """Lokalitetsoversigten: bestemte år (på forlangende) eller den sædvanlige bygning (alt første gang)."""
    async with SessionLocal() as s:
        await (locyear.refresh(s, years) if years else locyear.ensure(s))
        await locyear.prebuild_species(s)


async def aggregates(days: int) -> None:
    await jobstatus.step(f"Seneste {days} dage")
    async with SessionLocal() as s:
        await phenology.refresh_aggregates(s, dk_today() - timedelta(days=days))


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
                await run_safely("archive_import", archive.run_import())
            elif name == "compute":
                await run_safely("compute", nightly_compute())
            elif name == "climate":
                await run_safely("climate", refresh_climate())
            elif name == "seasons":
                await run_safely("seasons", compute_seasons())
            elif name == "area_counts":
                await run_safely("area_counts", area_counts(cmd["scope"]))
            elif name == "events":
                await run_safely("events", dof_events.sync())
            elif name == "forecast":
                await run_safely("forecast", dmi.forecast())
            elif name == "loc_finds":
                await run_safely("loc_finds", loc_finds(cmd.get("afdelinger")))
            elif name == "regions":
                await run_safely("regions", region_years(cmd.get("from_year")))
            elif name == "gaps":
                await run_safely("gaps", gaps(cmd.get("from_year")))
            elif name == "location_years":
                await run_safely("location_years", location_years(cmd.get("years")))
            elif name == "run_plan":
                await run_safely("plan", run_plans({cmd["source"]: [cmd["tier"]]}))
            elif name == "weather_months":
                await weather_archive.enqueue([tuple(x) for x in cmd["months"]], only_missing=bool(cmd.get("only_missing")))
            elif name == "netfugl_kryds":
                await run_safely("netfugl_kryds", netfugl_kryds())
            elif name == "netfugl_rss":
                await run_safely("netfugl_rss", netfugl_rss(cmd.get("feed")))
            elif name == "backup":
                await run_safely("backup", run_backup())
            elif name == "aggregates":
                await run_safely("aggregates", aggregates(int(cmd.get("days", 30))))
            elif name == "dku_candidates":
                await run_safely("dku_candidates", dku_candidates.scan(full=True, wait=True))
            elif name == "dku_analysis":
                await run_safely("dku_analysis", dku_candidates.run_analysis(cmd["id"]))
        except Exception:  # noqa: BLE001
            log.exception("Kommando-fejl")
            await asyncio.sleep(2)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    # Kun én worker kører: låse fra en afbrudt proces er forældede
    await kv().delete("archive:lock", "dof:lock")
    await jobstatus.interrupted()
    gw = gateway.Gateway(jobs.HANDLERS, jobs.live, jobs.live_enabled)
    tasks = [
        asyncio.create_task(gw.run(), name="gateway"),
        asyncio.create_task(scheduler(), name="scheduler"),
        asyncio.create_task(command_listener(), name="commands"),
        asyncio.create_task(weather_archive.loop(), name="weather"),
        asyncio.create_task(jobs.media_scanner(), name="media"),
        asyncio.create_task(build_background_indexes(), name="indexes"),
        asyncio.create_task(dku_candidates.loop(), name="dku_candidates"),
    ]
    if await archive.pending_files():
        log.info("Nye arkivfiler fundet – starter import i baggrunden")
        await rpush_cmd("archive_import")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
