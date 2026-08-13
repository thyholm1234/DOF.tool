from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from backend.app.api.router import api_router
from backend.app.api.routes.legacy_compat import router as legacy_compat_router
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models import FlashcardModel
from backend.app.db.session import SessionLocal, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

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
    yield


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
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
