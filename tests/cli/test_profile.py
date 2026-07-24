"""Tests for the shared profile service (build + delete)."""
import tempfile
from pathlib import Path

from app.core.habit_service import HabitService
from app.core.profile_service import build_profile, delete_profile_and_data
from app.storage.sqlite_registry import SQLiteRegistry


def _reg():
    return SQLiteRegistry(Path(tempfile.mkdtemp()) / "t.db")


def test_build_profile_fields():
    reg = _reg()
    u = reg.create_user("alice", "pw12345")["user_id"]
    sid = reg.get_or_create_named_session(f"cli:{u}:default", user_id=u)
    reg.append_turn(session_id=sid, turn_id="t0", role="user", content="hi", turn_index=0)
    HabitService(reg, u).add_habit("Gym")
    reg.create_todo("task", user_id=u)

    p = build_profile(reg, u, "alice")
    assert p["username"] == "alice"
    assert p["total_sessions"] >= 1
    assert p["days_active"] >= 1
    assert p["total_docs"] == 0
    assert set(p) >= {
        "username", "joined", "days_active", "total_sessions",
        "facts_personal", "facts_work", "longest_streak", "longest_streak_habit",
        "total_docs", "total_chunks",
    }


def test_delete_profile_and_data_wipes_account(monkeypatch):
    reg = _reg()
    u = reg.create_user("bob", "pw12345")["user_id"]
    sid = reg.get_or_create_named_session(f"cli:{u}:default", user_id=u)
    reg.append_turn(session_id=sid, turn_id="t0", role="user", content="hi", turn_index=0)
    reg.create_todo("task", user_id=u)

    # No vector store round-trip in the test.
    monkeypatch.setattr("app.core.profile_service.create_vector_store", lambda *a, **k: object())

    deleted = delete_profile_and_data(reg, u)

    assert isinstance(deleted, dict)
    assert reg.get_user_by_id(u) is None          # account row gone
    assert reg.list_sessions(limit=10, user_id=u) == []
