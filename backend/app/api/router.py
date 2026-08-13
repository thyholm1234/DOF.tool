from fastapi import APIRouter, Depends

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
from backend.app.core.security import require_user

authenticated = [Depends(require_user)]

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(observations.router, dependencies=authenticated)
api_router.include_router(rankings.router, dependencies=authenticated)
api_router.include_router(trends.router, dependencies=authenticated)
api_router.include_router(trips.router, dependencies=authenticated)
api_router.include_router(learning.router, dependencies=authenticated)
api_router.include_router(admin.router, dependencies=authenticated)
