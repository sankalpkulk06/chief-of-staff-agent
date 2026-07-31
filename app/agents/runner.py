"""AgentRunner — executes the orchestrator's plan and collects results."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.agents.action_agent import ActionAgent
from app.agents.conversational_agent import ConversationalAgent
from app.agents.email_agent import EmailAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planner_agent import PlannerAgent
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

logger = logging.getLogger(__name__)


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
        planner_service: Optional[Any] = None,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        assistant_name: str = "Sage",
        rag_top_k: int = 5,
        rag_fallback_threshold: float = 0.5,
        security_agent: Optional[SecurityAgent] = None,
        parallelism_enabled: bool = True,
        max_parallel_workers: int = 3,
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
        self._planner_service = planner_service
        self._schedule_todo_callback = schedule_todo_callback
        self._assistant_name = assistant_name
        self._rag_top_k = rag_top_k
        self._rag_fallback_threshold = rag_fallback_threshold
        self._security_agent = security_agent
        self._parallelism_enabled = parallelism_enabled
        self._max_parallel_workers = max(1, max_parallel_workers)

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
        self._planner = (
            PlannerAgent(self._planner_service, assistant_name=self._assistant_name)
            if self._planner_service is not None
            else None
        )

    def set_agent_provider(self, agent_name: str, provider: Any, model_spec: str) -> None:
        self._agent_chat_providers[agent_name] = provider
        self._agent_model_specs[agent_name] = model_spec
        self._rebuild_agents()

    def get_agent_model_specs(self) -> Dict[str, str]:
        return dict(self._agent_model_specs)

    _VALID_AGENTS: frozenset = frozenset(
        {"rag_agent", "research_agent", "action_agent", "conversational", "email_agent", "planner_agent"}
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
        trace_callback: Optional[Callable[..., None]] = None,
    ) -> RunResult:
        def _trace(event_type: str, status: str, message: str, metadata: Optional[dict] = None) -> None:
            if trace_callback is not None:
                try:
                    trace_callback(event_type, status, message, metadata or {})
                except TypeError:
                    try:
                        trace_callback(event_type, status, message)
                    except Exception:
                        pass
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
        plan.steps = self._normalize_plan_steps(plan.steps)

        step_count = len(plan.steps)
        _trace("orchestrator", "complete", f"{step_count} step{'s' if step_count != 1 else ''} planned")

        # 2. Execute dependency batches. Independent read steps in one batch can
        # run concurrently; dependent, write, and synthesis steps run alone.
        agent_results: List[AgentResult] = []
        pending_hitls: List[AgentResult] = []       # every result awaiting approval this turn
        pending_step_ids: set = set()
        batches = (
            plan.execution_batches(max_parallel=self._max_parallel_workers)
            if self._parallelism_enabled
            else [[step] for step in plan.steps]
        )
        for batch_index, batch in enumerate(batches, 1):
            batch_id = f"batch_{batch_index}"
            if pending_hitls:
                batch = [
                    step
                    for step in batch
                    if step.mode != "synthesize"
                    and not (pending_step_ids & set(step.depends_on))
                ]
                if not batch:
                    continue

            if len(batch) > 1:
                _trace(
                    "batch",
                    "running",
                    f"Running {len(batch)} independent steps",
                    {"batch_id": batch_id, "step_ids": [step.id for step in batch]},
                )

            batch_results = self._execute_batch(
                batch=batch,
                batch_id=batch_id,
                question=question,
                history=history,
                previous_results=agent_results,
                response_style=response_style,
                top_k=top_k,
                user_id=user_id,
                trace=_trace,
            )
            agent_results.extend(batch_results)

            # Collect ALL results awaiting approval (a single step may carry several items).
            new_pending = [r for r in batch_results if r.metadata.get("hitl_pending")]
            for result in new_pending:
                pending_hitls.append(result)
                sid = result.metadata.get("step_id")
                if sid:
                    pending_step_ids.add(sid)
            if new_pending:
                _trace(new_pending[0].agent, "waiting", "Waiting for your approval")

        if pending_hitls:
            # Present the confirmation prompt(s) verbatim; any read outputs that also ran are
            # concatenated. No continuation synthesis while items are pending (each resolves on
            # its own, and the surface shows that item's result).
            pending_ids = {id(r) for r in pending_hitls}
            pending_outputs = [r.output for r in pending_hitls if r.output]
            other_outputs = [
                r.output for r in agent_results
                if id(r) not in pending_ids and r.success and r.output
                and not r.metadata.get("hitl_pending")
            ]
            latency_ms = int((time.monotonic() - t0) * 1000)
            return RunResult(
                output="\n\n".join(pending_outputs + other_outputs),
                plan=plan,
                agent_results=agent_results,
                latency_ms=latency_ms,
                security_flags=_security_flags,
            )

        # Verbatim steps (e.g. calorie logging/queries) return their output exactly as-is with
        # no synthesis rewording — even if the planner also emitted a conversational step.
        verbatim_ids = {s.id for s in plan.steps if getattr(s, "verbatim", False)}
        verbatim_outputs = [
            r.output for r in agent_results
            if r.success and r.output and r.metadata.get("step_id") in verbatim_ids
        ]
        if verbatim_outputs:
            final_output = "\n\n".join(verbatim_outputs)
            if self._security_agent is not None:
                _trace("output_check", "running", "Scrubbing output")
                final_output = self._security_agent.check_output(final_output, user_id=user_id or "")
            _trace("complete", "complete", "Done")
            latency_ms = int((time.monotonic() - t0) * 1000)
            return RunResult(
                output=final_output,
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
            self._maybe_learn(question, agent_results, user_id)
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
        user_facts = self._user_facts_for(user_id)
        final_output = self._orchestrator.synthesize(question, agent_results, history, user_facts=user_facts)

        # Security: output scrub — redact secrets before the reply reaches the caller
        if self._security_agent is not None:
            _trace("output_check", "running", "Scrubbing output")
            final_output = self._security_agent.check_output(
                final_output, user_id=user_id or ""
            )
        _trace("complete", "complete", "Done")

        latency_ms = int((time.monotonic() - t0) * 1000)
        self._maybe_learn(question, agent_results, user_id)
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

    def _user_facts_for(self, user_id: Optional[str]) -> Optional[str]:
        facts = None
        if self._registry and user_id:
            facts = FactService(self._registry, user_id=user_id).list_facts()
        elif self._fact_service:
            facts = self._fact_service.list_facts()
        if not facts:
            return None
        return "\n".join(self._format_fact(f) for f in facts)

    @staticmethod
    def _format_fact(f: Any) -> str:
        """Label passively-learned facts so the model treats them as softer than user-stated ones:
        low-trust/tentative facts are attributed ("from an email") and never asserted as certain."""
        tags = [f.category]
        if getattr(f, "status", "confirmed") == "tentative":
            tags.append("tentative, from an email/document" if getattr(f, "trust", "high") == "low"
                        else "tentative")
        return f"- {f.content} ({', '.join(tags)})"

    def _maybe_learn(self, question: str, agent_results: List[AgentResult], user_id: Optional[str]) -> None:
        """Fire the passive fact-learner off the response path (fire-and-forget thread)."""
        try:
            from app.config.settings import get_settings
            settings = get_settings()
            if not settings.passive_learning_enabled or self._registry is None:
                return
            context = "\n\n".join(
                f"[{r.agent}] {r.output}" for r in agent_results if r.success and r.output
            )
            if not context:
                return
            external = any(r.agent in ("email_agent", "rag_agent") for r in agent_results)
            provider = self._agent_chat_providers.get("action_agent") or self._default_chat_provider
            threading.Thread(
                target=self._run_learner,
                args=(question, context, external, user_id or "", settings, provider),
                daemon=True,
            ).start()
        except Exception:
            logger.debug("passive learning: failed to start", exc_info=True)

    def _run_learner(self, question, context, external, user_id, settings, provider) -> None:
        # Own registry/connection — never share the request thread's SQLite handle.
        from app.core.fact_learner import FactLearnerService
        from app.storage.factory import create_registry
        registry = None
        try:
            paths = settings.resolve_paths()
            registry = create_registry(settings.database_url, paths.sqlite_db_path)
            FactLearnerService(registry, provider, settings, user_id=user_id).learn(
                question, context, external=external)
        except Exception:
            logger.debug("passive learning: learner run failed", exc_info=True)
        finally:
            if registry is not None:
                try:
                    registry.close()
                except Exception:
                    pass

    def _attach_hitl_context(self, hitl_id: Optional[str], continuation_output: str) -> None:
        if not hitl_id or not continuation_output or not self._registry:
            return
        attach = getattr(self._registry, "attach_hitl_context", None)
        if attach is None:
            return
        try:
            attach(hitl_id, {"continuation_output": continuation_output})
        except Exception:
            pass

    def _execute_batch(
        self,
        batch: list[AgentStep],
        batch_id: str,
        question: str,
        history: List[dict[str, Any]],
        previous_results: List[AgentResult],
        response_style: Optional[str],
        top_k: Optional[int],
        user_id: Optional[str],
        trace: Callable[[str, str, str, Optional[dict]], None],
    ) -> list[AgentResult]:
        if len(batch) == 1:
            step = batch[0]
            return [
                self._execute_step(
                    step=step,
                    batch_id=batch_id,
                    question=question,
                    history=history,
                    previous_results=list(previous_results),
                    response_style=response_style,
                    top_k=top_k,
                    user_id=user_id,
                    trace=trace,
                )
            ]

        results_by_step_id: dict[str, AgentResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(batch), self._max_parallel_workers)) as executor:
            futures = {
                executor.submit(
                    self._execute_step,
                    step,
                    batch_id,
                    question,
                    list(history),
                    list(previous_results),
                    response_style,
                    top_k,
                    user_id,
                    trace,
                ): step
                for step in batch
            }
            for future in as_completed(futures):
                step = futures[future]
                try:
                    results_by_step_id[step.id] = future.result()
                except Exception as exc:
                    results_by_step_id[step.id] = AgentResult(
                        agent=step.agent,
                        task=step.task,
                        output="",
                        success=False,
                        error=f"Agent failed: {exc}",
                    )

        return [results_by_step_id[step.id] for step in batch]

    def _execute_step(
        self,
        step: AgentStep,
        batch_id: str,
        question: str,
        history: List[dict[str, Any]],
        previous_results: List[AgentResult],
        response_style: Optional[str],
        top_k: Optional[int],
        user_id: Optional[str],
        trace: Callable[[str, str, str, Optional[dict]], None],
    ) -> AgentResult:
        metadata = {
            "step_id": step.id,
            "batch_id": batch_id,
            "parallel_group": step.parallel_group,
            "agent": step.agent,
        }
        agent = self._resolve_agent(step.agent)
        if agent is None:
            trace(step.agent, "error", "Agent not available", metadata)
            return AgentResult(
                agent=step.agent,
                task=step.task,
                output="",
                success=False,
                error=f"Agent '{step.agent}' is not available (missing service).",
            )

        trace(step.agent, "running", step.task[:80], metadata)

        if step.agent == "conversational":
            result = self._conversational.execute(
                task=step.task,
                original_question=question,
                history=history,
                previous_results=previous_results,
                response_style=response_style,
                user_id=user_id,
            )
        elif step.agent == "email_agent":
            result = self._email.execute(
                task=step.task,
                original_question=question,
                history=history,
                previous_results=previous_results,
                user_id=user_id,
                response_style=response_style,
            )
        elif step.agent == "planner_agent":
            result = self._planner.execute(
                task=step.task,
                original_question=question,
                history=history,
                previous_results=previous_results,
                user_id=user_id,
                response_style=response_style,
            )
        elif step.agent == "rag_agent":
            original_top_k = self._rag._top_k
            if top_k is not None:
                self._rag._top_k = top_k
            try:
                result = agent.execute(step.task, question, history, previous_results, user_id=user_id)
            finally:
                self._rag._top_k = original_top_k

            chunks_found = result.metadata.get("chunks_found", 0)
            if chunks_found == 0 and self._research is not None:
                trace("rag_agent", "fallback", "No matching document chunks, searching the web", metadata)
                result = self._research.execute(step.task, question, history, previous_results)
                result.metadata["triggered_by_rag_fallback"] = True
        else:
            result = agent.execute(step.task, question, history, previous_results, user_id=user_id)

        if result.success:
            chunks_found = result.metadata.get("chunks_found")
            if chunks_found is not None:
                trace(step.agent, "complete", f"Retrieved {chunks_found} chunk{'s' if chunks_found != 1 else ''}", metadata)
            else:
                trace(step.agent, "complete", "Done", metadata)
        else:
            trace(step.agent, "error", (result.error or "failed")[:80], metadata)
        result.metadata.setdefault("step_id", step.id)
        result.metadata.setdefault("batch_id", batch_id)
        result.metadata.setdefault("parallel_group", step.parallel_group)
        return result

    @staticmethod
    def _normalize_plan_steps(steps: List[AgentStep]) -> List[AgentStep]:
        seen_ids: set[str] = set()
        for i, step in enumerate(steps, 1):
            if not step.id or step.id in seen_ids:
                step.id = f"step_{i}"
            seen_ids.add(step.id)
            if not step.mode:
                step.mode = "synthesize" if step.agent == "conversational" else "read"

        valid_ids = {step.id for step in steps}
        for i, step in enumerate(steps):
            step.depends_on = [
                dep for dep in step.depends_on if dep in valid_ids and dep != step.id
            ]
            if step.agent == "conversational":
                step.mode = "synthesize"
            if step.mode == "synthesize" and not step.depends_on:
                step.depends_on = [
                    prior.id for prior in steps[:i] if prior.mode != "synthesize"
                ]
        return steps

    def _resolve_agent(self, agent_name: str):
        return {
            "rag_agent": self._rag,
            "research_agent": self._research,
            "action_agent": self._action,
            "conversational": self._conversational,
            "email_agent": self._email,
            "planner_agent": self._planner,
        }.get(agent_name)
