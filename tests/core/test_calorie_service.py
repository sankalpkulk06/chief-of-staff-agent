from datetime import datetime, timedelta

from app.agents.action_agent import ActionAgent
from app.core import calorie_util
from app.core.calorie_service import CalorieService, advance_calorie_flow
from app.storage.sqlite_registry import SQLiteRegistry


class _Provider:
    """Returns a fixed response, or cycles through a list of responses."""

    def __init__(self, response):
        self._responses = iter(response) if isinstance(response, list) else None
        self._single = None if isinstance(response, list) else response

    def chat(self, messages):
        if self._responses is not None:
            return next(self._responses)
        return self._single


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------

def test_today_total_and_remaining(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        svc.add_entry("biryani", 600)
        svc.add_entry("coffee", 50)
        assert svc.today_total() == 650
        assert svc.remaining(2000) == 1350
        # Scoped per-user.
        assert CalorieService(registry, user_id="u2").today_total() == 0
    finally:
        registry.close()


def test_only_todays_entries_count(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        svc.add_entry("yesterday dinner", 800, eaten_at=datetime.now() - timedelta(days=1))
        svc.add_entry("today snack", 200)
        assert svc.today_total() == 200
        totals = svc.daily_totals(days=2)
        assert totals[0]["total"] == 800  # yesterday
        assert totals[1]["total"] == 200  # today
    finally:
        registry.close()


def test_undo_removes_latest_today(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        svc.add_entry("first", 100)
        svc.add_entry("second", 300)
        removed = svc.delete_latest_today()
        assert removed["description"] == "second"
        assert svc.today_total() == 100
    finally:
        registry.close()


def test_budget_helper_roundtrip(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        assert calorie_util.has_calorie_budget(registry, "u1") is False
        assert calorie_util.get_calorie_budget(registry, "u1") == calorie_util.DEFAULT_CALORIE_BUDGET
        calorie_util.set_calorie_budget(registry, "u1", 1800)
        assert calorie_util.has_calorie_budget(registry, "u1") is True
        assert calorie_util.get_calorie_budget(registry, "u1") == 1800
    finally:
        registry.close()


# ----------------------------------------------------------------------
# State machine
# ----------------------------------------------------------------------

def test_flow_ready_then_confirm_logs(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        provider = _Provider('{"status":"ready","dish":"chicken biryani","calories":600}')
        # First turn: enough detail -> confirm question.
        step1 = advance_calorie_flow(provider, svc, 2000, None, "I ate a chicken biryani")
        assert step1["pending"]["stage"] == "confirming"
        assert step1["pending"]["calories"] == 600
        assert not step1["logged"]
        # Second turn: user says yes -> logged.
        step2 = advance_calorie_flow(provider, svc, 2000, step1["pending"], "yes")
        assert step2["logged"] is True
        assert step2["pending"] is None
        assert svc.today_total() == 600
        assert "1400" in step2["reply"]  # remaining
    finally:
        registry.close()


def test_flow_needs_clarification_then_estimates(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        provider = _Provider([
            '{"status":"need_info","question":"How much chicken?"}',
            '{"status":"ready","dish":"chicken quesadilla","calories":850}',
        ])
        step1 = advance_calorie_flow(provider, svc, 2000, None, "I had a chicken quesadilla")
        assert step1["pending"]["stage"] == "clarifying"
        assert "How much chicken?" in step1["reply"]
        step2 = advance_calorie_flow(provider, svc, 2000, step1["pending"], "300g chicken breast, little cheese")
        assert step2["pending"]["stage"] == "confirming"
        assert step2["pending"]["calories"] == 850
    finally:
        registry.close()


def test_flow_confirm_no_discards(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        state = {"stage": "confirming", "description": "cake", "dish": "cake", "calories": 400}
        result = advance_calorie_flow(_Provider("{}"), svc, 2000, state, "no")
        assert result["logged"] is False
        assert result["pending"] is None
        assert svc.today_total() == 0
    finally:
        registry.close()


def test_flow_forces_estimate_after_max_clarify_rounds(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        svc = CalorieService(registry, user_id="u1")
        # Model keeps asking; after MAX_CLARIFY_ROUNDS the flow forces a ready estimate.
        provider = _Provider('{"status":"need_info","question":"more detail?"}')
        state = {"stage": "clarifying", "description": "soup", "clarify_rounds": 2}
        result = advance_calorie_flow(provider, svc, 2000, state, "some soup")
        assert result["pending"]["stage"] == "confirming"  # forced past clarifying
    finally:
        registry.close()


# ----------------------------------------------------------------------
# ActionAgent verbs
# ----------------------------------------------------------------------

def test_action_agent_log_meal_returns_pending(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        agent = ActionAgent(
            chat_provider=_Provider([
                '{"action":"log_meal","params":{"description":"a banana"}}',   # extractor
                '{"status":"ready","dish":"banana","calories":105}',            # estimator
            ]),
            registry=registry,
        )
        result = agent.execute(
            task="log_meal: a banana", original_question="I ate a banana",
            history=[], user_id="u1",
        )
        assert result.success is True
        assert result.metadata.get("calorie_pending", {}).get("stage") == "confirming"
        assert "105" in result.output
    finally:
        registry.close()


def test_action_agent_set_budget_and_remaining(tmp_path):
    registry = SQLiteRegistry(tmp_path / "r.db")
    try:
        set_agent = ActionAgent(
            chat_provider=_Provider('{"action":"set_calorie_budget","params":{"amount":1800}}'),
            registry=registry,
        )
        set_agent.execute(task="set budget", original_question="set my calorie budget to 1800",
                          history=[], user_id="u1")
        assert calorie_util.get_calorie_budget(registry, "u1") == 1800

        CalorieService(registry, user_id="u1").add_entry("lunch", 500)
        rem_agent = ActionAgent(
            chat_provider=_Provider('{"action":"calories_remaining","params":{}}'),
            registry=registry,
        )
        result = rem_agent.execute(task="remaining", original_question="how many calories left",
                                   history=[], user_id="u1")
        assert "500/1800" in result.output
        assert "1300 left" in result.output
    finally:
        registry.close()
