"""Orchestrator planning via native structured tool-calling (create_plan) + legacy fallback."""
from app.agents.orchestrator import OrchestratorAgent
from app.providers.tool_types import ToolCall, ToolChatResult


class _ToolProvider:
    """Tool-capable provider: chat_tools returns a preset create_plan call."""

    def __init__(self, steps, reasoning="", record=None):
        self._steps = steps
        self._reasoning = reasoning
        self._record = record if record is not None else {}

    def chat_tools(self, messages, tools, tool_choice="auto"):
        self._record["tool_choice"] = tool_choice
        self._record["tool_names"] = [t.name for t in tools]
        return ToolChatResult(tool_calls=[ToolCall(
            name="create_plan", arguments={"reasoning": self._reasoning, "steps": self._steps})])

    def chat(self, messages=None):  # pragma: no cover - tool path shouldn't fall back
        raise AssertionError("tool path should not call chat()")


class _LegacyProvider:
    """No chat_tools → planning must fall back to prompt-for-JSON via chat()."""

    def __init__(self, response):
        self._response = response

    def chat(self, messages=None):
        return self._response


def test_plan_via_tool_call_builds_validated_plan():
    rec = {}
    provider = _ToolProvider(
        steps=[
            {"id": "facts", "agent": "action_agent", "task": "list_facts", "mode": "read", "depends_on": []},
            {"id": "docs", "agent": "rag_agent", "task": "search_documents: hotel", "mode": "read", "depends_on": []},
            {"id": "merge", "agent": "conversational", "task": "present", "mode": "synthesize",
             "depends_on": ["facts", "docs"]},
        ],
        reasoning="read facts + docs then merge",
        record=rec,
    )
    plan = OrchestratorAgent(provider).plan("when is my hotel booking?", history=[])

    assert rec["tool_choice"] == "required"                 # forced structured output
    assert rec["tool_names"] == ["create_plan"]
    assert [s.agent for s in plan.steps] == ["action_agent", "rag_agent", "conversational"]
    merge = next(s for s in plan.steps if s.id == "merge")
    assert merge.mode == "synthesize" and set(merge.depends_on) == {"facts", "docs"}
    assert plan.reasoning == "read facts + docs then merge"


def test_plan_tool_call_parses_verbatim_flag():
    provider = _ToolProvider(steps=[
        {"id": "action", "agent": "action_agent", "task": "log_meal: a banana", "mode": "write", "verbatim": True},
    ])
    plan = OrchestratorAgent(provider).plan("I ate a banana", history=[])
    assert len(plan.steps) == 1
    assert plan.steps[0].verbatim is True and plan.steps[0].mode == "write"


def test_invalid_agent_downgraded_to_conversational():
    plan = OrchestratorAgent._plan_from_args(
        {"steps": [{"agent": "hacker_agent", "task": "do bad things"}]}, "x")
    assert plan.steps[0].agent == "conversational"


def test_empty_steps_defaults_to_conversational():
    plan = OrchestratorAgent._plan_from_args({"steps": []}, "hello there")
    assert len(plan.steps) == 1 and plan.steps[0].agent == "conversational"
    assert plan.steps[0].task == "hello there"


def test_planning_falls_back_to_prompt_json_without_tools():
    # A provider with no chat_tools must use the legacy JSON path (and still route email).
    provider = _LegacyProvider(
        '{"reasoning":"x","steps":[{"agent":"email_agent","task":"fetch inbox","mode":"read"}]}')
    plan = OrchestratorAgent(provider).plan("check my email", history=[])
    assert any(s.agent == "email_agent" for s in plan.steps)


def test_tool_path_is_guard_free():
    # The trusted tool path must NOT re-apply the deterministic guards: a plan the model
    # returns is used as-is. Here the model (hypothetically) routed a calorie message to
    # conversational; without guards that plan passes through unchanged.
    provider = _ToolProvider(steps=[{"agent": "conversational", "task": "chat about food"}])
    plan = OrchestratorAgent(provider).plan("I ate a pizza", history=[])
    assert [s.agent for s in plan.steps] == ["conversational"]  # calorie guard did NOT fire


def test_legacy_path_still_applies_email_guard():
    # Legacy JSON omits email_agent for a clear email request → the guard injects it.
    provider = _LegacyProvider(
        '{"reasoning":"x","steps":[{"agent":"conversational","task":"reply"}]}')
    plan = OrchestratorAgent(provider).plan("any unread emails?", history=[])
    assert any(s.agent == "email_agent" for s in plan.steps)


def test_legacy_path_still_applies_calorie_guard():
    # Legacy JSON mis-routes a calorie log → the guard forces a single verbatim action step.
    provider = _LegacyProvider(
        '{"reasoning":"x","steps":[{"agent":"conversational","task":"reply"}]}')
    plan = OrchestratorAgent(provider).plan("I just ate a burrito", history=[])
    assert len(plan.steps) == 1
    assert plan.steps[0].agent == "action_agent" and plan.steps[0].verbatim is True
