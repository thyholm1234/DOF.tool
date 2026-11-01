"""Admin → Se som bruger (kun superadmin): se appen som en anden bruger i 15 minutter – kun læsning, logget. Tilstanden håndhæves af middleware i main.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import COOKIE, real_user
from portal.db import get_session
from portal.models import User
from portal.services import view_as

router = APIRouter(prefix="/api/admin/se-som", tags=["admin"])


class StartIn(BaseModel):
    user_id: int
    grund: str = Field(min_length=3, max_length=300)


@router.post("")
async def start(body: StartIn, request: Request, real: User | None = Depends(real_user), db: AsyncSession = Depends(get_session)):
    if real is None:
        raise HTTPException(401, "Log ind for at bruge denne funktion")
    try:
        d = await view_as.start(db, real, request.cookies.get(COOKIE) or "", body.user_id, body.grund)
    except view_as.ViewAsError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return {"until": d["until"]}


@router.delete("")
async def end(request: Request, real: User | None = Depends(real_user)):
    await view_as.end(real, request.cookies.get(COOKIE))
    return {"ok": True}
