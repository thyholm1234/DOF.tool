"""Profilbillede: upload (beskæres og skaleres til to størrelser) og fjern (services/avatar.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.cache import kv
from portal.db import get_session
from portal.models import User
from portal.services import avatar

router = APIRouter(prefix="/api/me/avatar", tags=["avatar"])


@router.post("")
async def upload(file: UploadFile = File(...), user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    data = await file.read(avatar.MAX_BYTES + 1)
    key = f"avatar:rate:{user.id}"
    n = await kv().incr(key)
    if n == 1:
        await kv().expire(key, 600)
    if n > 20:
        raise HTTPException(429, "For mange uploads – vent lidt")
    try:
        urls = await avatar.set_avatar(db, user, data)
    except avatar.AvatarError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return {"avatar": urls}


@router.delete("")
async def remove(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    await avatar.clear_avatar(db, user)
    await db.commit()
    return {"avatar": None}
