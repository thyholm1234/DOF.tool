"""Optagelseskontrol: højst API_MAX_INFLIGHT samtidige API-forespørgsler pr. uvicorn-proces.

En forespørgsel, der ikke får en plads inden for API_QUEUE_WAIT_S, afvises straks med 503 og Retry-After i stedet for at
stå i kø, til klienten alligevel har opgivet. Uden grænsen vokser køen frit under overbelastning, og serveren bruger
tiden på svar, ingen venter på – så falder alt, ikke kun de tunge sider. (uvicorns --limit-concurrency tæller åbne
forbindelser, også keep-alive og SSE-strømmen, og kan derfor ikke bruges her.)

Undtaget: alt uden for /api/ (statiske filer, medier, talemodel), SSE-strømmen, health-checket og lange downloads.
Kode, der venter længe uden at bruge CPU (fx på DOFbasen-gatewayens kø), giver pladsen tilbage med `async with idle():`.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from contextlib import asynccontextmanager

log = logging.getLogger("portal.admission")

EXEMPT_EXACT = {"/api/stream", "/api/health"}
# Lange overførsler (backup, dataeksport, lyd fra xeno-canto), der ellers ville holde en plads i minutter
EXEMPT_PREFIX = ("/api/admin/backup/download/", "/api/me/export", "/api/mobgang/xc/")
BUSY_BODY = json.dumps({"detail": "Serveren har travlt lige nu – prøv igen om lidt."}, ensure_ascii=False).encode()
RETRY_AFTER = "5"
LOG_EVERY = 10.0  # sekunder mellem advarsler om afviste forespørgsler


class _Slot:
    """Den aktuelle forespørgsels plads (deles via contextvar, så idle() kan give den tilbage)."""

    __slots__ = ("sem", "held")

    def __init__(self, sem: asyncio.Semaphore):
        self.sem = sem
        self.held = True


_current: contextvars.ContextVar[_Slot | None] = contextvars.ContextVar("admission_slot", default=None)


@asynccontextmanager
async def idle():
    """Giv pladsen tilbage, mens der ventes på noget uden CPU-forbrug, og tag den igen bagefter (uden frist – arbejdet
    er allerede i gang). Uden for en optaget forespørgsel (worker, test) gør den intet."""
    slot = _current.get()
    if slot is None or not slot.held:
        yield
        return
    slot.sem.release()
    slot.held = False
    try:
        yield
    finally:
        await slot.sem.acquire()
        slot.held = True


def exempt(path: str) -> bool:
    return not path.startswith("/api/") or path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIX)


class AdmissionMiddleware:
    """Ren ASGI-middleware (ingen BaseHTTPMiddleware: afvisningen skal være billig, og strømme må ikke bufres)."""

    def __init__(self, app, max_inflight: int = 16, queue_wait: float = 0.5):
        self.app = app
        self.max_inflight = max_inflight
        self.queue_wait = queue_wait
        self._sem: asyncio.Semaphore | None = None  # oprettes i event-loopet ved første forespørgsel
        self.rejected = 0
        self._logged_at = 0.0
        self._logged_count = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_inflight <= 0 or exempt(scope["path"]):
            return await self.app(scope, receive, send)
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_inflight)
        try:
            await asyncio.wait_for(self._sem.acquire(), self.queue_wait)
        except TimeoutError:
            self._reject_log()
            return await self._busy(send)
        slot = _Slot(self._sem)
        token = _current.set(slot)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
            if slot.held:
                slot.held = False
                self._sem.release()

    def _reject_log(self) -> None:
        self.rejected += 1
        now = time.monotonic()
        if now - self._logged_at >= LOG_EVERY:
            log.warning("Overbelastet: %s forespørgsler afvist med 503 (højst %s samtidige, %.1f s ventetid)",
                        self.rejected - self._logged_count, self.max_inflight, self.queue_wait)
            self._logged_at, self._logged_count = now, self.rejected

    @staticmethod
    async def _busy(send) -> None:
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(BUSY_BODY)).encode()),
            (b"retry-after", RETRY_AFTER.encode()),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": BUSY_BODY})
