"""Backup af databaserne med pg_dump: til en SFTP-server efter en fast plan (workeren) eller som download (Admin → Drift).

Én dump pr. database: i legacy-tilstand den samlede database ('portal'), ellers bruger- og observationsgruppens primary
hver for sig. Dumpen er pg_dumps eget format (-Fc, komprimeret) og gendannes med pg_restore. Den streames direkte fra
pg_dump til SFTP-serveren (først som .part, omdøbt når den er færdig), så workeren ikke skal have plads til den på disken.
Kun de nyeste `keep` filer pr. database beholdes i mappen.

SFTP-serverens værtsnøgle hentes og godkendes én gang fra admin-panelet (trust on first use); derefter afvises en
server med en anden nøgle.
"""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
from collections.abc import AsyncIterator
from contextlib import suppress

import asyncssh

from sqlalchemy.ext.asyncio import AsyncSession

from portal import jobstatus
from portal.cache import kv
from portal.dbgroups import GROUPS
from portal.dbrouter import Node, router
from portal.timeutil import dk_now
from portal.services.setting_store import get_setting_dict, put_setting

SETTING = "backup:sftp"
LAST = "backup:last"
CHUNK = 1 << 20
PREFIX = "doftool-"
DEFAULTS = {"host": "", "port": 22, "user": "", "password": "", "private_key": "", "path": "backup", "keep": 14, "host_key": ""}


async def load(session: AsyncSession) -> dict:
    return await get_setting_dict(session, SETTING, DEFAULTS)


async def save(session: AsyncSession, cfg: dict) -> None:
    await put_setting(session, SETTING, cfg)


def configured(cfg: dict) -> bool:
    return bool(cfg["host"] and cfg["user"] and (cfg["password"] or cfg["private_key"]) and cfg["host_key"])


def public(cfg: dict) -> dict:
    """Opsætningen uden hemmeligheder (til admin-panelet)."""
    out = {k: cfg[k] for k in ("host", "port", "user", "path", "keep")}
    out["has_password"], out["has_key"] = bool(cfg["password"]), bool(cfg["private_key"])
    out["host_key"] = fingerprint(cfg["host_key"]) if cfg["host_key"] else None
    out["configured"] = configured(cfg)
    return out


# ---------------------------------------------------------------- pg_dump


def targets() -> list[tuple[str, Node]]:
    """(navn, node) for hver database der skal dumpes."""
    if router.legacy or not router.separate():
        return [("portal", router.primary_node(GROUPS[0]))]
    return [(g, router.primary_node(g)) for g in GROUPS]


def target(name: str) -> Node:
    for n, node in targets():
        if n == name:
            return node
    raise KeyError(name)


def filename(name: str) -> str:
    return f"{PREFIX}{name}-{dk_now():%Y-%m-%d_%H%M}.dump"


async def dump(node: Node) -> AsyncIterator[bytes]:
    """pg_dump af noden i bidder. Fejler pg_dump, kastes RuntimeError med dens fejltekst (efter de bidder der nåede frem)."""
    proc = await asyncio.create_subprocess_exec(
        "pg_dump", "-Fc", "--no-password", "-h", node.host, "-p", str(node.port), "-U", node.user, "-d", node.dbname,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, "PGPASSWORD": node.token})
    err = asyncio.create_task(proc.stderr.read())
    try:
        while chunk := await proc.stdout.read(CHUNK):
            yield chunk
        if await proc.wait():
            raise RuntimeError(f"pg_dump fejlede: {(await err).decode(errors='replace').strip()[:500]}")
    finally:
        if proc.returncode is None:  # afbrudt (fx download annulleret)
            proc.kill()
            await proc.wait()
        err.cancel()


# ---------------------------------------------------------------- SFTP


def check_private_key(text: str) -> None:
    asyncssh.import_private_key(text)


def fingerprint(host_key: str) -> str:
    return asyncssh.import_public_key(host_key).get_fingerprint()


async def fetch_host_key(host: str, port: int) -> str:
    """Serverens offentlige værtsnøgle (OpenSSH-format) – vises som fingeraftryk og godkendes i admin-panelet."""
    key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port), 15)
    if key is None:
        raise RuntimeError("Serveren sendte ingen værtsnøgle")
    return key.export_public_key().decode().strip()


def _connect(cfg: dict):
    host = cfg["host"] if int(cfg["port"]) == 22 else f"[{cfg['host']}]:{cfg['port']}"
    return asyncssh.connect(
        cfg["host"], port=int(cfg["port"]), username=cfg["user"], password=cfg["password"] or None,
        client_keys=[asyncssh.import_private_key(cfg["private_key"])] if cfg["private_key"] else None,
        known_hosts=asyncssh.import_known_hosts(f"{host} {cfg['host_key']}\n"), connect_timeout=20)


async def check(cfg: dict) -> list[str]:
    """Log ind og list mappen (opretter den, hvis den mangler). Returnerer de backupfiler der ligger der."""
    async with _connect(cfg) as conn, conn.start_sftp_client() as sftp:
        await sftp.makedirs(cfg["path"] or ".", exist_ok=True)
        return sorted(n for n in await sftp.listdir(cfg["path"] or ".") if n.startswith(PREFIX))


async def to_sftp(cfg: dict) -> None:
    """Dump hver database til SFTP-serveren og ryd op i gamle filer. Fremdrift og fejl vises under Worker-opgaver."""
    base = cfg["path"] or "."
    done: list[dict] = []
    started = dk_now().isoformat()
    try:
        async with _connect(cfg) as conn, conn.start_sftp_client() as sftp:
            await sftp.makedirs(base, exist_ok=True)
            tg = targets()
            for i, (name, node) in enumerate(tg):
                fn = filename(name)
                path, part = posixpath.join(base, fn), posixpath.join(base, fn + ".part")
                await jobstatus.step(f"Dumper {name}", i, len(tg))
                size = 0
                try:
                    async with sftp.open(part, "wb") as f:
                        async for chunk in dump(node):
                            await f.write(chunk)
                            size += len(chunk)
                    await sftp.rename(part, path)
                except BaseException:
                    with suppress(Exception):
                        await sftp.remove(part)
                    raise
                done.append({"name": fn, "size": size})
            await jobstatus.step("Rydder op", len(tg), len(tg))
            names = await sftp.listdir(base)
            for name, _ in tg:
                own = sorted(n for n in names if n.startswith(f"{PREFIX}{name}-") and n.endswith(".dump"))
                for old in own[:-max(1, int(cfg["keep"]))]:
                    await sftp.remove(posixpath.join(base, old))
    except Exception as exc:
        await _last({"at": started, "ok": False, "files": done, "error": str(exc)[:500]})
        raise
    await _last({"at": started, "ok": True, "files": done, "error": None})


async def last() -> dict | None:
    raw = await kv().get(LAST)
    return json.loads(raw) if raw else None


async def _last(d: dict) -> None:
    await kv().set(LAST, json.dumps(d))
