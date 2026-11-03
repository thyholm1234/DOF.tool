"""Adgang til artsquizzen: funktionen «artsquiz» skal være synlig, og brugeren skal kunne se fotomodulet (spørgsmålene er fotos)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import features, photo_access


async def artsquiz(db: AsyncSession, user: User) -> bool:
    if not features.visible(await features.load(db), "artsquiz", superadmin=user.role == "superadmin", insider=False):
        return False
    return (await photo_access.context(db, user)).on("fotos")
