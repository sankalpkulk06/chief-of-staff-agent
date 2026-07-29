import time

from app.agents.base import AgentResult, AgentStep, OrchestratorPlan
from app.agents.orchestrator import OrchestratorAgent
from app.agents.runner import AgentRunner


class _Provider:
    def chat(self, messages):
        return "unused"


class _PlanOnlyOrchestrator:
    def __init__(self, plan):
        self._plan = plan

    def plan(self, question, history):
        return self._plan

    def synthesize(self, original_question, results, history, user_facts=None):
        return "\n".join(r.output for r in results if r.success and r.output)


class _SlowAgent:
    def __init__(self, name, delay, output, starts):
        self._name = name
        self._delay = delay
        self._output = output
        self._starts = starts

    def execute(self, task, original_question, history, previous_results=None, **kwargs):
        self._starts[self._name] = time.monotonic()
        time.sleep(self._delay)
        return AgentResult(
            agent=self._name,
            task=task,
            output=self._output,
            success=True,
        )


class _HitlAgent:
    def execute(self, task, original_question, history, previous_results=None, **kwargs):
        return AgentResult(
            agent="action_agent",
            task=task,
            output="I'm about to add a reminder: call mom. Please confirm.",
            success=True,
            metadata={"hitl_pending": True, "hitl_id": "hitl-1"},
        )


class _RagAgent:
    def __init__(self, chunks_found, top_score, output="document answer"):
        self._top_k = 5
        self._chunks_found = chunks_found
        self._top_score = top_score
        self._output = output

    def execute(self, task, original_question, history, previous_results=None, **kwargs):
        return AgentResult(
            agent="rag_agent",
            task=task,
            output=self._output,
            success=True,
            metadata={
                "chunks_found": self._chunks_found,
                "top_score": self._top_score,
                "retrieved_contexts": ["relevant document context"],
            },
        )


class _ResearchAgent:
    def __init__(self):
        self.calls = 0

    def execute(self, task, original_question, history, previous_results=None, **kwargs):
        self.calls += 1
        return AgentResult(
            agent="research_agent",
            task=task,
            output="web fallback",
            success=True,
        )


class _ContextRegistry:
    def __init__(self):
        self.contexts = {}

    def attach_hitl_context(self, hitl_id, context):
        self.contexts[hitl_id] = context


def test_parse_plan_backfills_dependency_fields_for_old_json():
    plan = OrchestratorAgent._parse_plan(
        """
        {
          "reasoning": "Two independent reads, then merge.",
          "steps": [
            {"agent": "action_agent", "task": "list_todos: retrieve tasks due today"},
            {"agent": "research_agent", "task": "fetch_news: latest IPL news"},
            {"agent": "conversational", "task": "present both results"}
          ]
        }
        """,
        "what are my tasks today and IPL news",
    )

    assert [step.id for step in plan.steps] == ["step_1", "step_2", "step_3"]
    assert [step.mode for step in plan.steps] == ["read", "read", "synthesize"]
    assert plan.steps[2].depends_on == ["step_1", "step_2"]


def test_execution_batches_run_independent_reads_together_then_synthesis():
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="todos", agent="action_agent", task="list_todos: today", mode="read"),
            AgentStep(id="news", agent="research_agent", task="fetch_news: IPL", mode="read"),
            AgentStep(
                id="merge",
                agent="conversational",
                task="present both",
                mode="synthesize",
                depends_on=["todos", "news"],
            ),
        ]
    )

    assert [[step.id for step in batch] for batch in plan.execution_batches()] == [
        ["todos", "news"],
        ["merge"],
    ]


def test_execution_batches_isolate_writes():
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="search", agent="research_agent", task="web_search: LangGraph", mode="read"),
            AgentStep(id="todo", agent="action_agent", task="add_todo: try LangGraph", mode="write"),
            AgentStep(
                id="merge",
                agent="conversational",
                task="present both",
                mode="synthesize",
                depends_on=["search", "todo"],
            ),
        ]
    )

    assert [[step.id for step in batch] for batch in plan.execution_batches()] == [
        ["search"],
        ["todo"],
        ["merge"],
    ]


def test_runner_executes_independent_steps_in_parallel():
    starts = {}
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="todos", agent="action_agent", task="list_todos: today", mode="read"),
            AgentStep(id="news", agent="research_agent", task="fetch_news: IPL", mode="read"),
        ]
    )
    runner = AgentRunner(
        chat_provider=_Provider(),
        parallelism_enabled=True,
        max_parallel_workers=2,
    )
    runner._orchestrator = _PlanOnlyOrchestrator(plan)
    runner._action = _SlowAgent("action_agent", 0.5, "Call mom by 9 PM.", starts)
    runner._research = _SlowAgent("research_agent", 0.5, "IPL playoff race is tight.", starts)

    t0 = time.monotonic()
    result = runner.run("tasks today and IPL news", history=[])
    elapsed = time.monotonic() - t0

    assert elapsed < 0.9
    assert abs(starts["action_agent"] - starts["research_agent"]) < 0.2
    assert result.output == "Call mom by 9 PM.\nIPL playoff race is tight."
    assert [r.agent for r in result.agent_results] == ["action_agent", "research_agent"]


def test_runner_continues_independent_work_after_hitl_pause():
    starts = {}
    registry = _ContextRegistry()
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="reminder", agent="action_agent", task="add_todo: call mom", mode="write"),
            AgentStep(id="tutorials", agent="research_agent", task="web_search: Python tutorials", mode="read"),
            AgentStep(
                id="merge",
                agent="conversational",
                task="present both",
                mode="synthesize",
                depends_on=["reminder", "tutorials"],
            ),
        ]
    )
    runner = AgentRunner(
        chat_provider=_Provider(),
        registry=registry,
        parallelism_enabled=True,
        max_parallel_workers=2,
    )
    runner._orchestrator = _PlanOnlyOrchestrator(plan)
    runner._action = _HitlAgent()
    runner._research = _SlowAgent("research_agent", 0.01, "Use the official Python tutorial.", starts)

    result = runner.run("remind me and find tutorials", history=[])

    assert "Please confirm" in result.output
    # Independent read work still runs and is now surfaced immediately in the same reply
    # (alongside the pending confirmation), rather than deferred as a post-approval continuation.
    assert "official Python tutorial" in result.output
    assert [r.agent for r in result.agent_results] == ["action_agent", "research_agent"]
    assert result.agent_results[0].metadata.get("hitl_pending") is True


def test_runner_keeps_rag_result_when_chunks_exist_even_with_high_distance():
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="docs", agent="rag_agent", task="search_documents: uploaded document", mode="read"),
        ]
    )
    research = _ResearchAgent()
    runner = AgentRunner(chat_provider=_Provider())
    runner._orchestrator = _PlanOnlyOrchestrator(plan)
    runner._rag = _RagAgent(chunks_found=1, top_score=0.9, output="answer from retrieved document")
    runner._research = research

    result = runner.run("what does my uploaded document say?", history=[])

    assert result.output == "answer from retrieved document"
    assert research.calls == 0
    assert result.agent_results[0].metadata.get("triggered_by_rag_fallback") is None


def test_runner_falls_back_to_web_when_rag_finds_no_chunks():
    plan = OrchestratorPlan(
        steps=[
            AgentStep(id="docs", agent="rag_agent", task="search_documents: uploaded document", mode="read"),
        ]
    )
    research = _ResearchAgent()
    runner = AgentRunner(chat_provider=_Provider())
    runner._orchestrator = _PlanOnlyOrchestrator(plan)
    runner._rag = _RagAgent(chunks_found=0, top_score=1.0)
    runner._research = research

    result = runner.run("what does my uploaded document say?", history=[])

    assert result.output == "web fallback"
    assert research.calls == 1
    assert result.agent_results[0].metadata["triggered_by_rag_fallback"] is True
