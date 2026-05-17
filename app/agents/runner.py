"""AgentRunner — executes the orchestrator's plan and collects results."""
import time
from typing import Any, Callable, Dict, List, Optional

from app.agents.action_agent import ActionAgent
from app.agents.base import AgentResult, OrchestratorPlan
from app.agents.conversational_agent import ConversationalAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.rag_agent import RAGAgent
from app.agents.research_agent import ResearchAgent
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever
from app.services.news_service import NewsService
from app.services.reminders_service import RemindersService
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
    ):
        self.output = output
        self.plan = plan
        self.agent_results = agent_results
        self.latency_ms = latency_ms

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
        retriever: Optional[Retriever] = None,
        registry: Optional[SQLiteRegistry] = None,
        fact_service: Optional[FactService] = None,
        news_service: Optional[NewsService] = None,
        web_search_service: Optional[WebSearchService] = None,
        habit_service: Optional[HabitService] = None,
        reminders_service: Optional[RemindersService] = None,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        assistant_name: str = "Sage",
        rag_top_k: int = 5,
    ):
        self._orchestrator = OrchestratorAgent(chat_provider, assistant_name=assistant_name)

        self._rag = RAGAgent(retriever, chat_provider, top_k=rag_top_k) if retriever else None
        self._research = ResearchAgent(chat_provider, web_search_service, news_service)
        self._action = ActionAgent(
            chat_provider,
            registry=registry,
            fact_service=fact_service,
            habit_service=habit_service,
            reminders_service=reminders_service,
            schedule_todo_callback=schedule_todo_callback,
        )
        self._conversational = ConversationalAgent(chat_provider, assistant_name, fact_service)

    def run(
        self,
        question: str,
        history: List[dict[str, Any]],
        response_style: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> RunResult:
        t0 = time.monotonic()

        # 1. Plan
        plan = self._orchestrator.plan(question, history)

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
            elif step.agent == "rag_agent" and top_k is not None:
                # Allow caller to override top_k (e.g. from session setting)
                original_top_k = self._rag._top_k
                self._rag._top_k = top_k
                result = agent.execute(step.task, question, history, agent_results)
                self._rag._top_k = original_top_k
            else:
                result = agent.execute(step.task, question, history, agent_results)

            agent_results.append(result)

        # 3. Synthesize
        final_output = self._orchestrator.synthesize(question, agent_results, history)

        latency_ms = int((time.monotonic() - t0) * 1000)
        return RunResult(
            output=final_output,
            plan=plan,
            agent_results=agent_results,
            latency_ms=latency_ms,
        )

    def _resolve_agent(self, agent_name: str):
        return {
            "rag_agent": self._rag,
            "research_agent": self._research,
            "action_agent": self._action,
            "conversational": self._conversational,
        }.get(agent_name)
