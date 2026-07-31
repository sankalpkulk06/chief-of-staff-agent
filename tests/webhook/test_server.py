import asyncio
import uuid
from pathlib import Path

from app.storage.sqlite_registry import SQLiteRegistry
from app.webhook import server


class _Registry:
    def __init__(self, pending=None):
        self.pending = pending
        self.cleared = []
        self.updated = []
        self.sessions = []

    def get_nudge_context(self, phone):
        return self.pending

    def clear_nudge_context(self, phone):
        self.cleared.append(phone)

    def update_whatsapp_last_active(self, phone):
        self.updated.append(phone)

    def get_or_create_whatsapp_session(self, phone):
        self.sessions.append(phone)
        return "session"


class _HabitService:
    def __init__(self):
        self.logged = []

    def get_habit_by_id(self, habit_id):
        class Habit:
            id = habit_id
            name = "gym"
        return Habit()

    def log_habit_by_id(self, habit_id, status="done"):
        self.logged.append((habit_id, status))


class _ChatService:
    def __init__(self):
        self.calls = []

    def answer_in_session(self, session_id, question, response_style=None):
        self.calls.append((session_id, question, response_style))

        class Result:
            answer = "chat reply"

        return Result()


class _WhatsApp:
    def __init__(self):
        self.messages = []

    def send_message(self, to, body):
        self.messages.append((to, body))


class _FailingWhatsApp:
    def send_message(self, to, body):
        raise RuntimeError("Twilio limit")


def test_nudge_reply_logs_habit_and_skips_chat(monkeypatch):
    registry = _Registry(pending="habit-1")
    habit_service = _HabitService()
    chat_service = _ChatService()
    whatsapp = _WhatsApp()
    monkeypatch.setattr(server, "_registry", registry)
    monkeypatch.setattr(server, "_habit_service", habit_service)
    monkeypatch.setattr(server, "_chat_service", chat_service)
    monkeypatch.setattr(server, "_whatsapp_service", whatsapp)

    response = asyncio.run(server.webhook(From="whatsapp:+1", Body="done"))

    assert response.status_code == 200
    assert habit_service.logged == [("habit-1", "done")]
    assert registry.cleared == ["whatsapp:+1"]
    assert whatsapp.messages[0][1] == "Logged *gym* as done for today!"
    assert chat_service.calls == []


def test_unknown_nudge_reply_falls_through_to_chat(monkeypatch):
    registry = _Registry(pending="habit-1")
    habit_service = _HabitService()
    chat_service = _ChatService()
    whatsapp = _WhatsApp()
    monkeypatch.setattr(server, "_registry", registry)
    monkeypatch.setattr(server, "_habit_service", habit_service)
    monkeypatch.setattr(server, "_chat_service", chat_service)
    monkeypatch.setattr(server, "_whatsapp_service", whatsapp)

    response = asyncio.run(server.webhook(From="whatsapp:+1", Body="maybe later"))

    assert response.status_code == 200
    assert habit_service.logged == []
    assert chat_service.calls == [("session", "maybe later", "whatsapp")]
    assert whatsapp.messages == [("whatsapp:+1", "chat reply")]


def test_send_failure_still_returns_successful_webhook_response(monkeypatch):
    registry = _Registry()
    chat_service = _ChatService()
    monkeypatch.setattr(server, "_registry", registry)
    monkeypatch.setattr(server, "_habit_service", _HabitService())
    monkeypatch.setattr(server, "_chat_service", chat_service)
    monkeypatch.setattr(server, "_whatsapp_service", _FailingWhatsApp())

    response = asyncio.run(server.webhook(From="whatsapp:+1", Body="hello"))

    assert response.status_code == 200
    assert chat_service.calls == [("session", "hello", "whatsapp")]
    assert registry.updated == ["whatsapp:+1"]


# ---------------------------------------------------------------------------
# HITL resolution: a single yes/no must resolve the pending approval
# ---------------------------------------------------------------------------

class _RaisingChat:
    """Fails if the chat pipeline is reached — proves no re-proposal."""
    def answer_in_session(self, *a, **k):
        raise AssertionError("chat pipeline must not run when a pending approval exists")


class _OkChat:
    def __init__(self):
        self.calls = []

    def answer_in_session(self, session_id, question, response_style=None, user_id=None):
        self.calls.append(question)
        class Result:
            answer = "chat reply"
            hitl_pending = False
            hitl_id = None
        return Result()


def _seed_pending(reg, user_id, action="log_habit", payload=None):
    hid = str(uuid.uuid4())
    reg.create_hitl_request(id=hid, user_id=user_id, action_type=action,
                            action_payload=payload or {"name": "gym"})
    return hid


def _fake_executor(monkeypatch, recorder):
    class _Res:
        output = "Habit 'gym' logged as done for today."
        success = True
    def _exec(row, user_id, **k):
        recorder.append(row["id"])
        return _Res()
    monkeypatch.setattr("app.agents.hitl_dispatch.execute_approved_by_type", _exec)


def _wire(monkeypatch, reg, uid, chat):
    wa = _WhatsApp()
    monkeypatch.setattr(server, "_registry", reg)
    monkeypatch.setattr(server, "_whatsapp_user_id", uid)
    monkeypatch.setattr(server, "_whatsapp_service", wa)
    monkeypatch.setattr(server, "_chat_service", chat)
    return wa


def test_yes_resolves_via_fallback_when_pointer_missing(monkeypatch, tmp_path):
    reg = SQLiteRegistry(tmp_path / "r.db")
    uid = reg.create_user("sankalp", "pw12345")["user_id"]
    hid = _seed_pending(reg, uid)
    executed = []
    _fake_executor(monkeypatch, executed)
    wa = _wire(monkeypatch, reg, uid, _RaisingChat())   # no whatsapp_hitl_context set

    asyncio.run(server.webhook(From="whatsapp:+1", Body="yes"))

    assert executed == [hid]                                   # resolved via fallback
    assert reg.get_hitl_request(hid)["status"] == "approved"
    assert wa.messages and "logged" in wa.messages[0][1].lower()


def test_yes_resolves_via_pointer_when_present(monkeypatch, tmp_path):
    reg = SQLiteRegistry(tmp_path / "r.db")
    uid = reg.create_user("sankalp", "pw12345")["user_id"]
    hid = _seed_pending(reg, uid)
    reg.set_whatsapp_hitl_context("whatsapp:+1", hid)
    executed = []
    _fake_executor(monkeypatch, executed)
    _wire(monkeypatch, reg, uid, _RaisingChat())

    asyncio.run(server.webhook(From="whatsapp:+1", Body="yes"))

    assert executed == [hid]
    assert reg.get_hitl_request(hid)["status"] == "approved"


def test_no_rejects_pending_without_executing(monkeypatch, tmp_path):
    reg = SQLiteRegistry(tmp_path / "r.db")
    uid = reg.create_user("sankalp", "pw12345")["user_id"]
    hid = _seed_pending(reg, uid)
    executed = []
    _fake_executor(monkeypatch, executed)
    _wire(monkeypatch, reg, uid, _RaisingChat())

    asyncio.run(server.webhook(From="whatsapp:+1", Body="no"))

    assert executed == []                                      # reject never executes
    assert reg.get_hitl_request(hid)["status"] == "rejected"


def test_non_confirmation_text_falls_through_to_chat(monkeypatch, tmp_path):
    reg = SQLiteRegistry(tmp_path / "r.db")
    uid = reg.create_user("sankalp", "pw12345")["user_id"]
    _seed_pending(reg, uid)                                    # pending exists, but...
    chat = _OkChat()
    _wire(monkeypatch, reg, uid, chat)

    asyncio.run(server.webhook(From="whatsapp:+1", Body="what's the weather"))

    assert chat.calls == ["what's the weather"]               # not treated as an approval
