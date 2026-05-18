"""AgentRunner — executes the orchestrator's plan and collects results."""
import time
from typing import Any, Callable, Dict, List, Optional

from app.agents.action_agent import ActionAgent
from app.agents.base import AgentResult, OrchestratorPlan
from app.agents.conversational_agent import ConversationalAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.rag_agent import RAGAgent
from app.agents.research_agent import ResearchAgent
from app.agents.security_agent import SecurityAgent
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever
from app.services.news_service import NewsService
from app.services.web_search_service import WebSearchService
from app.storage.sqlite_registry import SQLiteRegistry


class RunResult:
    """Return value from AgentRunner.run()."""

    def __init__(
        self,
        output: str,
        plan: OrchestratorPlan,
        agent_results: List[AgentResult],
        latency_ms: int,
        security_flags: Optional[list] = None,
    ):
        self.output = output
        self.plan = plan
        self.agent_results = agent_results
        self.latency_ms = latency_ms
        self.security_flags: list = security_flags or []

    @property
    def citations(self) -> list:
        seen: set = set()
        out = []
        for r in self.agent_results:
            for c in r.citations:
                key = c.get("url") or c.get("source") or str(c)
                if key not in seen:
                    seen.add(key)
                    out.append(c)
        return out

    @property
    def steps_summary(self) -> List[dict]:
        return [
            {
                "agent": r.agent,
                "task": r.task,
                "success": r.success,
                "error": r.error,
            }
            for r in self.agent_results
        ]


class AgentRunner:
    """
    Coordinates the full multi-agent pipeline:
      1. Orchestrator plans which agents to call and in what order.
      2. Each specialized agent executes its step.
      3. If multiple steps ran, the orchestrator synthesizes a unified reply.
    """

    def __init__(
        self,
        chat_provider: OllamaChatProvider,
        agent_chat_providers: Optional[Dict[str, Any]] = None,
        agent_model_specs: Optional[Dict[str, str]] = None,
        retriever: Optional[Retriever] = None,
        registry: Optional[SQLiteRegistry] = None,
        fact_service: Optional[FactService] = None,
        news_service: Optional[NewsService] = None,
        web_search_service: Optional[WebSearchService] = None,
        habit_service: Optional[HabitService] = None,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        assistant_name: str = "Sage",
        rag_top_k: int = 5,
        rag_fallback_threshold: float = 0.5,
        security_agent: Optional[SecurityAgent] = None,
    ):
        self._default_chat_provider = chat_provider
        self._agent_chat_providers = agent_chat_providers or {}
        self._agent_model_specs = agent_model_specs or {}
        self._retriever = retriever
        self._registry = registry
        self._fact_service = fact_service
        self._news_service = news_service
        self._web_search_service = web_search_service
        self._habit_service = habit_service
        self._schedule_todo_callback = schedule_todo_callback
        self._assistant_name = assistant_name
        self._rag_top_k = rag_top_k
        self._rag_fallback_threshold = rag_fallback_threshold
        self._security_agent = security_agent

        self._rebuild_agents()

    def _provider_for(self, agent_name: str):
        return self._agent_chat_providers.get(agent_name, self._default_chat_provider)

    def _rebuild_agents(self) -> None:
        self._orchestrator = OrchestratorAgent(
            self._provider_for("orchestrator"),
            assistant_name=self._assistant_name,
        )

        self._rag = (
            RAGAgent(self._retriever, self._provider_for("rag_agent"), top_k=self._rag_top_k)
            if self._retriever
            else None
        )
        self._research = ResearchAgent(
            self._provider_for("research_agent"),
            self._web_search_service,
            self._news_service,
        )
        self._action = ActionAgent(
            self._provider_for("action_agent"),
            registry=self._registry,
            fact_service=self._fact_service,
            habit_service=self._habit_service,
            schedule_todo_callback=self._schedule_todo_callback,
        )
        self._conversational = ConversationalAgent(
            self._provider_for("conversational"),
            self._assistant_name,
            self._fact_service,
        )

    def set_agent_provider(self, agent_name: str, provider: Any, model_spec: str) -> None:
        self._agent_chat_providers[agent_name] = provider
        self._agent_model_specs[agent_name] = model_spec
        self._rebuild_agents()

    def get_agent_model_specs(self) -> Dict[str, str]:
        return dict(self._agent_model_specs)

    # Valid agent names the orchestrator is allowed to route to.
    # Prevents the orchestrator from planning a step that re-enters itself.
    _VALID_AGENTS: frozenset = frozenset(
        {"rag_agent", "research_agent", "action_agent", "conversational"}
    )
    _MAX_STEPS: int = 5          # hard cap on plan steps per turn
    _MAX_HISTORY: int = 20       # max history turns passed to LLM context

    def run(
        self,
        question: str,
        history: List[dict[str, Any]],
        response_style: Optional[str] = None,
        top_k: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> RunResult:
        t0 = time.monotonic()

        # History truncation — prevent context window overflow on long sessions
        if len(history) > self._MAX_HISTORY:
            history = history[-self._MAX_HISTORY:]

        # Security: input check — runs before orchestrator so injections never reach LLM
        if self._security_agent is not None:
            sec = self._security_agent.check_input(question, user_id=user_id or "default")
            if sec.blocked:
                latency_ms = int((time.monotonic() - t0) * 1000)
                if sec.reason == "length_exceeded":
                    rejection = "Your message was blocked because it exceeded the maximum length."
                elif sec.reason == "rate_limit_exceeded":
                    rejection = "You're sending messages too quickly. Please wait a moment."
                else:
                    rejection = "Your message was blocked due to a security policy violation."
                return RunResult(
                    output=rejection,
                    plan=OrchestratorPlan(steps=[]),
                    agent_results=[],
                    latency_ms=latency_ms,
                    security_flags=["blocked", sec.reason],
                )
            # Use sanitized text downstream if HTML was stripped
            if sec.sanitized_input is not None:
                question = sec.sanitized_input
            _security_flags: list = sec.flags
        else:
            _security_flags = []

        # 1. Plan
        plan = self._orchestrator.plan(question, history)

        # Guard: strip invalid agent names (prevents orchestrator routing to itself)
        plan.steps = [s for s in plan.steps if s.agent in self._VALID_AGENTS]
        # Guard: cap total steps to prevent runaway multi-step plans
        plan.steps = plan.steps[:self._MAX_STEPS]

        # 2. Execute steps sequentially; pass previous outputs as context
        agent_results: List[AgentResult] = []
        for step in plan.steps:
            agent = self._resolve_agent(step.agent)
            if agent is None:
                agent_results.append(AgentResult(
                    agent=step.agent,
                    task=step.task,
                    output="",
                    success=False,
                    error=f"Agent '{step.agent}' is not available (missing service).",
                ))
                continue

            # Conversational agent gets special kwargs
            if step.agent == "conversational":
                result = self._conversational.execute(
                    task=step.task,
                    original_question=question,
                    history=history,
                    previous_results=agent_results,
                    response_style=response_style,
                )
            elif step.agent == "rag_agent":
                original_top_k = self._rag._top_k
                if top_k is not None:
                    self._rag._top_k = top_k
                result = agent.execute(step.task, question, history, agent_results, user_id=user_id)
                self._rag._top_k = original_top_k

                top_score = result.metadata.get("top_score", 1.0)
                if top_score > self._rag_fallback_threshold and self._research is not None:
                    result = self._research.execute(step.task, question, history, agent_results)
            else:
                result = agent.execute(step.task, question, history, agent_results, user_id=user_id)

            agent_results.append(result)

            # Stop immediately if a HITL gate was triggered — don't run further
            # steps or synthesize, as subsequent agents would produce output that
            # makes the reply sound like the action already completed.
            if result.metadata.get("hitl_pending"):
                latency_ms = int((time.monotonic() - t0) * 1000)
                return RunResult(
                    output=result.output,
                    plan=plan,
                    agent_results=agent_results,
                    latency_ms=latency_ms,
                    security_flags=_security_flags,
                )

        # 3. Synthesize
        final_output = self._orchestrator.synthesize(question, agent_results, history)

        # Security: output scrub — redact secrets before the reply reaches the caller
        if self._security_agent is not None:
            final_output = self._security_agent.check_output(
                final_output, user_id=user_id or "default"
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        return RunResult(
            output=final_output,
            plan=plan,
            agent_results=agent_results,
            latency_ms=latency_ms,
            security_flags=_security_flags,
        )

    def _resolve_agent(self, agent_name: str):
        return {
            "rag_agent": self._rag,
            "research_agent": self._research,
            "action_agent": self._action,
            "conversational": self._conversational,
        }.get(agent_name)
