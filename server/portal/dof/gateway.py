"""Den eneste vej ud til dofbasen.dk.

Alle kald – live-observationer, login, referencedata, historik, medier – går gennem én kø i
Valkey og udføres af én løkke i worker-processen. Mellem afslutningen af ét kald og starten af
det næste går der altid mindst `dof_min_interval` sekunder (standard 60). Grænsen håndhæves med
et globalt tidsstempel + lås i Valkey, så den også holder hvis der ved en fejl kører flere workers.

Planlægning pr. ledig plads:
  1. Interaktive job (login, medier) – en bruger venter.
  2. Live-hentning af dagens observationer, hvis den er forfalden.
  3. Øvrige job efter prioritet. Er der ventende job, skiftes der mellem live og kø, så
     live-feedet opdateres mindst hvert andet minut.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from portal.cache import kv
from portal.config import get_settings

log = logging.getLogger("portal.gateway")

INTERACTIVE, HIGH, NORMAL, BULK = 0, 1, 2, 3
Q = "dof:q"
LAST_CALL = "dof:last_call"
LOCK = "dof:lock"
STATUS = "dof:status"
PRIO_STEP = 1e14  # skal være større end tidsstempler i ms (~1,8e12)

Handler = Callable[[httpx.AsyncClient, dict], Awaitable[Any]]


async def enqueue(kind: str, params: dict | None = None, *, priority: int = NORMAL, dedupe: str | None = None,
                  secret: dict | None = None) -> str:
    """Læg et job i kø. Returnerer job-id (eksisterende id hvis dedupe-nøglen allerede venter)."""
    r = kv()
    job_id = uuid.uuid4().hex[:16]
    if dedupe:
        ok = await r.set(f"dof:dedupe:{dedupe}", job_id, nx=True, ex=7 * 86400)
        if not ok:
            return await r.get(f"dof:dedupe:{dedupe}") or job_id
    job = {"id": job_id, "kind": kind, "params": params or {}, "dedupe": dedupe,
           "priority": priority, "created": time.time()}
    pipe = r.pipeline()
    pipe.set(f"dof:job:{job_id}", json.dumps(job), ex=7 * 86400)
    if secret:
        # Følsomme data (adgangskode ved login) ligger kun kortvarigt og slettes efter brug
        pipe.set(f"dof:secret:{job_id}", json.dumps(secret), ex=180)
    pipe.zadd(Q, {job_id: priority * PRIO_STEP + time.time() * 1000})
    await pipe.execute()
    return job_id


async def wait_result(job_id: str, timeout: float) -> dict | None:
    # Korte BLPOP-runder: klientens socket-timeout er kortere end en hel gateway-cyklus
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = await kv().blpop([f"dof:wait:{job_id}"], timeout=2)
        if res:
            return json.loads(res[1])
    return None


async def queue_snapshot(limit: int = 50) -> list[dict]:
    r = kv()
    ids = await r.zrange(Q, 0, limit - 1)
    jobs = []
    for job_id in ids:
        raw = await r.get(f"dof:job:{job_id}")
        if raw:
            job = json.loads(raw)
            job.pop("secret", None)
            if job["kind"] == "login":
                job["params"] = {"obserkode": job["params"].get("obserkode")}
            jobs.append(job)
    return jobs


async def status() -> dict:
    r = kv()
    data = await r.hgetall(STATUS)
    data["queue_length"] = await r.zcard(Q)
    data["min_interval"] = get_settings().dof_min_interval
    last = await r.get(LAST_CALL)
    data["seconds_since_last_call"] = round(time.time() - float(last), 1) if last else None
    return data


async def cancel(job_id: str) -> None:
    r = kv()
    raw = await r.get(f"dof:job:{job_id}")
    await r.zrem(Q, job_id)
    if raw:
        job = json.loads(raw)
        if job.get("dedupe"):
            await r.delete(f"dof:dedupe:{job['dedupe']}")
    await r.delete(f"dof:job:{job_id}", f"dof:secret:{job_id}")


class Gateway:
    def __init__(self, handlers: dict[str, Handler], live_handler: Handler, live_enabled: Callable[[], Awaitable[bool]]):
        self.settings = get_settings()
        self.handlers = handlers
        self.live_handler = live_handler
        self.live_enabled = live_enabled
        self.last_was_live = False
        self.last_live = 0.0

    async def _pop(self, max_priority: int | None = None) -> dict | None:
        r = kv()
        while True:
            items = await r.zrange(Q, 0, 0, withscores=True)
            if not items:
                return None
            job_id, score = items[0]
            if max_priority is not None and score >= (max_priority + 1) * PRIO_STEP:
                return None
            if not await r.zrem(Q, job_id):
                continue
            raw = await r.get(f"dof:job:{job_id}")
            if not raw:
                continue
            job = json.loads(raw)
            secret = await r.get(f"dof:secret:{job_id}")
            if secret:
                job["secret"] = json.loads(secret)
                await r.delete(f"dof:secret:{job_id}")
            return job

    async def _choose(self) -> tuple[str, dict | None]:
        job = await self._pop(max_priority=INTERACTIVE)
        if job:
            return "job", job
        live_on = await self.live_enabled()
        # Planens interval for "dagen i dag" (0 = slået fra); aldrig under minimumsintervallet
        planned = await kv().get("dof:live_interval")
        if planned is not None and int(float(planned)) == 0:
            live_on = False
        interval = max(self.settings.dof_min_interval, int(float(planned or 0)))
        live_due = live_on and time.time() - self.last_live >= interval - 1
        pending = await kv().zcard(Q) > 0
        if live_due and (not pending or not self.last_was_live or time.time() - self.last_live > 150):
            return "live", None
        job = await self._pop()
        if job:
            return "job", job
        if live_due:
            return "live", None
        return "idle", None

    async def _wait_for_slot(self) -> None:
        r = kv()
        while True:
            last = float(await r.get(LAST_CALL) or 0)
            remaining = last + self.settings.dof_min_interval - time.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def run(self) -> None:
        r = kv()
        # Genberegn køens scores ud fra jobbenes gemte prioritet (robust over for ændret score-skema)
        for job_id in await r.zrange(Q, 0, -1):
            raw = await r.get(f"dof:job:{job_id}")
            if raw:
                job = json.loads(raw)
                await r.zadd(Q, {job_id: job["priority"] * PRIO_STEP + job["created"] * 1000})
            else:
                await r.zrem(Q, job_id)
        headers = {"User-Agent": self.settings.dof_user_agent}
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20), headers=headers, follow_redirects=True) as client:
            while True:
                try:
                    if not self.settings.dof_enabled:
                        await asyncio.sleep(10)
                        continue
                    await self._wait_for_slot()
                    kind, job = await self._choose()
                    if kind == "idle":
                        await asyncio.sleep(1)
                        continue
                    # Lås + tidsstempel før kaldet, så ingen anden proces kan nå at kalde imens
                    if not await r.set(LOCK, "1", nx=True, ex=600):
                        if job:
                            await self._requeue(job)
                        await asyncio.sleep(2)
                        continue
                    try:
                        await r.set(LAST_CALL, time.time())
                        await self._execute(client, kind, job)
                    finally:
                        # Intervallet regnes fra kaldet (inkl. efterbehandling) er helt færdigt
                        await r.set(LAST_CALL, time.time())
                        await r.delete(LOCK)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 – løkken må aldrig dø
                    log.exception("Gateway-fejl")
                    await asyncio.sleep(5)

    async def _requeue(self, job: dict) -> None:
        r = kv()
        secret = job.pop("secret", None)
        if secret:
            await r.set(f"dof:secret:{job['id']}", json.dumps(secret), ex=180)
        await r.zadd(Q, {job["id"]: job["priority"] * PRIO_STEP + job["created"] * 1000})

    async def _execute(self, client: httpx.AsyncClient, kind: str, job: dict | None) -> None:
        r = kv()
        started = time.time()
        label = "live" if kind == "live" else job["kind"]
        result: Any = None
        error: str | None = None
        try:
            if kind == "live":
                self.last_live = started
                self.last_was_live = True
                result = await self.live_handler(client, {})
            else:
                self.last_was_live = False
                handler = self.handlers.get(job["kind"])
                if handler is None:
                    raise ValueError(f"Ukendt jobtype {job['kind']}")
                result = await handler(client, {**job["params"], **({"secret": job["secret"]} if "secret" in job else {})})
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            log.warning("DOFbasen-job %s fejlede: %s", label, error)
        duration = round(time.time() - started, 2)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pipe = r.pipeline()
        pipe.hset(STATUS, mapping={
            "last_kind": label,
            "last_at": datetime.now(timezone.utc).isoformat(),
            "last_duration": duration,
            "last_error": error or "",
            "last_ok": "0" if error else "1",
        })
        pipe.hincrby(STATUS, f"calls:{today}", 1)
        pipe.lpush("dof:log", json.dumps({"kind": label, "params": _safe_params(job), "at": started,
                                          "duration": duration, "error": error, "result": _summary(result)}))
        pipe.ltrim("dof:log", 0, 199)
        if job:
            pipe.delete(f"dof:job:{job['id']}")
            if job.get("dedupe"):
                pipe.delete(f"dof:dedupe:{job['dedupe']}")
            pipe.rpush(f"dof:wait:{job['id']}", json.dumps({"ok": error is None, "error": error, "result": result}, default=str))
            pipe.expire(f"dof:wait:{job['id']}", 300)
        await pipe.execute()
        log.info("DOFbasen %s (%ss)%s", label, duration, f" FEJL {error}" if error else "")


def _safe_params(job: dict | None) -> dict:
    if not job:
        return {}
    params = dict(job.get("params") or {})
    params.pop("password", None)
    return params


def _summary(result: Any) -> Any:
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool)) and k != "token"}
    return None
