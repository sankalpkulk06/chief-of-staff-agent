"""Dispatch an approved HITL request to the right executor by action_type.

Both resolve paths (app/api/hitl.py for web, app/webhook/server.py for WhatsApp) call
this instead of hard-coding ActionAgent, so calendar-plan batches execute correctly on
either surface. Self-contained: it builds whatever provider/service each action needs.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from app.agents.base import AgentResult

log = logging.getLogger(__name__)

CALENDAR_ACTIONS = {"apply_calendar_plan"}


def execute_approved_by_type(
    row: Dict[str, Any],
    user_id: str,
    *,
    registry: Any,
    schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> AgentResult:
    """Execute an approved HITL row. Caller is responsible for marking it resolved."""
    action_type = row.get("action_type")

    if action_type in CALENDAR_ACTIONS:
        return _execute_calendar_plan(row, user_id, registry)

    # Default: the existing todo/habit/fact write actions.
    return _execute_action_agent(row, user_id, registry, schedule_todo_callback)


def _execute_action_agent(row, user_id, registry, schedule_todo_callback) -> AgentResult:
    from app.agents.action_agent import ActionAgent
    from app.config import get_settings
    from app.providers.factory import agent_model_specs, create_chat_provider

    settings = get_settings()
    specs = agent_model_specs(settings)
    chat_provider = create_chat_provider(settings, specs["action_agent"])
    agent = ActionAgent(
        chat_provider=chat_provider,
        registry=registry,
        schedule_todo_callback=schedule_todo_callback,
    )
    return agent.execute_approved(row["id"], user_id)


def _execute_calendar_plan(row, user_id, registry) -> AgentResult:
    from app.agents.calendar_plan_executor import CalendarPlanExecutor
    from app.config import get_settings
    from app.config.settings import get_google_client_secrets
    from app.services.calendar_service import CalendarService

    settings = get_settings()
    secrets = get_google_client_secrets(settings)
    if not secrets:
        return AgentResult(
            agent="calendar_plan_executor", task="apply_calendar_plan",
            output="Google OAuth isn't configured on this server, so I can't write to your calendar.",
            success=False, error="oauth_not_configured",
        )
    calendar_service = CalendarService(client_secrets=secrets)
    return CalendarPlanExecutor(calendar_service, registry).apply(row["id"], user_id)
