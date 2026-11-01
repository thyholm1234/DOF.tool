"""Fotomodulet (brugerdelen): galleri, fotoside, kommentarer, upload og Mine fotos.

Alt kræver, at funktionen «fotos» er synlig for brugeren (Admin → Funktioner); ellers svarer API'et 404. Moderation og import ligger i
api/admin/fotos.py. SQL ligger i services (photo_*)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import photo_access, photo_codes

router = APIRouter(prefix="/api/fotos", tags=["fotos"])


def need(feature: str = "fotos"):
    async def dep(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)) -> photo_access.Access:
        acc = await photo_access.context(db, user)
        if not acc.on(feature):
            raise HTTPException(404, "Funktionen er ikke slået til")
        return acc

    return dep


@router.get("/koder")
async def koder(acc: photo_access.Access = Depends(need()), db: AsyncSession = Depends(get_session)):
    """Kodelister (adfærd, køn, alder, dragt), emneord, rettigheder, sjældenhed og lande."""
    out = await photo_codes.all_codes(db)
    await db.commit()  # all_codes lægger seedet ind første gang
    return out
