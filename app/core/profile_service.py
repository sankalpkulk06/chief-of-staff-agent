"""Shared profile logic used by both the web API (app/api/profile.py) and the CLI
(`sage profile`). Keeps the account summary + deletion in one place so the two never drift.
"""
from typing import Any, Dict

from app.config import get_settings
from app.core.analytics_service import AnalyticsService
from app.storage.factory import create_vector_store


def build_profile(registry: Any, user_id: str, username: str) -> Dict[str, Any]:
    """Return the account summary fields (matches ProfileOut)."""
    analytics = AnalyticsService(registry).get_analytics(user_id=user_id)
    fact_counts = analytics.fact_categories_count

    longest_streak = 0
    longest_habit = ""
    try:
        from app.core.habit_service import HabitService
        for summary in HabitService(registry, user_id=user_id).get_weekly_summary():
            if summary.streak > longest_streak:
                longest_streak = summary.streak
                longest_habit = summary.habit.name
    except Exception:
        pass

    sources = registry.list_all_sources(user_id=user_id)
    total_docs = len(sources)
    total_chunks = sum(len(registry.get_chunks_for_document(s["document_id"])) for s in sources)

    return {
        "username": username,
        "joined": analytics.first_session,
        "days_active": analytics.days_active,
        "total_sessions": analytics.total_sessions,
        "facts_personal": fact_counts.get("personal", 0),
        "facts_work": fact_counts.get("work", 0),
        "longest_streak": longest_streak,
        "longest_streak_habit": longest_habit,
        "total_docs": total_docs,
        "total_chunks": total_chunks,
    }


def delete_profile_and_data(registry: Any, user_id: str) -> Dict[str, int]:
    """Irreversibly delete the user's vector records and all owned rows (incl. the account).

    Returns per-table deletion counts. Callers are responsible for verifying identity first.
    """
    document_ids = [row["document_id"] for row in registry.list_all_sources(user_id=user_id)]

    settings = get_settings()
    paths = settings.resolve_paths()
    vector_store = create_vector_store(
        settings.database_url, paths.chroma_dir, settings.embedding_dimension,
    )
    try:
        delete_vectors = getattr(vector_store, "delete_user_records", None)
        if callable(delete_vectors):
            delete_vectors(user_id=user_id, document_ids=document_ids)
    finally:
        close = getattr(vector_store, "close", None)
        if callable(close):
            close()

    return registry.delete_user_data(user_id=user_id)
