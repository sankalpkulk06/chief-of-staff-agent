from fastapi import APIRouter

from app.api import analytics, auth, facts, habits, profile, sessions, sources

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(sessions.router)
api_router.include_router(facts.router)
api_router.include_router(habits.router)
api_router.include_router(sources.router)
api_router.include_router(analytics.router)
api_router.include_router(profile.router)
