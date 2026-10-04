import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from backend.app.api.router import api_router
from backend.app.api.routes.legacy_compat import router as legacy_compat_router
from backend.app.api.routes.legacy_features import router as legacy_features_router
from backend.app.api.routes.legacy_extended import router as legacy_extended_router
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models import CommunityContact, CommunityDocument, CommunityEvent, CommunityNews, FlashcardModel
from backend.app.db.session import SessionLocal, engine
from backend.app.services.daily_sync import run_daily_sync

settings = get_settings()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text(
                "ALTER TABLE community_events "
                "ADD COLUMN IF NOT EXISTS recurrence_rule VARCHAR(24), "
                "ADD COLUMN IF NOT EXISTS recurrence_until TIMESTAMP WITH TIME ZONE"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE observations "
                "ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION, "
                "ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION, "
                "ADD COLUMN IF NOT EXISTS enriched_class VARCHAR(32) DEFAULT 'ALM', "
                "ADD COLUMN IF NOT EXISTS remarkable_count INTEGER"
            )
        )

    async with SessionLocal() as session:
        existing = await session.scalar(select(FlashcardModel.id).limit(1))
        if existing is None:
            session.add_all(
                [
                    FlashcardModel(
                        species="Rød glente",
                        media_type="audio",
                        prompt="Hvilken art har dette kald?",
                        answer="Rød glente",
                    ),
                    FlashcardModel(
                        species="Hvepsevåge",
                        media_type="image",
                        prompt="Hvilken art ses på silhuetten med lang hale og smalt hoved?",
                        answer="Hvepsevåge",
                    ),
                ]
            )
            await session.commit()

        if await session.scalar(select(CommunityNews.id).limit(1)) is None:
            session.add(
                CommunityNews(
                    title="Velkommen til fællesskabet",
                    body_markdown="Her finder du nyheder, arrangementer og dokumenter samlet ét sted. **Velkommen indenfor.**",
                    category="velkommen",
                    created_by="system",
                )
            )
        if await session.scalar(select(CommunityEvent.id).limit(1)) is None:
            session.add(
                CommunityEvent(
                    title="Åben fællesskabsaften",
                    description="Kom forbi til en uformel aften med kaffe, samtaler og nye idéer.\n\nAlle er velkomne.",
                    starts_at=datetime.now(timezone.utc) + timedelta(days=7),
                    location="Fælleshuset",
                    category="fællesskab",
                    max_participants=60,
                    created_by="system",
                )
            )
        if await session.scalar(select(CommunityContact.id).limit(1)) is None:
            session.add_all(
                [
                    CommunityContact(name="Maria Jensen", role="Forperson", email="maria@example.org", sort_order=1),
                    CommunityContact(name="Jonas Møller", role="Praktisk udvalg", email="jonas@example.org", sort_order=2),
                ]
            )
        if await session.scalar(select(CommunityDocument.id).limit(1)) is None:
            session.add(
                CommunityDocument(
                    title="Velkomstguide",
                    description="Praktisk information for nye beboere og medlemmer.",
                    category="guide",
                    file_url="/samtykke.html",
                    created_by="system",
                )
            )
        await session.commit()
    daily_sync_task = asyncio.create_task(run_daily_sync())
    try:
        yield
    finally:
        daily_sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await daily_sync_task


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(legacy_compat_router)
app.include_router(legacy_features_router)
app.include_router(legacy_extended_router)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
