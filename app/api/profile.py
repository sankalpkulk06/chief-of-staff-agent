from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user, get_registry
from app.config import get_settings
from app.core.analytics_service import AnalyticsService
from app.storage.factory import create_vector_store

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


class DeleteProfileRequest(BaseModel):
    username: str
    password: str


class DeleteProfileOut(BaseModel):
    ok: bool
    deleted: Dict[str, int]


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


@router.delete("", response_model=DeleteProfileOut)
async def delete_profile(
    body: DeleteProfileRequest,
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> DeleteProfileOut:
    username = body.username.strip()
    if username != current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username does not match the signed-in profile",
        )

    verified = registry.verify_password(username, body.password)
    if verified is None or verified["user_id"] != current_user["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    user_id = current_user["user_id"]
    document_ids = [row["document_id"] for row in registry.list_all_sources(user_id=user_id)]

    settings = get_settings()
    paths = settings.resolve_paths()
    vector_store = create_vector_store(
        settings.database_url,
        paths.chroma_dir,
        settings.embedding_dimension,
    )
    try:
        delete_vectors = getattr(vector_store, "delete_user_records", None)
        if callable(delete_vectors):
            delete_vectors(user_id=user_id, document_ids=document_ids)
    finally:
        close = getattr(vector_store, "close", None)
        if callable(close):
            close()

    deleted = registry.delete_user_data(user_id=user_id)
    return DeleteProfileOut(ok=True, deleted=deleted)
