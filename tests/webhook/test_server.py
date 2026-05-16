import asyncio

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
