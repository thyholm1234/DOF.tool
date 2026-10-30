"""Admin → Drift → Backup (superadmin): pg_dump til SFTP efter plan, test, kørsel og download."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    superadmin,
)
from portal.cache import worker_command
from portal.db import get_session
from portal.models import (
    User,
)
from portal.services import (
    activity,
    backup,
)

router = APIRouter(prefix="/api", tags=["admin"])


# ---------------------------------------------------------------- backup (pg_dump): SFTP efter plan og download


class BackupIn(BaseModel):
    host: str = Field("", max_length=253, pattern=r"^[A-Za-z0-9._:-]*$")
    port: int = Field(22, ge=1, le=65535)
    user: str = Field("", max_length=100)
    password: str | None = None  # None = behold; "" = fjern
    private_key: str | None = Field(None, max_length=20000)
    path: str = Field("backup", max_length=300)
    keep: int = Field(14, ge=1, le=1000)


async def _backup_panel(db: AsyncSession) -> dict:
    return {**backup.public(await backup.load(db)), "last": await backup.last(), "targets": [n for n, _ in backup.targets()]}


@router.get("/admin/backup")
async def get_backup(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    return await _backup_panel(db)


@router.put("/admin/backup")
async def put_backup(body: BackupIn, user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    if (body.host, body.port) != (cfg["host"], cfg["port"]):
        cfg["host_key"] = ""  # ny server: værtsnøglen skal godkendes igen
    cfg.update(host=body.host, port=body.port, user=body.user, path=body.path.strip(), keep=body.keep)
    if body.password is not None:
        cfg["password"] = body.password
    if body.private_key is not None:
        if body.private_key.strip():
            try:
                backup.check_private_key(body.private_key.strip())
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, f"Den private nøgle kan ikke læses (kun nøgler uden adgangskode): {exc}") from exc
        cfg["private_key"] = body.private_key.strip()
    await backup.save(db, cfg)
    await activity.track(user.id, "backup", None, {"host": body.host, "user": body.user, "path": body.path})
    return await _backup_panel(db)


@router.get("/admin/backup/host-key")
async def backup_host_key(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Serverens værtsnøgle (fingeraftryk) – godkendes med POST, før workeren vil forbinde."""
    cfg = await backup.load(db)
    if not cfg["host"]:
        raise HTTPException(400, "Angiv først en server")
    try:
        key = await backup.fetch_host_key(cfg["host"], int(cfg["port"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Kunne ikke hente værtsnøglen: {exc or type(exc).__name__}") from exc
    return {"key": key, "fingerprint": backup.fingerprint(key)}


@router.post("/admin/backup/host-key")
async def trust_host_key(body: dict[str, str], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    try:
        backup.fingerprint(body.get("key", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Ugyldig værtsnøgle") from exc
    cfg["host_key"] = body["key"]
    await backup.save(db, cfg)
    await activity.track(user.id, "backup_host_key", None, {"host": cfg["host"], "fingerprint": backup.fingerprint(body["key"])})
    return await _backup_panel(db)


@router.post("/admin/backup/test")
async def test_backup(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    cfg = await backup.load(db)
    if not backup.configured(cfg):
        raise HTTPException(400, "Udfyld server, bruger, kodeord eller nøgle, og godkend værtsnøglen")
    try:
        files = await backup.check(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"SFTP: {exc or type(exc).__name__}") from exc
    return {"files": files}


@router.post("/admin/backup/run")
async def run_backup(user: User = Depends(superadmin)):
    await worker_command("backup")
    return {"queued": "backup"}


@router.get("/admin/backup/download/{name}")
async def download_backup(name: str, user: User = Depends(superadmin)):
    """pg_dump af én database direkte til browseren (pg_restore-format)."""
    try:
        node = backup.target(name)
    except KeyError as exc:
        raise HTTPException(404, "Ukendt database") from exc
    stream = backup.dump(node)
    try:
        first = await anext(stream)  # fejler forbindelsen, får admin en fejl i stedet for en tom fil
    except StopAsyncIteration:
        first = b""
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    await activity.track(user.id, "backup_download", None, {"database": name})

    async def body():
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(body(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{backup.filename(name)}"'})
