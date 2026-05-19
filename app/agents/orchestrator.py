"""Orchestrator agent — plans tasks and synthesizes results from sub-agents."""
import json
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.agents.prompts import load
from app.providers.factory import ChatProvider

# Valid agent names — used to filter/validate the parsed plan.
VALID_AGENTS = frozenset({"action_agent", "rag_agent", "research_agent", "conversational"})

_PLAN_SYSTEM = load("orchestrator_plan")
_SYNTHESIS_SYSTEM = load("orchestrator_synthesis")

# Patterns that are obviously pure conversational — skip planning overhead.
_FAST_PATH_STARTS = frozenset([
    "hi", "hello", "hey", "howdy", "sup", "yo",
    "thanks", "thank", "cheers", "cool", "great", "nice",
    "bye", "goodbye", "see", "ok", "okay", "sure", "got",
    "good", "perfect", "understood",
])


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
        if self._is_fast_path(question):
            return OrchestratorPlan(
                steps=[AgentStep(agent="conversational", task=question)],
                reasoning="simple conversational query — no planning needed",
            )

        # Rule-based pre-decomposition for compound research requests.
        # Small models reliably miss these — detect and split before LLM planning.
        rule_plan = self._rule_based_plan(question, history=history)
        if rule_plan:
            return rule_plan

        # Give the planner recent history so it understands follow-ups.
        messages: list[dict[str, Any]] = [{"role": "system", "content": _PLAN_SYSTEM}]
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
        user_facts: Optional[str] = None,
    ) -> str:
        """Merge multiple agent outputs into a single coherent reply."""
        successful = [r for r in results if r.success and r.output]

        if not successful:
            failed_errors = "; ".join(r.error or "unknown error" for r in results if not r.success)
            return _friendly_failure(failed_errors)

        # Only skip synthesis when there was genuinely a single planned step.
        # If multiple steps were planned but only one succeeded, we still want
        # synthesis so the LLM can acknowledge what it found vs what it couldn't.
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
    def _extract_recent_upload(history: List[dict[str, Any]]) -> Optional[str]:
        """Return the filename of the most recently uploaded document, if any."""
        import re as _re
        for turn in reversed(history[-10:]):
            if turn.get("role") == "user":
                m = _re.search(r"Uploaded document:\s*(\S+)", turn.get("content", ""))
                if m:
                    return m.group(1)
        return None

    @staticmethod
    def _rule_based_plan(question: str, history: Optional[List[dict[str, Any]]] = None) -> Optional["OrchestratorPlan"]:
        """
        Detect compound requests that small models reliably fail to split.
        Returns a plan if a rule fires, None to fall through to LLM planning.

        Handles patterns like:
          "search for X and tell me the news about Y"
          "what is X and what's the news on Y"
          "find info on X and also check news about Y"
        """
        q = question.lower()

        # Uploaded/saved document follow-ups should go straight to RAG.
        # This avoids routing "the document I just uploaded" to general chat,
        # where the model cannot search the indexed chunks.
        _doc_refs = {
            "document",
            "doc",
            "uploaded",
            "upload",
            "file",
            "notes.txt",
            ".md",
            ".pdf",
            ".txt",
            "pdf",
            "readme",
            "saved docs",
            "knowledge base",
            "you indexed",
            "i shared",
            "i sent",
        }
        _doc_actions = {
            "summarize",
            "summary",
            "title",
            "explain",
            "tell me about",
            "what is this about",
            "what is in",
            "what's in",
            "what should i do",
            "key points",
            "main points",
            "takeaways",
            "based on",
            "according to",
            "what does it say",
            "what does it cover",
            "find",
            "search",
            "overview",
            "highlights",
        }
        if any(ref in q for ref in _doc_refs) and any(action in q for action in _doc_actions):
            task = question
            # If the question is implicitly about a recently-uploaded file (no explicit
            # filename mentioned), surface the filename from session history so the
            # retriever can target it.
            has_explicit_filename = any(ext in q for ext in (".md", ".pdf", ".txt", ".docx"))
            if not has_explicit_filename and history:
                recent_file = OrchestratorAgent._extract_recent_upload(history)
                if recent_file and recent_file.lower() not in q:
                    task = f"{question} (referring to the uploaded file: {recent_file})"
            return OrchestratorPlan(
                steps=[
                    AgentStep(
                        agent="rag_agent",
                        task=f"search_documents: {task}",
                    )
                ],
                reasoning="uploaded/saved document question — search indexed user documents",
            )

        # Detect: web-search intent + news intent in the same message
        _search_words = {"search", "look up", "find", "what is", "explain", "tell me about", "google"}
        _news_words = {"news", "latest", "recent", "headlines", "what's happening"}
        _connectors = {" and ", " also ", " plus ", " as well as ", " then "}

        has_search = any(w in q for w in _search_words)
        has_news = any(w in q for w in _news_words)
        has_connector = any(c in q for c in _connectors)

        if has_search and has_news and has_connector:
            # Split at the connector to extract the two sub-tasks
            import re
            parts = re.split(r"\band\b|\balso\b|\bplus\b|\bas well as\b|\bthen\b", question, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                first, second = parts[0].strip(), parts[1].strip()
                # Assign each part to the right agent
                first_is_news = any(w in first.lower() for w in _news_words)
                second_is_news = any(w in second.lower() for w in _news_words)
                steps = [
                    AgentStep(
                        agent="research_agent",
                        task=f"news: {first}" if first_is_news else f"web search: {first}",
                    ),
                    AgentStep(
                        agent="research_agent",
                        task=f"news: {second}" if second_is_news else f"web search: {second}",
                    ),
                ]
                return OrchestratorPlan(
                    steps=steps,
                    reasoning="compound research request — split into web search + news steps",
                )

        return None

    @staticmethod
    def _is_fast_path(question: str) -> bool:
        words = question.lower().strip().split()
        if not words:
            return True
        # Only short pure-greeting messages skip planning
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

        valid_agents = VALID_AGENTS
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
