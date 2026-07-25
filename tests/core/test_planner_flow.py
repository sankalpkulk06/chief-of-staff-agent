"""End-to-end wiring test for the daily planner: check-in → HITL → execute → re-plan.

Uses a fake CalendarService (records mutations, no network) and a scripted LLM, against
a real SQLiteRegistry. Verifies PlannerService stages the right HITL batch and
CalendarPlanExecutor applies create/patch/soft_cancel and keeps the local mirror in sync.
"""
import tempfile
from datetime import date
from pathlib import Path

import pytest

from app.agents.calendar_plan_executor import CalendarPlanExecutor
from app.core.planner_service import PlannerService
from app.services.calendar_service import CalEvent, GoogleTask
from app.storage.sqlite_registry import SQLiteRegistry

USER = "u1"
SESSION = "s1"
PLAN_DATE = date(2026, 8, 1)
TOKEN = {"token": "x", "refresh_token": "r", "scopes": ["calendar.events"]}


class FakeCalendar:
    """Records mutations; returns configured fixed events / tasks."""

    def __init__(self, fixed=None, tasks=None):
        self._fixed = fixed or []
        self._tasks = tasks or []
        self.inserted = []
        self.patched = []
        self.cancelled = []
        self.completed_tasks = []
        self._seq = 0

    def get_calendar_timezone(self, token):
        return "UTC", None

    def list_events(self, token, *, time_min_rfc3339, time_max_rfc3339, calendar_id="primary"):
        return list(self._fixed), None

    def list_open_tasks(self, token):
        return list(self._tasks), None

    def insert_event(self, token, *, block_id, title, start_rfc3339, end_rfc3339, tz, calendar_id="primary"):
        self._seq += 1
        gid = f"gev{self._seq}"
        self.inserted.append({"block_id": block_id, "title": title, "start": start_rfc3339, "end": end_rfc3339})
        return CalEvent(id=gid, etag=f"etag{self._seq}", title=title,
                        start=start_rfc3339, end=end_rfc3339, sage_managed=True, sage_block_id=block_id), None

    def patch_event(self, token, *, google_event_id, etag, fields, tz, calendar_id="primary"):
        self._seq += 1
        self.patched.append({"google_event_id": google_event_id, "fields": fields})
        return CalEvent(id=google_event_id, etag=f"etag{self._seq}", title=fields.get("title", ""),
                        start=fields.get("start_rfc3339"), end=fields.get("end_rfc3339"), sage_managed=True), None

    def soft_cancel_event(self, token, *, google_event_id, etag, calendar_id="primary"):
        self.cancelled.append(google_event_id)
        return CalEvent(id=google_event_id, etag="x", status="cancelled", sage_managed=True), None

    def complete_task(self, token, *, tasklist_id, task_id):
        self.completed_tasks.append((tasklist_id, task_id))
        return {"status": "completed"}, None


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def generate(self, prompt):
        return self._responses.pop(0)


@pytest.fixture
def registry():
    r = SQLiteRegistry(Path(tempfile.mkdtemp()) / "t.db")
    r.upsert_email_token(USER, TOKEN, "google_calendar")
    return r


def _planner(registry, calendar, llm):
    return PlannerService(registry=registry, calendar_service=calendar, chat_provider=llm, default_timezone="UTC")


def test_checkin_connected(registry):
    planner = _planner(registry, FakeCalendar(), ScriptedLLM([]))
    start = planner.start_checkin(USER, PLAN_DATE.isoformat())
    assert start.connected is True
    assert start.plan_date == PLAN_DATE


def test_checkin_not_connected():
    r = SQLiteRegistry(Path(tempfile.mkdtemp()) / "t.db")  # no token
    planner = _planner(r, FakeCalendar(), ScriptedLLM([]))
    start = planner.start_checkin(USER, "tomorrow")
    assert start.connected is False
    assert "isn't connected" in start.message


def test_build_plan_creates_hitl_batch(registry):
    llm = ScriptedLLM(['[{"title":"Gym","start":"08:00","end":"09:30","source_kind":"habit","source_ref":"h1"}]'])
    planner = _planner(registry, FakeCalendar(), llm)
    result = planner.build_plan(USER, SESSION, PLAN_DATE, notes=["gym in the morning"])

    assert result.hitl_pending is True
    row = registry.get_hitl_request(result.hitl_id)
    assert row["action_type"] == "apply_calendar_plan"
    ops = row["action_payload"]["operations"]
    assert len(ops) == 1 and ops[0]["action"] == "create"
    assert ops[0]["start_min"] == 8 * 60


def test_full_lifecycle_create_patch_cancel(registry):
    cal = FakeCalendar()

    # 1. CREATE
    planner = _planner(registry, cal,
        ScriptedLLM(['[{"title":"Gym","start":"08:00","end":"09:30","source_kind":"habit","source_ref":"h1"}]']))
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=[])
    CalendarPlanExecutor(cal, registry).apply(res.hitl_id, USER)
    assert len(cal.inserted) == 1
    active = registry.list_managed_calendar_events(USER, PLAN_DATE.isoformat(), "active")
    assert len(active) == 1 and active[0]["google_event_id"] == "gev1"

    # 2. PATCH (same habit ref, moved earlier) → no new create, one patch
    planner = _planner(registry, cal,
        ScriptedLLM(['[{"title":"Gym","start":"06:00","end":"07:30","source_kind":"habit","source_ref":"h1"}]']))
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=["move gym earlier"])
    ops = registry.get_hitl_request(res.hitl_id)["action_payload"]["operations"]
    assert [o["action"] for o in ops] == ["patch"]
    CalendarPlanExecutor(cal, registry).apply(res.hitl_id, USER)
    assert len(cal.patched) == 1
    assert len(cal.inserted) == 1  # unchanged — no new event
    active = registry.list_managed_calendar_events(USER, PLAN_DATE.isoformat(), "active")
    assert active[0]["start_local"].startswith("2026-08-01T06:00")

    # 3. SOFT CANCEL (empty plan → drop the Gym)
    planner = _planner(registry, cal, ScriptedLLM(["[]"]))
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=["clear my plan"])
    ops = registry.get_hitl_request(res.hitl_id)["action_payload"]["operations"]
    assert [o["action"] for o in ops] == ["soft_cancel"]
    CalendarPlanExecutor(cal, registry).apply(res.hitl_id, USER)
    assert cal.cancelled == ["gev1"]
    assert registry.list_managed_calendar_events(USER, PLAN_DATE.isoformat(), "active") == []
    assert len(registry.list_managed_calendar_events(USER, PLAN_DATE.isoformat(), "cancelled")) == 1


def test_removing_task_block_completes_google_task(registry):
    cal = FakeCalendar(tasks=[GoogleTask(id="gt7", title="Pick Up Tickets", tasklist_id="list1")])
    # 1. Schedule the task as a block and apply it.
    planner = _planner(registry, cal,
        ScriptedLLM(['[{"title":"Pick Up Tickets","start":"14:00","end":"14:30","source_kind":"google_task","source_ref":"ignored"}]']))
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=["pick up tickets at 2"])
    CalendarPlanExecutor(cal, registry).apply(res.hitl_id, USER)
    active = registry.list_managed_calendar_events(USER, PLAN_DATE.isoformat(), "active")
    assert active[0]["source_ref"] == "list1::gt7"   # authoritative composite ref, not the LLM's echo

    # 2. Remove it → soft-cancel the block AND complete the underlying Google Task.
    planner = _planner(registry, cal, ScriptedLLM(["[]"]))
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=["remove pick up tickets"])
    assert [o["action"] for o in registry.get_hitl_request(res.hitl_id)["action_payload"]["operations"]] == ["soft_cancel"]
    CalendarPlanExecutor(cal, registry).apply(res.hitl_id, USER)
    assert cal.completed_tasks == [("list1", "gt7")]


def test_task_dedup_across_sources(registry):
    cal = FakeCalendar(tasks=[GoogleTask(id="gt1", title="Ship PR", tasklist_id="@default")])
    registry.create_todo(title="Ship PR", user_id=USER)  # same title as the Google Task
    # LLM schedules one "Ship PR" block; dedup means only one candidate was offered.
    llm = ScriptedLLM(['[{"title":"Ship PR","start":"10:00","end":"11:00","source_kind":"todo","source_ref":"t1"}]'])
    planner = _planner(registry, cal, llm)
    res = planner.build_plan(USER, SESSION, PLAN_DATE, notes=[])
    ops = registry.get_hitl_request(res.hitl_id)["action_payload"]["operations"]
    assert len([o for o in ops if o["action"] == "create"]) == 1
