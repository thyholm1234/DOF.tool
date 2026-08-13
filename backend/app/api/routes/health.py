from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from backend.app.api.schemas import HealthResponse
from backend.app.core.config import get_settings
from backend.app.db.session import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    postgres_state = "ok"
    redis_state = "ok"

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        postgres_state = "error"

    try:
        redis_client = Redis.from_url(get_settings().redis_dsn, decode_responses=True)
        await redis_client.ping()
        await redis_client.aclose()
    except Exception:
        redis_state = "error"

    status = "ok" if postgres_state == "ok" and redis_state == "ok" else "degraded"
    return HealthResponse(status=status, postgres=postgres_state, redis=redis_state)
