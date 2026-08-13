from fastapi import APIRouter

from backend.app.api.routes import (
    admin,
    auth,
    health,
    learning,
    observations,
    rankings,
    trends,
    trips,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(observations.router)
api_router.include_router(rankings.router)
api_router.include_router(trends.router)
api_router.include_router(trips.router)
api_router.include_router(learning.router)
api_router.include_router(admin.router)
