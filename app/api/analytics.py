"""Analytics API — a single windowed dashboard payload + a lazy LLM insights digest.

Both delegate to AnalyticsService so the web UI, /profile, and the CLI share one
metrics engine. Compute-on-read: aggregates are derived live from raw events; nothing
aggregated is persisted.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user, get_registry
from app.core.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
def get_analytics(
    window: int = Query(30, ge=1, le=365, description="Rolling window in days (e.g. 7 / 30 / 90)."),
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Full analytics dashboard for the current user over the given window."""
    return AnalyticsService(registry).get_dashboard(current_user["user_id"], window_days=window)


@router.get("/insights")
def get_insights(
    window: int = Query(30, ge=1, le=365),
    registry: Any = Depends(get_registry),
    current_user: Dict = Depends(get_current_user),
) -> Dict[str, str]:
    """Plain-English narrative over the computed stats (LLM; cached with a short TTL)."""
    text = AnalyticsService(registry).insights(current_user["user_id"], window_days=window)
    return {"insights": text}
