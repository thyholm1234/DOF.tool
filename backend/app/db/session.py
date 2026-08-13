from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(settings.postgres_dsn, echo=False, future=True)
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session
