"""Valkey (Redis-kompatibel) klient + små hjælpere til JSON-cache og pub/sub."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from portal.config import get_settings

_client: aioredis.Redis | None = None


def kv() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_dsn, decode_responses=True, socket_timeout=30,
                                    socket_connect_timeout=5, health_check_interval=30)
    return _client


async def get_json(key: str) -> Any | None:
    raw = await kv().get(key)
    return json.loads(raw) if raw else None


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    await kv().set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)


async def cached(key: str, ttl: int, producer: Callable[[], Awaitable[Any]]) -> Any:
    hit = await get_json(key)
    if hit is not None:
        return hit
    value = await producer()
    await set_json(key, value, ttl)
    return value


async def bump(name: str) -> int:
    """Versionsnøgle til cache-invalidering (fx 'feed', 'lists')."""
    return int(await kv().incr(f"ver:{name}"))


async def version(name: str) -> int:
    return int(await kv().get(f"ver:{name}") or 0)


async def publish(channel: str, payload: dict) -> None:
    await kv().publish(channel, json.dumps(payload, ensure_ascii=False, default=str))


COMMANDS = "worker:commands"


async def worker_command(cmd: str, **params) -> None:
    from portal import jobstatus

    await kv().rpush(COMMANDS, json.dumps({"cmd": cmd, **params}))
    name = "plan" if cmd == "run_plan" else cmd
    if name in jobstatus.LABELS:
        await jobstatus.queued(name)
