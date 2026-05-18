from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry
from app.core.analytics_service import AnalyticsService

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileOut(BaseModel):
    username: str
    joined: Optional[str]
    days_active: int
    total_sessions: int
    facts_personal: int
    facts_work: int
    longest_streak: int
    longest_streak_habit: str
    total_docs: int
    total_chunks: int


@router.get("", response_model=ProfileOut)
async def get_profile(
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> ProfileOut:
    user_id = current_user["user_id"]
    analytics = AnalyticsService(registry).get_analytics(user_id=user_id)

    fact_counts = analytics.fact_categories_count
    facts_personal = fact_counts.get("personal", 0)
    facts_work = fact_counts.get("work", 0)

    from app.core.habit_service import HabitService
    longest_streak = 0
    longest_habit = ""
    try:
        habit_service = HabitService(registry, user_id=user_id)
        for summary in habit_service.get_weekly_summary():
            if summary.streak > longest_streak:
                longest_streak = summary.streak
                longest_habit = summary.habit.name
    except Exception:
        pass

    sources = registry.list_all_sources(user_id=user_id)
    total_docs = len(sources)
    total_chunks = sum(len(registry.get_chunks_for_document(s["document_id"])) for s in sources)

    return ProfileOut(
        username=current_user["username"],
        joined=analytics.first_session,
        days_active=analytics.days_active,
        total_sessions=analytics.total_sessions,
        facts_personal=facts_personal,
        facts_work=facts_work,
        longest_streak=longest_streak,
        longest_streak_habit=longest_habit,
        total_docs=total_docs,
        total_chunks=total_chunks,
    )
