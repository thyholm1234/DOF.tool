from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import Flashcard, FlashcardCreate
from backend.app.db.models import FlashcardModel
from backend.app.db.session import get_db

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/flashcards", response_model=list[Flashcard])
async def list_flashcards(db: AsyncSession = Depends(get_db)) -> list[Flashcard]:
    result = await db.execute(select(FlashcardModel).order_by(FlashcardModel.created_at.desc()))
    cards = result.scalars().all()
    return [
        Flashcard(
            id=card.id,
            species=card.species,
            media_type=card.media_type,
            prompt=card.prompt,
            answer=card.answer,
            media_url=card.media_url,
        )
        for card in cards
    ]


@router.post("/flashcards", response_model=Flashcard)
async def create_flashcard(
    payload: FlashcardCreate, db: AsyncSession = Depends(get_db)
) -> Flashcard:
    card = FlashcardModel(**payload.model_dump())
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return Flashcard(
        id=card.id,
        species=card.species,
        media_type=card.media_type,
        prompt=card.prompt,
        answer=card.answer,
        media_url=card.media_url,
    )


@router.get("/flashcards/quiz")
async def quiz_pool(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(FlashcardModel).order_by(func.random()).limit(limit))
    cards = result.scalars().all()
    return {
        "items": [
            {
                "id": card.id,
                "species": card.species,
                "media_type": card.media_type,
                "prompt": card.prompt,
                "media_url": card.media_url,
            }
            for card in cards
        ]
    }

