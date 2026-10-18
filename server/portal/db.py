from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import UserDefinedType

from portal.config import get_settings


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


engine = create_async_engine(
    get_settings().postgres_dsn,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
