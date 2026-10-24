"""Lagerforbrug til Admin → Drift: databaser (hver node og de største tabeller), objektlageret, filer på disken og Valkey.

Målingen gemmes 5 minutter, da gennemgang af mapperne og objektlageret kan tage lidt tid."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from portal.cache import get_json, kv, set_json
from portal.config import get_settings
from portal.db import router
from portal.services import storage

KEY = "admin:storage"
TTL = 300
TOP_TABLES = 8
GROUP_LABEL = {"users": "Brugerdata", "observations": "Observationer"}


def _du(path: Path) -> tuple[int, int]:
    """(antal filer, bytes) under en mappe."""
    n = size = 0
    if not path.is_dir():
        return 0, 0
    stack = [path]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        n += 1
                        size += e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return n, size


def _files() -> list[dict]:
    s = get_settings()
    media = Path(s.media_dir)
    learn = [p for p in media.iterdir() if p.is_file()] if media.is_dir() else []  # læringens filer ligger øverst
    out = [{"name": "Læring (billeder og lyd)", "files": len(learn), "bytes": sum(p.stat().st_size for p in learn)}]
    for sub, label in (("netfugl", "Netfugl-fotos"), ("xc", "Mobgang-lyde (xeno-canto)"), ("obs", "Observationer (på disken)")):
        n, b = _du(media / sub)
        if sub != "obs" or n:
            out.append({"name": label, "files": n, "bytes": b})
    for path, label in ((s.weather_dir, "Vejrdata (midlertidige DMI-filer)"), (s.archive_upload_dir, "Uploadede månedsfiler"),
                        (s.archive_dir, "DOFbasen-arkiv (månedsudtræk)")):
        n, b = _du(Path(path))
        out.append({"name": label, "files": n, "bytes": b})
    return out


def _disk() -> dict | None:
    try:
        d = shutil.disk_usage(get_settings().media_dir)
    except OSError:
        return None
    return {"total": d.total, "used": d.used, "free": d.free}


async def _databases() -> list[dict]:
    out, seen = [], set()
    for node in router.nodes:
        if node.key in seen:
            continue
        seen.add(node.key)
        label = "Samlet database" if not router.separate() else f"{GROUP_LABEL.get(node.group, node.group)} · {node.name} ({node.role})"
        try:
            async with router.engine_for(node).connect() as conn:
                await conn.execute(text("SET LOCAL statement_timeout = '15s'"))
                size = (await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar()
                tables = (await conn.execute(text("""
                    SELECT c.relname, pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS rows
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'm', 'p')
                    ORDER BY 2 DESC LIMIT :n"""), {"n": TOP_TABLES})).mappings().all()
            out.append({"name": label, "bytes": size, "tables": [{"name": t["relname"], "bytes": t["bytes"], "rows": max(t["rows"], 0)}
                                                                 for t in tables]})
        except Exception as exc:  # noqa: BLE001 – en node, der er nede, må ikke vælte oversigten
            out.append({"name": label, "bytes": None, "error": str(exc)[:200], "tables": []})
    return out


async def _objects() -> dict:
    s = get_settings()
    name = f"Objektlager ({s.s3_endpoint}, bucket {s.s3_bucket})" if storage.enabled() else "Observationer (på disken)"
    try:
        n, b = await storage.usage()
        return {"name": name, "files": n, "bytes": b, "enabled": storage.enabled()}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "files": None, "bytes": None, "enabled": storage.enabled(), "error": str(exc)[:200]}


async def _valkey() -> dict:
    info = await kv().info("memory")
    return {"name": "Valkey (cache, køer og sessioner)", "bytes": int(info.get("used_memory", 0)), "keys": await kv().dbsize()}


async def measure(refresh: bool = False) -> dict:
    if not refresh and (hit := await get_json(KEY)):
        return hit
    dbs, objects, valkey, files, disk = await asyncio.gather(_databases(), _objects(), _valkey(), asyncio.to_thread(_files),
                                                             asyncio.to_thread(_disk))
    data = {"measured_at": datetime.now(timezone.utc).isoformat(), "databases": dbs, "objects": objects, "valkey": valkey,
            "files": files, "disk": disk}
    await set_json(KEY, data, TTL)
    return data
