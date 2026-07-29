from datetime import date, datetime, timedelta

from app.agents.action_agent import ActionAgent
from app.core.habit_service import HabitService
from app.storage.sqlite_registry import SQLiteRegistry


class _Provider:
    def __init__(self, response):
        self.response = response

    def chat(self, messages):
        return self.response


class _SeqProvider:
    """Returns queued responses in order (extractor call, then estimator call, ...)."""

    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages):
        return self._responses.pop(0)


def test_compound_request_stages_two_hitl_items(tmp_path):
    from app.core.calorie_service import CalorieService

    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        HabitService(registry, user_id="u1").add_habit("going to the gym")
        agent = ActionAgent(
            chat_provider=_SeqProvider([
                # extractor returns TWO actions
                '{"actions":[{"action":"log_habit","params":{"name":"going to the gym","status":"done"}},'
                '{"action":"log_meal","params":{"description":"1 scoop of whey protein"}}]}',
                # estimator for the meal (best-effort, forced)
                '{"status":"ready","dish":"whey protein","calories":120}',
            ]),
            registry=registry,
        )
        res = agent.execute(task="log gym and whey", original_question="x", history=[], user_id="u1")
        items = res.metadata.get("hitl_items", [])
        assert len(items) == 2
        assert {i["action_type"] for i in items} == {"log_habit", "log_calorie"}
        assert res.metadata.get("hitl_pending") is True

        # Approve the habit, reject the meal → exactly one habit log, no calorie entry.
        by_type = {i["action_type"]: i for i in items}
        assert agent.execute_approved(by_type["log_habit"]["id"], user_id="u1").success is True
        registry.resolve_hitl_request(by_type["log_calorie"]["id"], "rejected")

        assert registry._connection.execute("SELECT COUNT(*) c FROM habit_logs").fetchone()["c"] == 1
        assert CalorieService(registry, user_id="u1").today_total() == 0
    finally:
        registry.close()


def test_log_habit_backdates_to_named_day(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        hs = HabitService(registry, user_id="u1")
        hs.add_habit("going to the gym")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        agent = ActionAgent(
            chat_provider=_Provider(
                f'{{"action":"log_habit","params":{{"name":"going to the gym",'
                f'"status":"done","logged_on":"{yesterday}"}}}}'
            ),
            registry=registry,
        )
        res = agent.execute(task="log habit", original_question="i went to the gym yesterday",
                            history=[], user_id="u1")
        # Write is HITL-gated; the confirmation must say "yesterday", not "today".
        assert res.metadata.get("hitl_pending") is True
        assert "yesterday" in res.output and "today" not in res.output

        approved = agent.execute_approved(res.metadata["hitl_id"], user_id="u1")
        assert approved.success is True

        rows = registry._connection.execute("SELECT DATE(logged_at) AS d FROM habit_logs").fetchall()
        assert rows[0]["d"] == yesterday          # logged to yesterday, not today
        assert hs.get_unlogged_today()            # today is still NOT logged
    finally:
        registry.close()


def test_action_agent_lists_todays_todos(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        now = datetime.now().replace(second=0, microsecond=0)
        registry.create_todo("Call dad", due_at=now + timedelta(hours=1), user_id="user-1")
        registry.create_todo("Later task", due_at=now + timedelta(days=1), user_id="user-1")

        agent = ActionAgent(
            chat_provider=_Provider('{"action": "list_todos", "params": {"scope": "today"}}'),
            registry=registry,
        )

        result = agent.execute(
            task="list_todos: retrieve all reminders and tasks scheduled for today",
            original_question="what do i have to do today",
            history=[],
            user_id="user-1",
        )

        assert result.success is True
        assert "Call dad" in result.output
        assert "Later task" not in result.output
    finally:
        registry.close()


def test_action_agent_execute_approved_preserves_user_id(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        registry.create_hitl_request(
            id="hitl-1",
            user_id="user-1",
            action_type="add_todo",
            action_payload={"task": "Call mom", "due_date": "tomorrow at 9:30 AM"},
        )

        agent = ActionAgent(chat_provider=_Provider("{}"), registry=registry)

        result = agent.execute_approved("hitl-1", user_id="user-1")

        assert result.success is True
        user_todos = registry.list_todos(user_id="user-1")
        anonymous_todos = registry.list_todos(user_id="")
        assert [todo["title"] for todo in user_todos] == ["Call mom"]
        assert anonymous_todos == []
    finally:
        registry.close()
