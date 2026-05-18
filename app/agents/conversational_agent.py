"""Conversational agent — general chat with memory of facts."""
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.core.fact_service import FactService
from app.providers.ollama_chat import OllamaChatProvider

_SYSTEM_BASE = """\
You are {assistant_name} — a wise, warm, and knowledgeable personal AI companion. \
Be thoughtful and direct. No filler. Short answers unless depth is needed. \
Remember the conversation — you have full history."""


class ConversationalAgent:
    """Handles general chat without needing tools."""

    def __init__(
        self,
        chat_provider: OllamaChatProvider,
        assistant_name: str = "Sage",
        fact_service: Optional[FactService] = None,
    ):
        self._provider = chat_provider
        self._assistant_name = assistant_name
        self._fact_service = fact_service

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
            system = _SYSTEM_BASE.format(assistant_name=self._assistant_name)

            # Inject known facts about the user.
            if self._fact_service:
                personal = self._fact_service.get_relevant_facts("personal", limit=5)
                work = self._fact_service.get_relevant_facts("work", limit=5)
                all_facts = personal + work
                if all_facts:
                    facts_block = "\n".join(f"- {f.content}" for f in all_facts)
                    system += f"\n\nAbout the user:\n{facts_block}"

            if response_style:
                system += f"\n\nResponse style:\n{response_style}"

            # If previous agents already gathered context, include it.
            if previous_results:
                prior = "\n\n".join(r.output for r in previous_results if r.success and r.output)
                if prior:
                    system += f"\n\nContext from other agents:\n{prior}"

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
