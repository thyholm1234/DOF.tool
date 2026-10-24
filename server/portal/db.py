from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import UserDefinedType

from portal.dbrouter import ObsSession, SessionLocal, router  # noqa: F401 – genudstilles til resten af koden


class Base(DeclarativeBase):
    pass


class Geometry(UserDefinedType):
    """PostGIS-geometri. Læses/skrives altid via ST_*-funktioner i SQL, aldrig som Python-objekt."""

    cache_ok = True

    def __init__(self, kind: str = "Geometry", srid: int = 4326) -> None:
        self.kind = kind
        self.srid = srid

    def get_col_spec(self, **_: object) -> str:
        return f"geometry({self.kind},{self.srid})"


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
