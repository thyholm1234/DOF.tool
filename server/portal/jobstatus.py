"""Status for worker-opgaver (beregninger, import, synkronisering) til admin-panelet.

Seneste kørsel pr. opgave ligger i en Valkey-hash; afsluttede kørsler i en kort historik.
Kode der kører inde i `track()` kan melde fremdrift med `step()` uden at kende opgavens navn.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

from portal.cache import kv
from portal.timeutil import dk_now

JOBS = "worker:jobs"
HISTORY = "worker:job_log"

LABELS = {
    "compute": "Genberegning (fænologi, lokale tal)",
    "archive_import": "Arkivimport",
    "aggregates": "Dagsaggregater",
    "climate": "DMI-klima",
    "events": "DOF-ture",
    "forecast": "DMI-prognose",
    "area_counts": "Områdetal",
    "loc_finds": "Lokale hits: fund pr. lokalitet",
    "seasons": "Normale forekomstperioder (fænologi-filter)",
    "regions": "Arter pr. år (regioner)",
    "gaps": "Huller (observatører og ture pr. art)",
    "location_years": "Lokalitetsoversigt",
    "plan": "Opdateringsplan",
    "netfugl_kryds": "Netfugl: krydslister fra DOFbasen",
    "netfugl_rss": "Netfugl: RSS-kilder",
}

_current: ContextVar[str | None] = ContextVar("job", default=None)
_t0: ContextVar[float] = ContextVar("job_t0", default=0.0)


async def _get(name: str) -> dict:
    raw = await kv().hget(JOBS, name)
    return json.loads(raw) if raw else {}


async def _put(name: str, job: dict) -> None:
    await kv().hset(JOBS, name, json.dumps({"name": name, "label": LABELS.get(name, name), **job}, ensure_ascii=False))


async def queued(name: str) -> None:
    job = await _get(name)
    if job.get("status") != "running":
        await _put(name, {**job, "status": "queued", "queued_at": dk_now().isoformat()})


@asynccontextmanager
async def track(name: str):
    prev = await _get(name)
    t0 = time.time()
    await _put(name, {"status": "running", "started_at": dk_now().isoformat(), "step": "", "last": prev.get("last")})
    token, token_t0 = _current.set(name), _t0.set(t0)
    try:
        yield
    except Exception as exc:
        await _finish(name, "error", t0, str(exc)[:500])
        raise
    else:
        await _finish(name, "done", t0)
    finally:
        _current.reset(token)
        _t0.reset(token_t0)


async def _finish(name: str, status: str, t0: float, error: str | None = None) -> None:
    job = await _get(name)
    end = {"status": status, "started_at": job.get("started_at"), "finished_at": dk_now().isoformat(),
           "duration": round(time.time() - t0, 1), "error": error}
    await _put(name, {**end, "step": job.get("step", ""), "last": end})
    r = kv()
    await r.lpush(HISTORY, json.dumps({"name": name, "label": LABELS.get(name, name), **end}, ensure_ascii=False))
    await r.ltrim(HISTORY, 0, 49)


async def step(text: str, done: int | None = None, total: int | None = None) -> None:
    """Fremdrift for den opgave der kører i denne kontekst (no-op uden for track())."""
    name = _current.get()
    if not name:
        return
    job = await _get(name)
    await _put(name, {**job, "step": text, "done": done, "total": total})


async def interrupted() -> None:
    """Ved worker-opstart: opgaver der stod som kørende/i kø blev afbrudt."""
    for name, raw in (await kv().hgetall(JOBS)).items():
        job = json.loads(raw)
        if job.get("status") in ("running", "queued"):
            await _put(name, {**job, "status": "interrupted", "finished_at": dk_now().isoformat()})


async def snapshot(history: int = 20) -> dict:
    r = kv()
    jobs = {n: json.loads(v) for n, v in (await r.hgetall(JOBS)).items()}
    return {
        "jobs": [jobs.get(n) or {"name": n, "label": label, "status": "idle"} for n, label in LABELS.items()],
        "history": [json.loads(x) for x in await r.lrange(HISTORY, 0, history - 1)],
    }
