"""Conversational agent — general chat with memory of facts."""
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.agents.prompts import load
from app.config.settings import get_settings
from app.core import timezone_util as tzu
from app.core.fact_service import FactService
from app.providers.ollama_chat import OllamaChatProvider
from app.storage.sqlite_registry import SQLiteRegistry

_SYSTEM_BASE = load("conversational")


class ConversationalAgent:
    """Handles general chat without needing tools."""

    def __init__(
        self,
        chat_provider: OllamaChatProvider,
        assistant_name: str = "Sage",
        fact_service: Optional[FactService] = None,
        registry: Optional[SQLiteRegistry] = None,
    ):
        self._provider = chat_provider
        self._assistant_name = assistant_name
        self._fact_service = fact_service
        self._registry = registry

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict[str, Any]],
        previous_results: Optional[List[AgentResult]] = None,
        response_style: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> AgentResult:
        try:
            # Build user facts — use per-request FactService scoped to the actual user.
            user_facts = "No personal facts stored yet."
            fact_svc: Optional[FactService] = None
            if self._registry and user_id:
                fact_svc = FactService(self._registry, user_id=user_id)
            elif self._fact_service:
                fact_svc = self._fact_service
            if fact_svc:
                personal = fact_svc.get_relevant_facts("personal", limit=5)
                work = fact_svc.get_relevant_facts("work", limit=5)
                all_facts = personal + work
                if all_facts:
                    user_facts = "\n".join(f"- {f.content} ({f.category})" for f in all_facts)

            # Build agent results block
            agent_results = ""
            if previous_results:
                parts = [r.output for r in previous_results if r.success and r.output]
                if parts:
                    agent_results = "\n\n---\n\n".join(parts)
            if not agent_results:
                agent_results = "None — you are the only agent responding."

            current_datetime = tzu.describe_now(
                self._registry, user_id or "", default=get_settings().default_timezone
            )

            system = (
                _SYSTEM_BASE
                .replace("{assistant_name}", self._assistant_name)
                .replace("{current_datetime}", current_datetime)
                .replace("{user_facts}", user_facts)
                .replace("{agent_results}", agent_results)
            )

            if response_style:
                system += f"\n\nResponse style: {response_style}"

            # History stays in the messages array for proper turn-based context.
            messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
            messages.extend(history)
            messages.append({"role": "user", "content": task})

            answer = self._provider.chat(messages=messages)
            return AgentResult(agent="conversational", task=task, output=answer, success=True)

        except Exception as exc:
            return AgentResult(
                agent="conversational",
                task=task,
                output="",
                success=False,
                error=f"Conversational agent failed: {exc}",
            )
