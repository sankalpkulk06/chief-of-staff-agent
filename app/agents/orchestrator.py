"""Orchestrator agent — plans tasks and synthesizes results from sub-agents."""
import json
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.agents.prompts import load
from app.providers.factory import ChatProvider

# Valid agent names — used to filter/validate the parsed plan.
VALID_AGENTS = frozenset({"action_agent", "rag_agent", "research_agent", "conversational", "email_agent", "planner_agent"})

# Clear email nouns → a deterministic guard forces email_agent even if the LLM planner
# dropped it (routing is otherwise non-deterministic). Tight on purpose: no bare "mail".
_EMAIL_RE = re.compile(r"\b(e-?mails?|inbox|gmail)\b", re.IGNORECASE)

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
            result = self._parse_plan(response, question)
        except Exception:
            result = OrchestratorPlan(
                steps=[AgentStep(agent="conversational", task=question)],
                reasoning="planning failed — falling back to conversational",
            )
        # Deterministic safety net: a clear email request must reach email_agent.
        result.steps = self._ensure_email_agent(question, result.steps)
        return result

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

        steps: list[AgentStep] = []
        for i, s in enumerate(raw_steps, 1):
            agent = s.get("agent", "conversational")
            if agent not in VALID_AGENTS:
                agent = "conversational"
            task = s.get("task", fallback_question)
            step_id = str(s.get("id") or f"step_{i}")
            depends_on = s.get("depends_on") or []
            if isinstance(depends_on, str):
                depends_on = [depends_on]
            if not isinstance(depends_on, list):
                depends_on = []
            parallel_group = s.get("parallel_group")
            mode = s.get("mode") or OrchestratorAgent._infer_step_mode(agent, task)
            if mode not in {"read", "write", "synthesize"}:
                mode = OrchestratorAgent._infer_step_mode(agent, task)
            steps.append(
                AgentStep(
                    id=step_id,
                    agent=agent,
                    task=task,
                    depends_on=[str(dep) for dep in depends_on],
                    parallel_group=str(parallel_group) if parallel_group else None,
                    mode=mode,
                )
            )

        if not steps:
            steps = [
                AgentStep(
                    id="step_1",
                    agent="conversational",
                    task=fallback_question,
                    mode="synthesize",
                )
            ]

        steps = OrchestratorAgent._normalize_step_dependencies(steps)

        return OrchestratorPlan(steps=steps, reasoning=data.get("reasoning", ""))

    @staticmethod
    def _infer_step_mode(agent: str, task: str) -> str:
        if agent == "conversational":
            return "synthesize"
        task_lower = task.lower()
        write_markers = (
            "add_todo",
            "add_habit",
            "log_habit",
            "remember_fact",
            "create",
            "save",
            "log ",
            "remind me",
        )
        if agent == "action_agent" and any(marker in task_lower for marker in write_markers):
            return "write"
        return "read"

    @staticmethod
    def _normalize_step_dependencies(steps: list[AgentStep]) -> list[AgentStep]:
        seen_ids: set[str] = set()
        for i, step in enumerate(steps, 1):
            if not step.id or step.id in seen_ids:
                step.id = f"step_{i}"
            seen_ids.add(step.id)

        valid_ids = {step.id for step in steps}
        for step in steps:
            step.depends_on = [
                dep for dep in step.depends_on if dep in valid_ids and dep != step.id
            ]

        for i, step in enumerate(steps):
            if step.mode == "synthesize" and not step.depends_on:
                step.depends_on = [
                    prior.id
                    for prior in steps[:i]
                    if prior.mode != "synthesize"
                ]
        return steps

    @staticmethod
    def _ensure_email_agent(question: str, steps: list[AgentStep]) -> list[AgentStep]:
        """If the message is clearly about email but the plan omitted email_agent, inject it.

        Guards against LLM routing non-determinism (e.g. an email request answered by
        conversational). Only fires for explicit email nouns, so unrelated messages are
        untouched. email_agent still does its own instruction-aware summary of the request.
        """
        if not _EMAIL_RE.search(question or ""):
            return steps
        if any(step.agent == "email_agent" for step in steps):
            return steps

        kept = [step for step in steps if step.agent != "conversational"]
        kept.append(AgentStep(id="email_forced", agent="email_agent", task=question, mode="read"))
        kept.append(AgentStep(
            id="synth_forced",
            agent="conversational",
            task="Present the email results warmly and directly answer the user's request.",
            mode="synthesize",
        ))
        return OrchestratorAgent._normalize_step_dependencies(kept)
