import os
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_chat_service, get_registry, require_auth
from app.config import get_settings
from app.core.analytics_service import AnalyticsService
from app.core.chat_service import ChatService
from app.storage.sqlite_registry import SQLiteRegistry

router = APIRouter(prefix="/profile", tags=["profile"], dependencies=[Depends(require_auth)])


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
    registry: SQLiteRegistry = Depends(get_registry),
    chat_service: ChatService = Depends(get_chat_service),
) -> ProfileOut:
    analytics = AnalyticsService(registry).get_analytics()

    fact_counts = analytics.fact_categories_count
    facts_personal = fact_counts.get("personal", 0)
    facts_work = fact_counts.get("work", 0)

    # Longest streak across all habits
    longest_streak = 0
    longest_habit = ""
    habit_service = chat_service.get_habit_service()
    if habit_service:
        for summary in habit_service.get_weekly_summary():
            if summary.streak > longest_streak:
                longest_streak = summary.streak
                longest_habit = summary.habit.name

    # Total docs and chunks
    row = registry._connection.execute(
        "SELECT COUNT(DISTINCT d.document_id) AS docs, COUNT(c.chunk_id) AS chunks "
        "FROM documents d LEFT JOIN chunks c ON c.document_id = d.document_id"
    ).fetchone()
    total_docs = row["docs"] if row else 0
    total_chunks = row["chunks"] if row else 0

    username = (
        get_settings().sage_username
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "local user"
    )

    return ProfileOut(
        username=username,
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
