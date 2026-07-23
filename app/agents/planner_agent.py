"""PlannerAgent — routes natural-language calendar-planning requests to PlannerService.

The orchestrator sends requests like "plan my day tomorrow", "move gym to 7am",
"remove the pickup tickets from my calendar" here. Because the request already carries
the change, this is a single-shot: build_plan gathers current state, the LLM produces the
revised schedule, the diff engine yields create/patch/soft_cancel ops, and the whole batch
is staged behind the HITL gate (same approval flow as everything else).
"""
import logging
from typing import Any, List, Optional

from app.agents.base import AgentResult

log = logging.getLogger(__name__)


class PlannerAgent:
    def __init__(self, planner_service: Any, assistant_name: str = "Sage") -> None:
        self._planner = planner_service
        self._assistant_name = assistant_name

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict],
        previous_results: Optional[List[AgentResult]] = None,
        user_id: Optional[str] = None,
        response_style: Optional[str] = None,
    ) -> AgentResult:
        if not user_id:
            return AgentResult(
                agent="planner_agent", task=task,
                output="I need to know who you are to plan your calendar.",
                success=False, error="no_user_id",
            )
        if not self._planner or not self._planner.available:
            return AgentResult(
                agent="planner_agent", task=task,
                output="The daily planner isn't available (Google Calendar isn't configured).",
                success=False, error="unavailable",
            )

        text = original_question or task
        try:
            plan_date = self._planner.resolve_plan_date_from_text(text, user_id)
            result = self._planner.build_plan(
                user_id=user_id, session_id="", plan_date=plan_date, notes=[text],
            )
        except Exception as exc:
            log.warning("PlannerAgent: build_plan failed: %s", exc)
            return AgentResult(
                agent="planner_agent", task=task,
                output="I couldn't update your calendar plan just now. Please try again.",
                success=False, error=str(exc),
            )

        metadata = {}
        if result.hitl_pending and result.hitl_id:
            metadata = {"hitl_pending": True, "hitl_id": result.hitl_id}
        return AgentResult(
            agent="planner_agent", task=task,
            output=result.message, success=True, metadata=metadata,
        )
