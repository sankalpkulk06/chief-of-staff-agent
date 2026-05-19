"""AgentRunner — executes the orchestrator's plan and collects results."""
import time
from typing import Any, Callable, Dict, List, Optional

from app.agents.action_agent import ActionAgent
from app.agents.base import AgentResult, OrchestratorPlan
from app.agents.conversational_agent import ConversationalAgent
from app.agents.email_agent import EmailAgent
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
        rag_eval_inputs: Optional[dict] = None,
    ):
        self.output = output
        self.plan = plan
        self.agent_results = agent_results
        self.latency_ms = latency_ms
        self.security_flags: list = security_flags or []
        # Set when a successful RAG turn retrieved contexts (no web fallback).
        # Structure: {"question": str, "contexts": list[str]}
        self.rag_eval_inputs: Optional[dict] = rag_eval_inputs

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
        email_service: Optional[Any] = None,
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
        self._email_service = email_service
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
            RAGAgent(
                self._retriever,
                self._provider_for("rag_agent"),
                top_k=self._rag_top_k,
                assistant_name=self._assistant_name,
            )
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
            registry=self._registry,
        )
        self._email = (
            EmailAgent(
                self._email_service,
                self._provider_for("email_agent"),
                registry=self._registry,
                assistant_name=self._assistant_name,
            )
            if self._email_service
            else None
        )

    def set_agent_provider(self, agent_name: str, provider: Any, model_spec: str) -> None:
        self._agent_chat_providers[agent_name] = provider
        self._agent_model_specs[agent_name] = model_spec
        self._rebuild_agents()

    def get_agent_model_specs(self) -> Dict[str, str]:
        return dict(self._agent_model_specs)

    _VALID_AGENTS: frozenset = frozenset(
        {"rag_agent", "research_agent", "action_agent", "conversational", "email_agent"}
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
        trace_callback: Optional[Callable[[str, str, str], None]] = None,
    ) -> RunResult:
        def _trace(event_type: str, status: str, message: str) -> None:
            if trace_callback is not None:
                try:
                    trace_callback(event_type, status, message)
                except Exception:
                    pass

        t0 = time.monotonic()

        # History truncation — prevent context window overflow on long sessions
        if len(history) > self._MAX_HISTORY:
            history = history[-self._MAX_HISTORY:]

        # Security: input check — runs before orchestrator so injections never reach LLM
        if self._security_agent is not None:
            _trace("security", "running", "Checking input safety")
            sec = self._security_agent.check_input(question, user_id=user_id or "")
            if sec.blocked:
                latency_ms = int((time.monotonic() - t0) * 1000)
                if sec.reason == "length_exceeded":
                    rejection = "Your message was blocked because it exceeded the maximum length."
                elif sec.reason == "rate_limit_exceeded":
                    rejection = "You're sending messages too quickly. Please wait a moment."
                else:
                    rejection = "Your message was blocked due to a security policy violation."
                _trace("security", "error", sec.reason or "blocked")
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
        _trace("orchestrator", "running", "Planning response")
        plan = self._orchestrator.plan(question, history)

        # Guard: strip invalid agent names (prevents orchestrator routing to itself)
        plan.steps = [s for s in plan.steps if s.agent in self._VALID_AGENTS]
        # Guard: cap total steps to prevent runaway multi-step plans
        plan.steps = plan.steps[:self._MAX_STEPS]

        step_count = len(plan.steps)
        _trace("orchestrator", "complete", f"{step_count} step{'s' if step_count != 1 else ''} planned")

        # 2. Execute steps sequentially; pass previous outputs as context
        agent_results: List[AgentResult] = []
        for step in plan.steps:
            agent = self._resolve_agent(step.agent)
            if agent is None:
                _trace(step.agent, "error", f"Agent not available")
                agent_results.append(AgentResult(
                    agent=step.agent,
                    task=step.task,
                    output="",
                    success=False,
                    error=f"Agent '{step.agent}' is not available (missing service).",
                ))
                continue

            _trace(step.agent, "running", step.task[:80])

            if step.agent == "conversational":
                result = self._conversational.execute(
                    task=step.task,
                    original_question=question,
                    history=history,
                    previous_results=agent_results,
                    response_style=response_style,
                    user_id=user_id,
                )
            elif step.agent == "email_agent":
                result = self._email.execute(
                    task=step.task,
                    original_question=question,
                    history=history,
                    previous_results=agent_results,
                    user_id=user_id,
                    response_style=response_style,
                )
            elif step.agent == "rag_agent":
                original_top_k = self._rag._top_k
                if top_k is not None:
                    self._rag._top_k = top_k
                result = agent.execute(step.task, question, history, agent_results, user_id=user_id)
                self._rag._top_k = original_top_k

                top_score = result.metadata.get("top_score", 1.0)
                chunks_found = result.metadata.get("chunks_found", 0)
                # Fallback to web when: no chunks at all, OR chunks were retrieved but
                # the best match has poor cosine distance (> 0.65 means likely irrelevant).
                # The second condition catches the case where the vector store returns
                # top-K results but none are about the requested topic.
                poor_match = top_score > 0.65
                if (chunks_found == 0 or poor_match) and self._research is not None:
                    _trace("rag_agent", "fallback", "Low relevance in documents, searching the web")
                    result = self._research.execute(step.task, question, history, agent_results)
                    result.metadata["triggered_by_rag_fallback"] = True
            else:
                result = agent.execute(step.task, question, history, agent_results, user_id=user_id)

            if result.success:
                chunks_found = result.metadata.get("chunks_found")
                if chunks_found is not None:
                    _trace(step.agent, "complete", f"Retrieved {chunks_found} chunk{'s' if chunks_found != 1 else ''}")
                else:
                    _trace(step.agent, "complete", "Done")
            else:
                _trace(step.agent, "error", (result.error or "failed")[:80])

            agent_results.append(result)

            # Stop immediately if a HITL gate was triggered — don't run further
            # steps or synthesize, as subsequent agents would produce output that
            # makes the reply sound like the action already completed.
            if result.metadata.get("hitl_pending"):
                _trace("action_agent", "waiting", "Waiting for your approval")
                latency_ms = int((time.monotonic() - t0) * 1000)
                return RunResult(
                    output=result.output,
                    plan=plan,
                    agent_results=agent_results,
                    latency_ms=latency_ms,
                    security_flags=_security_flags,
                )

        if len(agent_results) == 1 and agent_results[0].agent in {"research_agent", "rag_agent"}:
            final_output = agent_results[0].output
            if self._security_agent is not None:
                _trace("output_check", "running", "Scrubbing output")
                final_output = self._security_agent.check_output(
                    final_output, user_id=user_id or ""
                )
            _trace("complete", "complete", "Done")
            latency_ms = int((time.monotonic() - t0) * 1000)
            return RunResult(
                output=final_output,
                plan=plan,
                agent_results=agent_results,
                latency_ms=latency_ms,
                security_flags=_security_flags,
                rag_eval_inputs=self._extract_rag_eval_inputs(question, agent_results),
            )

        # 3. Synthesize — inject stored user facts for persona-aware replies.
        # Create a per-request FactService scoped to the actual user, not the startup default.
        _trace("synthesis", "running", "Synthesizing results")
        user_facts: Optional[str] = None
        if self._registry and user_id:
            per_request_facts = FactService(self._registry, user_id=user_id)
            facts = per_request_facts.list_facts()
            if facts:
                user_facts = "\n".join(f"- {f.content} ({f.category})" for f in facts)
        elif self._fact_service:
            facts = self._fact_service.list_facts()
            if facts:
                user_facts = "\n".join(f"- {f.content} ({f.category})" for f in facts)
        final_output = self._orchestrator.synthesize(question, agent_results, history, user_facts=user_facts)

        # Security: output scrub — redact secrets before the reply reaches the caller
        if self._security_agent is not None:
            _trace("output_check", "running", "Scrubbing output")
            final_output = self._security_agent.check_output(
                final_output, user_id=user_id or ""
            )
        _trace("complete", "complete", "Done")

        latency_ms = int((time.monotonic() - t0) * 1000)
        return RunResult(
            output=final_output,
            plan=plan,
            agent_results=agent_results,
            latency_ms=latency_ms,
            security_flags=_security_flags,
            rag_eval_inputs=self._extract_rag_eval_inputs(question, agent_results),
        )

    @staticmethod
    def _extract_rag_eval_inputs(question: str, agent_results: List[AgentResult]) -> Optional[dict]:
        """Return {question, contexts} for RAGAS scoring, or None if not applicable."""
        fallback_occurred = any(r.metadata.get("triggered_by_rag_fallback") for r in agent_results)
        if fallback_occurred:
            return None
        for r in agent_results:
            if r.agent == "rag_agent" and r.success:
                contexts = r.metadata.get("retrieved_contexts")
                if contexts:
                    return {"question": question, "contexts": contexts}
        return None

    def _resolve_agent(self, agent_name: str):
        return {
            "rag_agent": self._rag,
            "research_agent": self._research,
            "action_agent": self._action,
            "conversational": self._conversational,
            "email_agent": self._email,
        }.get(agent_name)
