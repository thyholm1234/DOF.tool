"""Admin: portalindstillinger (offentlige og under Drift/Brugere), funktionsflag og FCM-konto (superadmin)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.admin._deps import (
    superadmin,
)
from portal.api.deps import require_user
from portal.cache import bump
from portal.db import get_session
from portal.models import (
    Setting,
    User,
)
from portal.services import (
    activity,
    fcm,
    features,
)
from portal.services.auth import (
    MODULES,
    can,
)
from portal.services.setting_store import put_setting

router = APIRouter(prefix="/api", tags=["admin"])


SETTINGS = {"live_enabled": True, "matrikel_tag": "#BB", "welcome": "", "require_approval": True}
# Indstillinger der styres fra et andet modul end Drift
SETTING_MODULE = {"require_approval": "brugere"}


async def get_settings_dict(db: AsyncSession) -> dict:
    rows = {s.key: s.value for s in (await db.execute(select(Setting).where(Setting.key.in_(list(SETTINGS))))).scalars()}
    return {k: rows.get(k, v) for k, v in SETTINGS.items()}


@router.get("/settings/public")
async def public_settings(db: AsyncSession = Depends(get_session)):
    s = await get_settings_dict(db)
    return {"matrikel_tag": s["matrikel_tag"], "welcome": s["welcome"]}


@router.put("/admin/settings")
async def put_settings(body: dict[str, Any], user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    for k, v in body.items():
        if k not in SETTINGS:
            raise HTTPException(400, f"Ukendt indstilling {k}")
        module = SETTING_MODULE.get(k, "drift")
        if not can(user, module):
            raise HTTPException(403, f"Kræver adgang til {MODULES[module].split(':')[0]}")
        await put_setting(db, k, v, commit=False)
    await db.commit()
    if "matrikel_tag" in body:
        await bump("lists")
    return await get_settings_dict(db)


@router.get("/admin/features")
async def get_features(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Funktioner, der kan slås til/fra (kun superadmin): off / preview (kun superadmin og DOFbasen-roller) / on."""
    flags = await features.load(db)
    return [{"key": k, "name": name, "description": desc, "parent": parent, "default": default, "state": flags[k]}
            for k, (name, desc, parent, default) in features.FEATURES.items()]


@router.put("/admin/features")
async def put_features(body: dict[str, str], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    try:
        await features.save(db, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await activity.track(user.id, "features", None, body)
    return await get_features(user, db)


@router.get("/admin/fcm")
async def get_fcm(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Firebase (push til Android-appen): er en servicekonto sat op, og hvorfra (.env eller her)?"""
    await fcm.ensure(db)
    acc = fcm._account()
    return {"source": fcm.source(), "project_id": acc and acc["project_id"], "client_email": acc and acc["client_email"],
            "devices": (await db.execute(text("SELECT count(*) FROM push_subscriptions WHERE endpoint LIKE 'fcm:%'"))).scalar(),
            "last_error": fcm.LAST["error"]}


@router.put("/admin/fcm")
async def put_fcm(body: dict[str, Any], user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    """Gem servicekontoens JSON (Firebase → Projektindstillinger → Tjenestekonti → Generér ny privat nøgle)."""
    acc = body.get("account")
    if isinstance(acc, str):
        try:
            acc = json.loads(acc)
        except ValueError as exc:
            raise HTTPException(400, "Filen er ikke gyldig JSON") from exc
    if not isinstance(acc, dict) or acc.get("type") != "service_account" or not fcm.valid(acc):
        raise HTTPException(400, "Det er ikke en servicekonto-nøgle (mangler type, project_id, client_email eller private_key)")
    try:
        fcm._assertion(acc)  # kan nøglen signere?
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Den private nøgle kan ikke bruges: {exc}") from exc
    await put_setting(db, fcm.SETTING, acc)
    fcm.set_db_account(acc)
    await activity.track(user.id, "fcm", None, {"project_id": acc["project_id"]})
    return await get_fcm(user, db)


@router.delete("/admin/fcm")
async def delete_fcm(user: User = Depends(superadmin), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(Setting).where(Setting.key == fcm.SETTING))
    await db.commit()
    fcm.set_db_account(None)
    return await get_fcm(user, db)
