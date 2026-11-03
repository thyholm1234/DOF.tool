"""Adgang til Læring: funktionsflag (se services/features.py) og hvem der må bygge/undervise."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from portal.models import User
from portal.services import features, photo_access
from portal.services.auth import can


def _insider(user: User | None) -> bool:
    """Redaktører og undervisere ser forhåndsvisninger (preview) af Læring."""
    return bool(user) and (can(user, "laering") or can(user, "laering_undervis"))


async def flag(db: AsyncSession, user: User | None, key: str) -> bool:
    return features.visible(await features.load(db), key, superadmin=bool(user) and user.role == "superadmin", insider=_insider(user))


async def portal(db: AsyncSession, user: User | None) -> bool:
    return await flag(db, user, "laering_portal")


async def data(db: AsyncSession, user: User | None) -> bool:
    return await flag(db, user, "laering_data")


async def byg(db: AsyncSession, user: User | None) -> bool:
    return await flag(db, user, "laering_byg")


async def forloeb(db: AsyncSession, user: User | None) -> bool:
    return await flag(db, user, "laering_forloeb")


async def felt(db: AsyncSession, user: User | None) -> bool:
    return await flag(db, user, "laering_felt")


async def artsquiz(db: AsyncSession, user: User) -> bool:
    if not features.visible(await features.load(db), "artsquiz", superadmin=user.role == "superadmin", insider=False):
        return False
    return (await photo_access.context(db, user)).on("fotos")


async def lydquiz(db: AsyncSession, user: User) -> bool:
    return features.visible(await features.load(db), "lydquiz", superadmin=user.role == "superadmin", insider=False)


def can_build(user: User | None) -> bool:
    return can(user, "laering") or can(user, "laering_undervis")
