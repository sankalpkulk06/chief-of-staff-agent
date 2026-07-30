"""ActionAgent extraction via native tool-calling feeds the same dispatch/HITL path."""
from app.agents.action_agent import ActionAgent
from app.core.calorie_service import CalorieService
from app.core.habit_service import HabitService
from app.providers.tool_types import ToolCall, ToolChatResult
from app.storage.sqlite_registry import SQLiteRegistry


class _ToolProvider:
    """A tool-capable provider: chat_tools returns preset calls (extraction); chat is unused."""

    def __init__(self, tool_calls):
        self._tool_calls = tool_calls

    def chat_tools(self, messages, tools, tool_choice="auto"):
        return ToolChatResult(content=None, tool_calls=list(self._tool_calls))

    def chat(self, messages=None):  # pragma: no cover - shouldn't be hit in these cases
        raise AssertionError("tool path should not fall back to chat()")


def test_compound_tool_calls_stage_two_hitl_items(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        HabitService(registry, user_id="u1").add_habit("going to the gym")
        provider = _ToolProvider([
            ToolCall(name="log_habit", arguments={"name": "going to the gym", "status": "done"}),
            ToolCall(name="log_burn", arguments={"calories": 400, "description": "gym workout"}),
        ])
        agent = ActionAgent(chat_provider=provider, registry=registry)
        res = agent.execute(task="log gym and 400 burned", original_question="x",
                            history=[], user_id="u1")
        items = res.metadata.get("hitl_items", [])
        assert {i["action_type"] for i in items} == {"log_habit", "log_burn"}
        for it in items:
            assert agent.execute_approved(it["id"], user_id="u1").success is True
        assert registry._connection.execute("SELECT COUNT(*) c FROM habit_logs").fetchone()["c"] == 1
        assert CalorieService(registry, user_id="u1").today_burned() == 400
    finally:
        registry.close()


def test_single_tool_call_read_executes_immediately(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        CalorieService(registry, user_id="u1").add_entry("lunch", 500)
        provider = _ToolProvider([ToolCall(name="calories_remaining", arguments={})])
        agent = ActionAgent(chat_provider=provider, registry=registry)
        res = agent.execute(task="how many left", original_question="x", history=[], user_id="u1")
        assert res.success and "500/2000" in res.output
    finally:
        registry.close()
