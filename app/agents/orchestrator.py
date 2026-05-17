"""Orchestrator agent — plans tasks and synthesizes results from sub-agents."""
import json
import re
from typing import Any, List

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.providers.ollama_chat import OllamaChatProvider

# Descriptions shown to the orchestrator LLM so it knows what each agent can do.
AGENT_DESCRIPTIONS = {
    "rag_agent": (
        "Searches the user's personal saved documents and notes using semantic search. "
        "Use for questions about things the user has read, saved, or ingested."
    ),
    "research_agent": (
        "Fetches live news and searches the web for current or factual information. "
        "Use for questions about recent events, external facts, or anything not in the user's documents."
    ),
    "action_agent": (
        "Performs actions: creates todos/reminders, logs habit completions, adds new habits to track, "
        "and saves or recalls personal/work facts. Use when the user wants to DO something."
    ),
    "conversational": (
        "Handles general chat, greetings, simple direct questions, and follow-ups that don't need "
        "documents, web search, or any action."
    ),
}

_PLAN_SYSTEM = """\
You are the orchestrator of Sage, a personal AI assistant. \
Analyze the user's request and produce a step-by-step plan using specialized agents.

Available agents:
{agent_list}

Rules:
- Choose ONLY the agents actually needed for this request.
- For a simple greeting or direct question, one "conversational" step is enough.
- For compound requests (e.g. "search the web for X and remind me to follow up"), use multiple agents.
- Order steps so that information-gathering steps come before action steps.
- Each "task" field should be a clear, self-contained instruction for that agent.

Respond with ONLY valid JSON — no prose before or after:
{{
  "reasoning": "one sentence explaining your plan",
  "steps": [
    {{"agent": "agent_name", "task": "specific instruction for this agent"}}
  ]
}}"""

_SYNTHESIS_SYSTEM = """\
You are Sage, a wise and warm personal AI assistant. \
Multiple specialized agents have gathered information to answer the user's request. \
Combine their outputs into a single clear, natural response. \
Do not mention the agents or the internal process — just answer the user directly."""

# Patterns that are obviously conversational — skip planning overhead.
_FAST_PATH_STARTS = frozenset([
    "hi", "hello", "hey", "howdy", "sup", "yo",
    "thanks", "thank", "cheers", "cool", "great", "nice",
    "bye", "goodbye", "see", "ok", "okay", "sure", "got",
    "good", "perfect", "understood",
])


class OrchestratorAgent:
    """Plans the multi-agent execution and synthesizes the final reply."""

    def __init__(self, chat_provider: OllamaChatProvider, assistant_name: str = "Sage"):
        self._provider = chat_provider
        self._assistant_name = assistant_name

    def plan(self, question: str, history: List[dict[str, Any]]) -> OrchestratorPlan:
        """Decompose the user question into a sequence of agent steps."""
        if self._is_fast_path(question):
            return OrchestratorPlan(
                steps=[AgentStep(agent="conversational", task=question)],
                reasoning="simple conversational query — no planning needed",
            )

        agent_list = "\n".join(f"- {name}: {desc}" for name, desc in AGENT_DESCRIPTIONS.items())
        system = _PLAN_SYSTEM.format(agent_list=agent_list)

        # Give the planner recent history so it understands follow-ups.
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history[-4:])  # last 2 turns
        messages.append({"role": "user", "content": question})

        try:
            response = self._provider.chat(messages=messages)
            return self._parse_plan(response, question)
        except Exception:
            # Fallback: send to conversational rather than crash.
            return OrchestratorPlan(
                steps=[AgentStep(agent="conversational", task=question)],
                reasoning="planning failed — falling back to conversational",
            )

    def synthesize(
        self,
        original_question: str,
        results: List[AgentResult],
        history: List[dict[str, Any]],
    ) -> str:
        """Merge multiple agent outputs into a single coherent reply."""
        successful = [r for r in results if r.success and r.output]

        # Single result — no synthesis LLM call needed.
        if len(successful) == 1:
            return successful[0].output

        if not successful:
            failed_errors = "; ".join(r.error or "unknown error" for r in results if not r.success)
            return f"I ran into some trouble completing your request: {failed_errors}"

        context_parts = []
        for r in successful:
            context_parts.append(f"[{r.agent}]\n{r.output}")
        combined = "\n\n---\n\n".join(context_parts)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM},
        ]
        messages.extend(history[-4:])
        messages.append({
            "role": "user",
            "content": (
                f"My original request: {original_question}\n\n"
                f"Agent results:\n{combined}\n\n"
                "Please give me a unified response."
            ),
        })

        return self._provider.chat(messages=messages)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fast_path(question: str) -> bool:
        words = question.lower().strip().split()
        if not words:
            return True
        return words[0] in _FAST_PATH_STARTS and len(words) <= 4

    @staticmethod
    def _parse_plan(response: str, fallback_question: str) -> OrchestratorPlan:
        """Extract and validate the JSON plan from the LLM response."""
        # Strip markdown code fences if the model wraps JSON in ```
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()

        # Find the outermost {...}
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in orchestrator response")

        data = json.loads(match.group())
        raw_steps = data.get("steps", [])

        valid_agents = set(AGENT_DESCRIPTIONS.keys())
        steps = []
        for s in raw_steps:
            agent = s.get("agent", "conversational")
            if agent not in valid_agents:
                agent = "conversational"
            task = s.get("task", fallback_question)
            steps.append(AgentStep(agent=agent, task=task))

        if not steps:
            steps = [AgentStep(agent="conversational", task=fallback_question)]

        return OrchestratorPlan(steps=steps, reasoning=data.get("reasoning", ""))
