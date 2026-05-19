"""Orchestrator agent — plans tasks and synthesizes results from sub-agents."""
import json
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.agents.prompts import load
from app.providers.factory import ChatProvider

# Valid agent names — used to filter/validate the parsed plan.
VALID_AGENTS = frozenset({"action_agent", "rag_agent", "research_agent", "conversational", "email_agent"})

_PLAN_SYSTEM = load("orchestrator_plan")
_SYNTHESIS_SYSTEM = load("orchestrator_synthesis")


def _friendly_failure(errors: str) -> str:
    detail = errors.lower()
    if "429" in detail or "rate_limit" in detail or "quota" in detail or "tokens per day" in detail:
        return (
            "The chat model is rate-limited right now. This happened during answer generation, "
            "not during document chunking or embedding. Try again in a few minutes or switch models."
        )
    return "I ran into some trouble completing your request. Try again in a moment."


class OrchestratorAgent:
    """Plans the multi-agent execution and synthesizes the final reply."""

    def __init__(self, chat_provider: ChatProvider, assistant_name: str = "Sage"):
        self._provider = chat_provider
        self._assistant_name = assistant_name

    def plan(self, question: str, history: List[dict[str, Any]]) -> OrchestratorPlan:
        """Decompose the user question into a sequence of agent steps."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": _PLAN_SYSTEM}]
        messages.extend(history[-4:])
        messages.append({"role": "user", "content": question})

        try:
            response = self._provider.chat(messages=messages)
            return self._parse_plan(response, question)
        except Exception:
            return OrchestratorPlan(
                steps=[AgentStep(agent="conversational", task=question)],
                reasoning="planning failed — falling back to conversational",
            )

    def synthesize(
        self,
        original_question: str,
        results: List[AgentResult],
        history: List[dict[str, Any]],
        user_facts: Optional[str] = None,
    ) -> str:
        """Merge multiple agent outputs into a single coherent reply."""
        successful = [r for r in results if r.success and r.output]

        if not successful:
            failed_errors = "; ".join(r.error or "unknown error" for r in results if not r.success)
            return _friendly_failure(failed_errors)

        if len(results) == 1 and len(successful) == 1:
            return successful[0].output

        agent_results = "\n\n---\n\n".join(
            f"[{r.agent} — {r.task}]\n{r.output}" for r in successful
        )
        system = (
            _SYNTHESIS_SYSTEM
            .replace("{agent_results}", agent_results)
            .replace("{user_facts}", user_facts or "No personal facts stored yet.")
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history[-4:])
        messages.append({"role": "user", "content": original_question})

        return self._provider.chat(messages=messages)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_plan(response: str, fallback_question: str) -> OrchestratorPlan:
        """Extract and validate the JSON plan from the LLM response."""
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in orchestrator response")

        data = json.loads(match.group())
        raw_steps = data.get("steps", [])

        steps = []
        for s in raw_steps:
            agent = s.get("agent", "conversational")
            if agent not in VALID_AGENTS:
                agent = "conversational"
            task = s.get("task", fallback_question)
            steps.append(AgentStep(agent=agent, task=task))

        if not steps:
            steps = [AgentStep(agent="conversational", task=fallback_question)]

        return OrchestratorPlan(steps=steps, reasoning=data.get("reasoning", ""))
