"""Gatewayens rate-grænse og planlægning testes mod en falsk Valkey i hukommelsen."""

import asyncio
import time

import pytest

from portal.dof import gateway


class FakeRedis:
    def __init__(self):
        self.kv, self.z, self.h, self.lists = {}, {}, {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = str(v)
        return True

    async def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)

    async def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    async def zrange(self, k, a, b, withscores=False):
        items = sorted(self.z.get(k, {}).items(), key=lambda x: x[1])
        items = items[a:(b + 1 if b >= 0 else None)]
        return items if withscores else [i[0] for i in items]

    async def zrem(self, k, m):
        return 1 if self.z.get(k, {}).pop(m, None) is not None else 0

    async def zcard(self, k):
        return len(self.z.get(k, {}))

    def pipeline(self):
        return FakePipe(self)


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a, **kw):
            self.ops.append((name, a, kw))
            return self
        return op

    async def execute(self):
        for name, a, kw in self.ops:
            fn = getattr(self.r, name, None)
            if fn:
                await fn(*a, **kw)


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(gateway, "kv", lambda: r)
    return r


async def test_enqueue_dedupes_and_orders_by_priority(fake):
    a = await gateway.enqueue("day", {"date": "2025-01-01"}, priority=gateway.BULK, dedupe="day:1")
    b = await gateway.enqueue("day", {"date": "2025-01-01"}, priority=gateway.BULK, dedupe="day:1")
    c = await gateway.enqueue("history", {"obserkode": "X"}, priority=gateway.INTERACTIVE, secret={"password": "p"})
    assert a == b
    gw = gateway.Gateway({}, None, None)
    job = await gw._pop()
    assert job["id"] == c and job["secret"] == {"password": "p"}
    assert await fake.get(f"dof:secret:{c}") is None  # hemmeligheden fjernes straks fra Valkey
    assert (await gw._pop())["id"] == a


async def test_login_skips_extract_queue(fake):
    """Login går i opslagsbanen (uden minimumsinterval) og bevares, når køen ryddes."""
    job_id = await gateway.enqueue("login", {"obserkode": "X"}, priority=gateway.INTERACTIVE, secret={"password": "p"})
    assert await fake.zcard(gateway.Q) == 0 and await fake.zrange(gateway.LQ, 0, -1) == [job_id]
    await gateway.clear()
    assert await fake.zrange(gateway.LQ, 0, -1) == [job_id]


async def test_live_and_queue_alternate(fake):
    async def enabled():
        return True
    gw = gateway.Gateway({}, None, enabled)
    gw.settings.dof_min_interval = 60
    await gateway.enqueue("day", {"date": "x"}, priority=gateway.BULK)
    kind, _ = await gw._choose()
    assert kind == "live"
    gw.last_was_live, gw.last_live = True, time.time() - 61
    kind, job = await gw._choose()
    assert kind == "job" and job["kind"] == "day"


async def test_wait_for_slot_respects_minimum_interval(fake, monkeypatch):
    gw = gateway.Gateway({}, None, None)
    gw.settings.dof_min_interval = 60
    await fake.set(gateway.LAST_CALL, time.time() - 59.5)
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        await fake.set(gateway.LAST_CALL, float(await fake.get(gateway.LAST_CALL)) - s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await gw._wait_for_slot()
    assert slept and sum(slept) >= 0.4


def test_min_interval_cannot_be_lowered():
    from portal.config import Settings
    assert Settings(dof_min_interval=5).dof_min_interval == 60


async def test_interactive_beats_old_bulk_jobs(fake):
    """Regression: prioritet skal vægte tungere end tidsstempel (ms siden 1970)."""
    await gateway.enqueue("day", {"date": "a"}, priority=gateway.BULK)
    await gateway.enqueue("history", {"obserkode": "X"}, priority=gateway.INTERACTIVE)
    gw = gateway.Gateway({}, None, None)
    job = await gw._pop(max_priority=gateway.INTERACTIVE)
    assert job is not None and job["kind"] == "history"
