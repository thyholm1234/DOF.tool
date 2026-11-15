"""Optagelseskontrollen (portal/admission.py): grænse, afvisning med 503, undtagelser og idle()."""

import asyncio

import httpx

from portal import admission
from portal.admission import AdmissionMiddleware


def make(max_inflight=1, queue_wait=0.05):
    gate = asyncio.Event()
    entered = asyncio.Event()
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        if scope["path"] == "/api/slow":
            entered.set()
            await gate.wait()
        elif scope["path"] == "/api/idle":
            entered.set()
            async with admission.idle():
                await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = AdmissionMiddleware(app, max_inflight=max_inflight, queue_wait=queue_wait)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mw), base_url="http://t")
    return mw, client, gate, entered, seen


async def test_rejects_with_503_when_full_and_recovers():
    mw, client, gate, entered, seen = make()
    async with client:
        slow = asyncio.create_task(client.get("/api/slow"))
        await entered.wait()
        busy = await client.get("/api/feed")
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "5"
        assert "travlt" in busy.json()["detail"]
        assert "/api/feed" not in seen  # appen blev aldrig kaldt
        assert mw.rejected == 1
        gate.set()
        assert (await slow).status_code == 200
        assert (await client.get("/api/feed")).status_code == 200  # pladsen er givet tilbage


async def test_waits_for_slot_within_queue_wait():
    mw, client, gate, entered, _ = make(queue_wait=2)
    async with client:
        slow = asyncio.create_task(client.get("/api/slow"))
        await entered.wait()
        waiting = asyncio.create_task(client.get("/api/feed"))
        await asyncio.sleep(0.05)
        gate.set()
        assert (await waiting).status_code == 200
        assert (await slow).status_code == 200
        assert mw.rejected == 0


async def test_exempt_paths_bypass_limit():
    _, client, gate, entered, _ = make()
    async with client:
        slow = asyncio.create_task(client.get("/api/slow"))
        await entered.wait()
        for path in ("/api/health", "/api/stream", "/index.html", "/media/obs/x.jpg", "/api/me/export"):
            assert (await client.get(path)).status_code == 200, path
        gate.set()
        await slow


async def test_idle_releases_slot_while_waiting():
    _, client, gate, entered, _ = make()
    async with client:
        waiting = asyncio.create_task(client.get("/api/idle"))
        await entered.wait()
        assert (await client.get("/api/feed")).status_code == 200  # pladsen var fri, mens /api/idle ventede
        gate.set()
        assert (await waiting).status_code == 200
        assert (await client.get("/api/feed")).status_code == 200  # og den er ikke givet tilbage to gange ved fejl


async def test_disabled_and_idle_outside_request():
    _, client, gate, entered, _ = make(max_inflight=0)
    async with client:
        gate.set()
        assert (await client.get("/api/slow")).status_code == 200
    async with admission.idle():  # i workeren/uden forespørgsel: ingen effekt
        pass
