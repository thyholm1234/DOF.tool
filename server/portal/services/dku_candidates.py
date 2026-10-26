"""DKU-kandidater i baggrunden: det løbende filter og analyser i workeren (søgningen selv er dku.candidates()).

Løbende: der gemmes ét filter (Setting «dku_candidate_filter»), som DKU's administratorer og landsdækkende ansvarlige kan
rette. Workeren kigger hvert minut efter observationer, der er nye eller ændret siden sidst (observations.updated_at
sættes ved indlæsningen, når en række er ny eller ændret) og ligger inden for filtrets dage, vurderer dem og gemmer dem,
der rammer, i dku_candidates. Ændres filtret, gennemgås hele perioden forfra. Hver afdelings DKU-ansvarlige ser kun
kandidater fra deres egne afdelinger; sager og godkendelser slås op, når listen vises.

Analyser: en DKU-ansvarlig kan sende en søgning til workeren i stedet for at vente på den i browseren (fx mange dage eller
lang historik). Status og resultat ligger i Valkey i en uge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import get_json, kv, set_json, worker_command
from portal.db import SessionLocal
from portal.models import Setting
from portal.services import dku, features
from portal.timeutil import dk_now, dk_today

log = logging.getLogger("portal.dku")

FILTER_KEY = "dku_candidate_filter"
MARK = "dku:cand:mark"  # {"at": seneste kørsel, "f": filtret den kørte med}
LAST = "dku:cand:last"  # seneste kørsel til visning
LOCK = "dku:cand:lock"
OVERLAP = timedelta(minutes=10)  # rækker indlæst lige før en kørsel, men gemt (committed) efter, tages med næste gang
INTERVAL = 60
STANDING_LIMIT = 20000
ANALYSIS_LIMIT = 1000
ANALYSIS_TTL = 7 * 86400

DEFAULT_FILTER = {"on": False, "kriterier": list(dku.CRITERIA), "days": 7, "min": 1, "niveau": "lok", "hist_years": 10,
                  "antal_min": 5, "faktor": 2.0, "ny_max": 0, "saeson_dage": 14, "kun_bemaerk": False}


async def run(db: AsyncSession, f: dict, *, afd: set[int] | None, obsids: list[int] | None = None,
              limit: int = 300) -> list[dict]:
    """dku.candidates() med et filter, som det gemmes og sendes fra Kandidater-fanen."""
    crit = [k for k in f["kriterier"] if k in dku.CRITERIA]
    return await dku.candidates(db, afd=afd, days=f["days"], criteria=crit, min_match=min(f["min"], len(crit)),
                                level=f["niveau"], hist_years=f["hist_years"], antal_min=f["antal_min"],
                                antal_factor=f["faktor"], ny_max=f["ny_max"], season_days=f["saeson_dage"],
                                only_notable=f["kun_bemaerk"], obsids=obsids, limit=limit)


async def recheck_dok(rows: list[dict], min_match: int) -> list[dict]:
    """«Mangler dokumentation» kan være forældet: fotos og lyd hentes af medie-scanneren efter observationen. Fjern
    kriteriet, hvor der nu er medier, og rækken, hvis den så rammer for få kriterier."""
    from portal.dof.jobs import media_many

    media = await media_many([r["obsid"] for r in rows if "dok" in r["matched"]])
    out = []
    for r in rows:
        m = media.get(r["obsid"]) or {}
        if "dok" in r["matched"] and (m.get("images") or m.get("sounds")):
            r = {**r, "matched": [k for k in r["matched"] if k != "dok"]}
        if len(r["matched"]) >= min_match:
            out.append(r)
    return out


# ---------------------------------------------------------------- det løbende filter

async def load_filter(db: AsyncSession) -> dict:
    row = await db.get(Setting, FILTER_KEY)
    return {**DEFAULT_FILTER, **(row.value if row is not None and isinstance(row.value, dict) else {})}


async def save_filter(db: AsyncSession, f: dict) -> dict:
    f = {**DEFAULT_FILTER, **f}
    row = await db.get(Setting, FILTER_KEY)
    if row is None:
        db.add(Setting(key=FILTER_KEY, value=f))
    else:
        row.value = f
    if not f["on"]:
        await db.execute(text("DELETE FROM dku_candidates"))
    await db.commit()
    if f["on"]:
        await worker_command("dku_candidates")  # hele perioden med det nye filter
    else:
        await kv().delete(MARK, LAST)
    return f


async def status() -> dict | None:
    return await get_json(LAST)


async def standing(db: AsyncSession, f: dict, afd: set[int] | None, include_reviewed: bool) -> list[dict]:
    """De gemte kandidater for afdelingerne (None = alle), sorteret som dku.candidates()."""
    rows = (await db.execute(text("""SELECT data, found_at FROM dku_candidates
        WHERE dato >= :since AND (CAST(:all AS boolean) OR afdelinger && CAST(:afd AS smallint[]))"""),
        {"since": dk_today() - timedelta(days=f["days"]), "all": afd is None, "afd": sorted(afd or [])})).all()
    out = [{**data, "found_at": found.isoformat()} for data, found in rows]
    crit = [k for k in f["kriterier"] if k in dku.CRITERIA]
    out = await recheck_dok(out, min(f["min"], len(crit)))
    out.sort(key=lambda x: (len(x["matched"]), x["dato"], x["obsid"]), reverse=True)
    return await dku.annotate(db, out[:1000], include_reviewed)


async def scan(full: bool = False, wait: bool = False) -> dict | None:
    """Vurdér nye og ændrede observationer (eller hele perioden) mod det løbende filter. None = intet at gøre."""
    async with SessionLocal() as s:
        if (await features.load(s)).get("dku", "off") == "off":
            return None
        f = await load_filter(s)
    if not f["on"]:
        return None
    r = kv()
    while not await r.set(LOCK, "1", nx=True, ex=1800):
        if not wait:
            return None
        await asyncio.sleep(2)
    try:
        started = datetime.now(timezone.utc)
        sig = json.dumps(f, sort_keys=True)
        mark = await get_json(MARK)
        full = full or not mark or mark.get("f") != sig
        since = dk_today() - timedelta(days=f["days"])
        async with SessionLocal() as s:
            if full:
                await jobstatus.step(f"Hele perioden ({f['days']} dage)")
                checked = None
                rows = await run(s, f, afd=None, limit=STANDING_LIMIT)
            else:
                checked = list((await s.execute(text("""SELECT obsid FROM observations
                    WHERE dato >= :since AND updated_at > :t"""),
                    {"since": since, "t": datetime.fromisoformat(mark["at"]) - OVERLAP})).scalars())
                rows = await run(s, f, afd=None, obsids=checked, limit=len(checked)) if checked else []
            hits = {x["obsid"] for x in rows}
            stored = list((await s.execute(text("SELECT obsid FROM dku_candidates"))).scalars())
            # Gemte kandidater, der er slettet, blevet hemmelige eller faldet ud af perioden – og ved en fuld kørsel
            # dem, filtret ikke længere rammer
            alive = set((await s.execute(text("""SELECT obsid FROM observations
                WHERE obsid = ANY(:o) AND hemmelig = 0 AND dato >= :since"""), {"o": stored, "since": since})).scalars()) \
                if stored else set()
            gone = {o for o in stored if o not in alive or (full and o not in hits)} | (set(checked or []) - hits)
            if gone:
                await s.execute(text("DELETE FROM dku_candidates WHERE obsid = ANY(:o)"), {"o": sorted(gone)})
            if rows:
                await s.execute(text("""INSERT INTO dku_candidates (obsid, dato, afdelinger, matched, data)
                    VALUES (:obsid, :dato, CAST(:afd AS smallint[]), CAST(:matched AS varchar[]), CAST(:data AS jsonb))
                    ON CONFLICT (obsid) DO UPDATE SET dato = EXCLUDED.dato, afdelinger = EXCLUDED.afdelinger,
                        matched = EXCLUDED.matched, data = EXCLUDED.data, updated_at = now()"""),
                    [{"obsid": x["obsid"], "dato": datetime.fromisoformat(x["dato"]).date(), "afd": x["afdelinger"],
                      "matched": x["matched"], "data": json.dumps(x, ensure_ascii=False)} for x in rows])
            await s.commit()
            total = (await s.execute(text("SELECT count(*) FROM dku_candidates"))).scalar()
        await set_json(MARK, {"at": started.isoformat(), "f": sig})
        out = {"at": dk_now().isoformat(), "full": full, "checked": len(checked) if checked is not None else None,
               "hits": len(rows), "total": total}
        if full or rows or gone:  # ellers er der intet nyt at vise
            await set_json(LAST, out)
        else:
            await set_json(LAST, {**(await get_json(LAST) or {}), "checked_at": out["at"]})
        await jobstatus.step(f"{len(rows)} kandidater, {total} i alt")
        return out
    finally:
        await r.delete(LOCK)


async def loop() -> None:
    """Workerens løbende kørsel (hvert minut). Kører ikke, når DKU-modulet eller filtret er slået fra."""
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            await scan()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – løkken må aldrig dø
            log.exception("Løbende DKU-kandidater fejlede")


# ---------------------------------------------------------------- analyser i workeren

def _key(aid: str) -> str:
    return f"dku:analysis:{aid}"


async def start_analysis(user_id: int, f: dict, afd: set[int] | None, label: str) -> str:
    aid = secrets.token_hex(6)
    await set_json(_key(aid), {"id": aid, "user_id": user_id, "status": "queued", "filter": f, "label": label,
                               "afd": sorted(afd) if afd is not None else None, "created_at": dk_now().isoformat()},
                   ANALYSIS_TTL)
    mine = f"dku:analyses:{user_id}"
    await kv().lpush(mine, aid)
    await kv().ltrim(mine, 0, 9)
    await kv().expire(mine, ANALYSIS_TTL)
    await worker_command("dku_analysis", id=aid)
    return aid


async def analysis(aid: str) -> dict | None:
    return await get_json(_key(aid))


async def analyses(user_id: int) -> list[dict]:
    """Brugerens seneste analyser, nyeste først (uden rækkerne)."""
    out = []
    for aid in await kv().lrange(f"dku:analyses:{user_id}", 0, 9):
        job = await analysis(aid)
        if job:
            job["n"] = len(job.pop("rows", None) or [])
            out.append(job)
    return out


async def run_analysis(aid: str) -> None:
    """Workeren: kør en analyse sendt fra Kandidater-fanen og gem resultatet."""
    job = await analysis(aid)
    if job is None:
        return
    await jobstatus.step(job.get("label") or aid)
    await set_json(_key(aid), {**job, "status": "running", "started_at": dk_now().isoformat()}, ANALYSIS_TTL)
    try:
        async with SessionLocal() as s:
            rows = await run(s, job["filter"], afd=set(job["afd"]) if job["afd"] is not None else None, limit=ANALYSIS_LIMIT)
    except Exception as exc:
        await set_json(_key(aid), {**job, "status": "error", "error": str(exc)[:300], "finished_at": dk_now().isoformat()},
                       ANALYSIS_TTL)
        raise
    await set_json(_key(aid), {**job, "status": "done", "rows": rows, "finished_at": dk_now().isoformat()}, ANALYSIS_TTL)
    await jobstatus.step(f"{len(rows)} kandidater")
