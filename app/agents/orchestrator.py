"""Orchestrator agent — plans tasks and synthesizes results from sub-agents."""
import json
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.providers.factory import ChatProvider

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
        "Performs actions and retrieves personal data: creates todos/reminders, logs habit completions, "
        "adds new habits to track, lists the user's current habits and streaks, "
        "and saves or recalls personal/work facts. "
        "ALWAYS use this when the user asks about their own habits, goals, or saved facts. "
        "ALWAYS use this when the user states personal information (name, job, location, preferences, "
        "age, etc.) — save it as a fact so Sage remembers it. "
        "Use when the user wants to DO something, retrieve their personal data, or share something about themselves."
    ),
    "conversational": (
        "Handles general chat, greetings, acknowledgements, and follow-ups that don't need "
        "documents, web search, or any action. Use AFTER action_agent when confirming what was saved."
    ),
}

_PLAN_SYSTEM = """\
You are the orchestrator of Sage, a personal AI assistant. \
Analyze the user's request and produce a step-by-step plan using specialized agents.

Available agents:
{agent_list}

Rules:
- Choose ONLY the agents actually needed. Prefer fewer steps over more.
- For greetings, thanks, or acknowledgements: one "conversational" step only.
- For pure factual questions with no personal context needed: one "conversational" step only.
- If the user shares ANY personal information (name, job, location, age, preferences, health, \
habits, relationships): ALWAYS use action_agent first to save it as a fact, then conversational.
- If the user wants to log a habit completion ("I ran today", "did meditation", "went to gym"): \
use action_agent to log it, then conversational to acknowledge.
- If the user asks about their own saved notes, documents, or past information: use rag_agent.
- If the user asks about current events, news, or external facts: use research_agent.
- For compound requests, split into the minimum necessary steps and order them: \
facts/actions first → research second → conversational last.
- Each "task" field must be a self-contained instruction that makes sense without context.

Examples:
- "hi" → [conversational: greet the user]
- "my name is John" → [action_agent: save fact user's name is John | conversational: greet John by name]
- "I'm a software engineer at Google" → [action_agent: save facts job=software engineer company=Google | conversational: acknowledge warmly]
- "I live in Mumbai and I'm 28" → [action_agent: save facts location=Mumbai age=28 | conversational: acknowledge]
- "I ran 5km today" → [action_agent: log habit completion running 5km | conversational: congratulate]
- "I've been meditating every day this week" → [action_agent: log habit meditation completed | conversational: acknowledge streak]
- "remind me to call mom at 5pm" → [action_agent: create todo call mom at 5pm | conversational: confirm the reminder]
- "add reading to my habits" → [action_agent: create new habit reading | conversational: confirm habit added]
- "what are my habits?" → [action_agent: recall habits list | conversational: present the list]
- "what did I save about React?" → [rag_agent: search documents about React | conversational: summarize findings]
- "catch me up on my goals" → [rag_agent: search documents about goals and objectives | conversational: summarize]
- "what's happening in AI?" → [research_agent: search latest AI news and developments | conversational: summarize]
- "what is the capital of France?" → [conversational: answer directly]
- "search for LangGraph tutorials and remind me to try it" → [research_agent: search LangGraph tutorials | action_agent: create todo try LangGraph | conversational: confirm]
- "what are my habits and what's the news in fitness?" → [action_agent: recall habits list | research_agent: search fitness news | conversational: present both]
- "catch me up on my goals and what's happening in AI" → [rag_agent: search saved goals | research_agent: search latest AI news | conversational: summarize both]
- "I prefer working at night" → [action_agent: save fact prefers working at night | conversational: acknowledge]
- "I drink coffee every morning" → [action_agent: save fact drinks coffee every morning | conversational: acknowledge]

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

# Patterns that are obviously pure conversational — skip planning overhead.
_FAST_PATH_STARTS = frozenset([
    "hi", "hello", "hey", "howdy", "sup", "yo",
    "thanks", "thank", "cheers", "cool", "great", "nice",
    "bye", "goodbye", "see", "ok", "okay", "sure", "got",
    "good", "perfect", "understood",
])


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
        rule_plan = self._rule_based_plan(question)
        if rule_plan:
            return rule_plan

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

        if not successful:
            failed_errors = "; ".join(r.error or "unknown error" for r in results if not r.success)
            return f"I ran into some trouble completing your request: {failed_errors}"

        # Only skip synthesis when there was genuinely a single planned step.
        # If multiple steps were planned but only one succeeded, we still want
        # synthesis so the LLM can acknowledge what it found vs what it couldn't.
        if len(results) == 1 and len(successful) == 1:
            return successful[0].output

        context_parts = []
        for r in successful:
            context_parts.append(f"[{r.agent} — task: {r.task}]\n{r.output}")
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
    def _rule_based_plan(question: str) -> Optional["OrchestratorPlan"]:
        """
        Detect compound requests that small models reliably fail to split.
        Returns a plan if a rule fires, None to fall through to LLM planning.

        Handles patterns like:
          "search for X and tell me the news about Y"
          "what is X and what's the news on Y"
          "find info on X and also check news about Y"
        """
        q = question.lower()

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
