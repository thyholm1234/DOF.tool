"""Den eneste vej ud til dofbasen.dk.

Alle kald til DOFbasen går gennem gatewayen i worker-processen. Der er to baner:

  * Udtræk (live-observationer, login, referencedata, historik, måneder): én kø og én løkke. Mellem afslutningen af
    ét kald og starten af det næste går der altid mindst `dof_min_interval` sekunder (standard 60). Grænsen
    håndhæves med et globalt tidsstempel + lås i Valkey, så den også holder hvis der ved en fejl kører flere workers.
    Undtagelse: en administrator kan bede om én live-hentning af dagen med det samme (request_live_now) – den
    springer ventetiden over én gang og kan højst bestilles hvert LIVE_NOW_COOLDOWN sekund.
  * Opslag (LOOKUP_KINDS: billeder, lyd og DKU-status for én observation): små sider der ikke trækker data ud af
    DOFbasens database. De har deres egen kø og udføres med det samme, uden minimumsinterval, og de tæller ikke
    med i udtrækkenes interval. Kun et teknisk loft på samtidige opslag (LOOKUP_CONCURRENCY) og på køens længde.

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
RUNNING = "dof:running"  # id på jobbet der udføres lige nu (genoptages hvis workeren dør undervejs)
MAX_ATTEMPTS = 3
PRIO_STEP = 1e14  # skal være større end tidsstempler i ms (~1,8e12)
LIVE_NOW = "dof:live_now"  # sat af en administrator: hent dagen nu, uden at vente på minimumsintervallet
LIVE_NOW_COOLDOWN = 15
LIVE_MAX_GAP = 150  # sekunder: så længe må interaktive job højst holde live-hentningen tilbage
# Opslagsbanen: små opslag pr. observation uden minimumsinterval
LQ = "dof:lq"
LOOKUP_RUNNING = "dof:lq:running"  # id'er på opslag der udføres lige nu
LOOKUP_KINDS = {"media"}
LOOKUP_CONCURRENCY = 4  # samtidige opslag (teknisk loft, ikke en hastighedsgrænse)
MAX_LOOKUP_QUEUE = 200  # værn mod at køen vokser uendeligt

Handler = Callable[[httpx.AsyncClient, dict], Awaitable[Any]]


async def request_live_now() -> bool:
    """Bed om én live-hentning af dagen med det samme (springer minimumsintervallet over). False ved for hyppige kald."""
    if not await kv().set(f"{LIVE_NOW}:cooldown", "1", nx=True, ex=LIVE_NOW_COOLDOWN):
        return False
    await kv().set(LIVE_NOW, "1", ex=300)
    return True


async def enqueue(kind: str, params: dict | None = None, *, priority: int = NORMAL, dedupe: str | None = None,
                  secret: dict | None = None) -> str:
    """Læg et job i kø. Returnerer job-id (eksisterende id hvis dedupe-nøglen allerede venter)."""
    r = kv()
    job_id = uuid.uuid4().hex[:16]
    if dedupe:
        ok = await r.set(f"dof:dedupe:{dedupe}", job_id, nx=True, ex=7 * 86400)
        if not ok:
            existing = await r.get(f"dof:dedupe:{dedupe}")
            if existing and existing in await pending_ids():
                return existing
            # Forældet nøgle: jobbet er tabt (fx afbrudt worker) – læg det i kø igen
            await r.set(f"dof:dedupe:{dedupe}", job_id, ex=7 * 86400)
    job = {"id": job_id, "kind": kind, "params": params or {}, "dedupe": dedupe,
           "priority": priority, "created": time.time()}
    pipe = r.pipeline()
    pipe.set(f"dof:job:{job_id}", json.dumps(job), ex=7 * 86400)
    if secret:
        # Følsomme data (adgangskode ved login) ligger kun kortvarigt og slettes efter brug
        pipe.set(f"dof:secret:{job_id}", json.dumps(secret), ex=180)
    pipe.zadd(LQ if kind in LOOKUP_KINDS else Q, {job_id: priority * PRIO_STEP + time.time() * 1000})
    await pipe.execute()
    return job_id


async def lookup_depth() -> int:
    """Antal opslag der venter eller udføres."""
    r = kv()
    return await r.zcard(LQ) + await r.scard(LOOKUP_RUNNING)


async def eta(job_ids: list[str]) -> dict:
    """Hvornår de første af jobbene udføres: pending = mindst ét venter, eta = sekunder til det første af dem.
    Opslag udføres straks; udtræk højst ét pr. dof_min_interval (resten af intervallet + ét pr. job foran)."""
    r = kv()
    interval = get_settings().dof_min_interval
    last = float(await r.get(LAST_CALL) or 0)
    wait = max(0.0, last + interval - time.time())
    running = await r.get(RUNNING)
    best = None
    for job_id in job_ids:
        if await r.sismember(LOOKUP_RUNNING, job_id):
            t = 2.0  # opslaget er i gang
        elif (lrank := await r.zrank(LQ, job_id)) is not None:
            t = 2.0 + (lrank // LOOKUP_CONCURRENCY) * 2  # opslag udføres med det samme, nogle få ad gangen
        elif job_id == running:
            t = 5.0  # kaldet er i gang
        else:
            rank = await r.zrank(Q, job_id)
            if rank is None:
                continue  # udført eller væk
            t = wait + rank * interval
        best = t if best is None else min(best, t)
    return {"pending": best is not None, "eta": None if best is None else int(best) + 1}


async def pending_ids() -> set[str]:
    """Id'er på job der venter i køen eller udføres lige nu."""
    r = kv()
    ids = set(await r.zrange(Q, 0, -1)) | set(await r.zrange(LQ, 0, -1)) | set(await r.smembers(LOOKUP_RUNNING))
    running = await r.get(RUNNING)
    if running:
        ids.add(running)
    return ids


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
    data["lookup_queue"] = await r.zcard(LQ)
    data["lookups_running"] = await r.scard(LOOKUP_RUNNING)
    running = await r.get(RUNNING)
    raw = await r.get(f"dof:job:{running}") if running else None
    data["running"] = {k: v for k, v in json.loads(raw).items() if k in ("id", "kind", "params")} if raw else None
    data["min_interval"] = get_settings().dof_min_interval
    last = await r.get(LAST_CALL)
    data["seconds_since_last_call"] = round(time.time() - float(last), 1) if last else None
    return data


async def cancel(job_id: str) -> None:
    r = kv()
    raw = await r.get(f"dof:job:{job_id}")
    await r.zrem(Q, job_id)
    await r.zrem(LQ, job_id)
    if raw:
        job = json.loads(raw)
        if job.get("dedupe"):
            await r.delete(f"dof:dedupe:{job['dedupe']}")
    await r.delete(f"dof:job:{job_id}", f"dof:secret:{job_id}")


async def clear() -> int:
    """Fjern alle ventende job (det kørende kald gøres færdigt). Login-job bevares – en bruger venter på svaret."""
    r = kv()
    removed = 0
    for job_id in await r.zrange(Q, 0, -1):
        raw = await r.get(f"dof:job:{job_id}")
        if raw and json.loads(raw).get("kind") == "login":
            continue
        await cancel(job_id)
        removed += 1
    for job_id in await r.zrange(LQ, 0, -1):  # ventende opslag
        await cancel(job_id)
        removed += 1
    return removed


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
            await r.set(RUNNING, job_id)
            job = json.loads(raw)
            secret = await r.get(f"dof:secret:{job_id}")
            if secret:
                job["secret"] = json.loads(secret)
                await r.delete(f"dof:secret:{job_id}")
            return job

    async def _choose(self) -> tuple[str, dict | None]:
        if await kv().getdel(LIVE_NOW):
            return "live", None
        live_on = await self.live_enabled()
        # Planens interval for "dagen i dag" (0 = slået fra); aldrig under minimumsintervallet
        planned = await kv().get("dof:live_interval")
        if planned is not None and int(float(planned)) == 0:
            live_on = False
        interval = max(self.settings.dof_min_interval, int(float(planned or 0)))
        live_due = live_on and time.time() - self.last_live >= interval - 1
        # Interaktive job (login, billedopslag) går først – men en lang række af dem må ikke tage live-feedet:
        # er live forfalden med mere end LIVE_MAX_GAP sekunder, kommer den imellem
        if live_due and not self.last_was_live and time.time() - self.last_live > LIVE_MAX_GAP:
            return "live", None
        job = await self._pop(max_priority=INTERACTIVE)
        if job:
            return "job", job
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
            if await r.exists(LIVE_NOW):
                return
            last = float(await r.get(LAST_CALL) or 0)
            remaining = last + self.settings.dof_min_interval - time.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def _recover(self) -> None:
        """Ved opstart: genoptag et job der blev afbrudt midt i kaldet, og ryd forældede dedupe-nøgler."""
        r = kv()
        running = await r.get(RUNNING)
        raw = await r.get(f"dof:job:{running}") if running else None
        if raw:
            job = json.loads(raw)
            job["attempts"] = job.get("attempts", 0) + 1
            if job["attempts"] < MAX_ATTEMPTS:
                await r.set(f"dof:job:{job['id']}", json.dumps(job), ex=7 * 86400)
                await r.zadd(Q, {job["id"]: job["priority"] * PRIO_STEP + job["created"] * 1000})
                log.info("Genoptager afbrudt DOFbasen-job %s %s", job["kind"], job["params"])
            else:
                log.warning("Opgiver DOFbasen-job %s %s efter %s afbrudte forsøg", job["kind"], job["params"], job["attempts"])
                await r.delete(f"dof:job:{job['id']}")
                if job.get("dedupe"):
                    await r.delete(f"dof:dedupe:{job['dedupe']}")
        await r.delete(RUNNING)
        # Opslag der blev afbrudt midt i kaldet lægges i kø igen (de er små og kan uden skade gentages)
        for job_id in await r.smembers(LOOKUP_RUNNING):
            raw = await r.get(f"dof:job:{job_id}")
            if raw:
                job = json.loads(raw)
                await r.zadd(LQ, {job_id: job["priority"] * PRIO_STEP + job["created"] * 1000})
        await r.delete(LOOKUP_RUNNING)
        alive = set(await r.zrange(Q, 0, -1)) | set(await r.zrange(LQ, 0, -1))
        stale = 0
        async for key in r.scan_iter(match="dof:dedupe:*", count=500):
            if await r.get(key) not in alive:
                await r.delete(key)
                stale += 1
        if stale:
            log.info("Ryddede %s forældede dedupe-nøgler", stale)

    async def run(self) -> None:
        r = kv()
        await self._recover()
        # Genberegn køens scores ud fra jobbenes gemte prioritet (robust over for ændret score-skema)
        for job_id in await r.zrange(Q, 0, -1):
            raw = await r.get(f"dof:job:{job_id}")
            if raw:
                job = json.loads(raw)
                await r.zadd(Q, {job_id: job["priority"] * PRIO_STEP + job["created"] * 1000})
            else:
                await r.zrem(Q, job_id)
        headers = {"User-Agent": self.settings.dof_user_agent}
        lookups = asyncio.create_task(self.run_lookups(), name="dof-lookups")
        try:
            await self._run_extracts(headers)
        finally:
            lookups.cancel()

    async def _run_extracts(self, headers: dict) -> None:
        r = kv()
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
                            await r.delete(RUNNING)
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

    async def run_lookups(self) -> None:
        """Opslagsbanen: små opslag udføres straks og samtidigt (højst LOOKUP_CONCURRENCY ad gangen) – uden at vente på
        minimumsintervallet og uden at påvirke det. Opslagene deler ikke lås med udtrækkene."""
        r = kv()
        sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)
        tasks: set[asyncio.Task] = set()
        headers = {"User-Agent": self.settings.dof_user_agent}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), headers=headers, follow_redirects=True) as client:
            while True:
                try:
                    if not self.settings.dof_enabled:
                        await asyncio.sleep(10)
                        continue
                    await sem.acquire()
                    popped = None
                    try:
                        items = await r.zpopmin(LQ)
                        if items:
                            popped = items[0][0]
                            await r.sadd(LOOKUP_RUNNING, popped)
                    except BaseException:
                        sem.release()
                        raise
                    if popped is None:
                        sem.release()
                        await asyncio.sleep(0.25)
                        continue
                    task = asyncio.create_task(self._run_lookup(client, popped, sem))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                except asyncio.CancelledError:
                    for t in tasks:
                        t.cancel()
                    raise
                except Exception:  # noqa: BLE001 – løkken må aldrig dø
                    log.exception("Opslagsbane-fejl")
                    await asyncio.sleep(2)

    async def _run_lookup(self, client: httpx.AsyncClient, job_id: str, sem: asyncio.Semaphore) -> None:
        r = kv()
        started = time.time()
        result: Any = None
        error: str | None = None
        job: dict | None = None
        try:
            raw = await r.get(f"dof:job:{job_id}")
            if not raw:
                return  # annulleret imens
            job = json.loads(raw)
            handler = self.handlers.get(job["kind"])
            if handler is None:
                raise ValueError(f"Ukendt jobtype {job['kind']}")
            result = await handler(client, dict(job["params"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            log.warning("DOFbasen-opslag %s fejlede: %s", job and job["kind"], error)
        finally:
            sem.release()
            if job is not None:
                duration = round(time.time() - started, 2)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                pipe = r.pipeline()
                pipe.hincrby(STATUS, f"lookups:{today}", 1)
                pipe.lpush("dof:log", json.dumps({"kind": job["kind"], "params": _safe_params(job), "at": started,
                                                  "duration": duration, "error": error, "result": _summary(result), "lane": "opslag"}))
                pipe.ltrim("dof:log", 0, 199)
                pipe.delete(f"dof:job:{job_id}")
                if job.get("dedupe"):
                    pipe.delete(f"dof:dedupe:{job['dedupe']}")
                pipe.rpush(f"dof:wait:{job_id}", json.dumps({"ok": error is None, "error": error, "result": result}, default=str))
                pipe.expire(f"dof:wait:{job_id}", 300)
                await pipe.execute()
                log.info("DOFbasen opslag %s (%ss)%s", job["kind"], duration, f" FEJL {error}" if error else "")
            await r.srem(LOOKUP_RUNNING, job_id)

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
            pipe.delete(RUNNING)
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
