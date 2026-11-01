"""Redaktion → Oversigt: de samlede moderationskøer på tværs af modulerne (services/redaktion.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from portal.api.deps import require_user
from portal.db import get_session
from portal.models import User
from portal.services import redaktion

router = APIRouter(prefix="/api/admin/redaktion", tags=["admin"])


@router.get("")
async def oversigt(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    """Køerne brugeren har adgang til, med antal der venter. Tom liste = ingen moderationsrettigheder."""
    return {"koeer": await redaktion.queues(db, user)}
