"""Tests for the CLI HITL resolver (_resolve_hitl)."""
from datetime import datetime, timedelta, timezone

import app.agents.hitl_dispatch as hd
from app.cli.commands_chat import _resolve_hitl


class _FakeReg:
    def __init__(self, row):
        self.row = row
        self.resolved = None

    def get_hitl_request(self, hitl_id):
        return self.row

    def resolve_hitl_request(self, hitl_id, status):
        self.resolved = status


class _Res:
    def __init__(self, output):
        self.output = output


def _row(**over):
    base = {"id": "h1", "user_id": "u1", "status": "pending", "action_payload": {}}
    base.update(over)
    return base


def test_approve_executes_and_marks_approved(monkeypatch):
    reg = _FakeReg(_row())
    monkeypatch.setattr(hd, "execute_approved_by_type", lambda *a, **k: _Res("Marked 'gym' done today. 🎉"))
    out = _resolve_hitl(reg, "h1", "u1", approved=True)
    assert reg.resolved == "approved"
    assert "gym" in out


def test_reject_resolves_without_executing(monkeypatch):
    reg = _FakeReg(_row())
    called = {"ran": False}

    def _boom(*a, **k):
        called["ran"] = True
        return _Res("should not run")

    monkeypatch.setattr(hd, "execute_approved_by_type", _boom)
    out = _resolve_hitl(reg, "h1", "u1", approved=False)
    assert reg.resolved == "rejected"
    assert called["ran"] is False
    assert "cancelled" in out.lower()


def test_wrong_user_is_not_available():
    reg = _FakeReg(_row(user_id="someone_else"))
    out = _resolve_hitl(reg, "h1", "u1", approved=True)
    assert "no longer available" in out
    assert reg.resolved is None


def test_already_resolved():
    reg = _FakeReg(_row(status="approved"))
    out = _resolve_hitl(reg, "h1", "u1", approved=True)
    assert "already resolved" in out


def test_expired_request_is_marked_expired():
    reg = _FakeReg(_row(expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    out = _resolve_hitl(reg, "h1", "u1", approved=True)
    assert reg.resolved == "expired"
    assert "expired" in out


def test_continuation_output_appended_on_approve(monkeypatch):
    reg = _FakeReg(_row(action_payload={"__hitl_context": {"continuation_output": "Also: 2 todos left today."}}))
    monkeypatch.setattr(hd, "execute_approved_by_type", lambda *a, **k: _Res("Done."))
    out = _resolve_hitl(reg, "h1", "u1", approved=True)
    assert "Done." in out and "2 todos left today." in out
