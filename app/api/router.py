from fastapi import APIRouter

from app.api import analytics, auth, email, facts, habits, hitl, profile, sessions, sources, todos

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(sessions.router)
api_router.include_router(facts.router)
api_router.include_router(habits.router)
api_router.include_router(sources.router)
api_router.include_router(analytics.router)
api_router.include_router(profile.router)
api_router.include_router(hitl.router)
api_router.include_router(email.router)
api_router.include_router(todos.router)
