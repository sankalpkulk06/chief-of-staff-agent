"""Tests for the windowed analytics dashboard + insights (compute-on-read)."""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.analytics_service import AnalyticsService
from app.core.habit_service import HabitService
from app.storage.sqlite_registry import SQLiteRegistry


@pytest.fixture()
def reg():
    return SQLiteRegistry(Path(tempfile.mkdtemp()) / "t.db")


def _user(reg, name="alice"):
    return reg.create_user(name, "pw12345")["user_id"]


def test_dashboard_shape_and_empty_user(reg):
    u = _user(reg)
    d = AnalyticsService(reg).get_dashboard(u, window_days=30)
    assert set(d) == {"window_days", "kpis", "habits", "todos", "usage", "agents", "topics"}
    assert d["window_days"] == 30
    assert d["habits"] == [] and d["agents"] == []
    assert d["todos"]["total"] == 0 and d["todos"]["pct"] == 0
    assert len(d["usage"]["heatmap"]) == 7 and len(d["usage"]["heatmap"][0]) == 24


def test_agents_and_chat_source(reg):
    u = _user(reg)
    sid = reg.get_or_create_named_session(f"cli:{u}:default", user_id=u)
    for i in range(4):
        reg.append_turn(session_id=sid, turn_id=f"t{i}",
                        role="user" if i % 2 == 0 else "assistant",
                        content="deploy the docker container", turn_index=i)
    for a in ["research_agent", "research_agent", "email_agent"]:
        reg.record_agent_invocation(u, sid, a)

    d = AnalyticsService(reg).get_dashboard(u, 30)
    agents = {a["name"]: a for a in d["agents"]}
    assert agents["research_agent"]["count"] == 2
    assert agents["research_agent"]["pct"] == 67
    assert d["usage"]["source"]["cli"] == 2          # 2 user turns on a cli: session
    assert "docker" in [t["label"] for t in d["topics"]]


def test_todo_stats(reg):
    u = _user(reg)
    done = reg.create_todo("done", user_id=u)
    reg.mark_todo_completed(done["id"])
    reg.create_todo("overdue", due_at=datetime.now() - timedelta(days=2), user_id=u)
    reg.create_todo("future", due_at=datetime.now() + timedelta(days=2), user_id=u)

    t = AnalyticsService(reg).get_dashboard(u, 30)["todos"]
    assert t["done"] == 1
    assert t["total"] == 3          # 1 done + 2 open
    assert t["overdue"] == 1        # only the past-due open one
    assert t["pct"] == 33


def test_habits_section(reg):
    u = _user(reg)
    hs = HabitService(reg, u)
    hs.add_habit("Gym")
    hs.log_habit("Gym")
    habits = AnalyticsService(reg).get_dashboard(u, 30)["habits"]
    assert len(habits) == 1
    g = habits[0]
    assert g["name"] == "Gym" and g["streak"] == 1
    assert len(g["series"]) == 30 and g["series"][-1] == 1   # logged today


def test_window_size_reflected_in_series(reg):
    u = _user(reg)
    for w in (7, 30, 90):
        d = AnalyticsService(reg).get_dashboard(u, w)
        assert d["window_days"] == w
        assert len(d["usage"]["daily"]) == w
        assert len(d["kpis"]["sessions_spark"]) == w


def test_insights_fallback_without_llm(reg, monkeypatch):
    u = _user(reg)
    reg.mark_todo_completed(reg.create_todo("d", user_id=u)["id"])
    HabitService(reg, u).add_habit("Gym")
    # Force the LLM path to fail → deterministic fallback.
    import app.core.analytics_service as mod
    monkeypatch.setattr(mod, "_INSIGHTS_CACHE", {})
    monkeypatch.setattr("app.providers.factory.create_default_chat_provider",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    out = AnalyticsService(reg).insights(u, 30)
    assert "todos" in out.lower() or "habit" in out.lower()
