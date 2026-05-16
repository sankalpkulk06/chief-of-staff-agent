from datetime import datetime, timedelta

from app.core.habit_service import HabitService
from app.scheduler.scheduler import (
    build_scheduler,
    check_habits_and_nudge,
    scan_due_todos,
    send_morning_briefing,
)
from app.services.news_service import NewsArticle
from app.storage.sqlite_registry import SQLiteRegistry


class _WhatsApp:
    def __init__(self):
        self.messages = []

    def send_message(self, to, body):
        self.messages.append((to, body))


class _FailingWhatsApp:
    def send_message(self, to, body):
        raise RuntimeError("Twilio limit")


class _News:
    def get_top_news(self, max_results=None):
        return [
            NewsArticle(
                title="OpenAI releases update",
                source="Example",
                url="https://example.com",
                published="today",
            )
        ][:max_results]


def test_build_scheduler_registers_jobs(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        scheduler = build_scheduler(
            habit_service=HabitService(registry),
            whatsapp_service=_WhatsApp(),
            news_service=_News(),
            registry=registry,
            your_number="whatsapp:+1",
        )
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert {"habit_nudges", "morning_briefing", "due_todo_scan"} <= job_ids
        assert not scheduler.running
    finally:
        registry.close()


def test_scan_due_todos_sends_and_marks_notified(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    whatsapp = _WhatsApp()
    try:
        todo = registry.create_todo(
            "Take trash out",
            due_at=datetime.now() - timedelta(minutes=1),
        )

        scan_due_todos(whatsapp, registry, "whatsapp:+1")

        stored = registry.get_todo(todo["id"])
        assert whatsapp.messages == [("whatsapp:+1", "Reminder: *Take trash out* is due now.")]
        assert stored["notified_at"] is not None
    finally:
        registry.close()


def test_scan_due_todos_does_not_mark_notified_when_send_fails(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        todo = registry.create_todo(
            "Take trash out",
            due_at=datetime.now() - timedelta(minutes=1),
        )

        scan_due_todos(_FailingWhatsApp(), registry, "whatsapp:+1")

        stored = registry.get_todo(todo["id"])
        assert stored["notified_at"] is None
    finally:
        registry.close()


def test_habit_nudge_sends_message_and_context(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    whatsapp = _WhatsApp()
    try:
        habit_service = HabitService(registry)
        habit = habit_service.add_habit("gym")

        check_habits_and_nudge(habit_service, whatsapp, registry, "whatsapp:+1")

        assert len(whatsapp.messages) == 1
        assert "gym" in whatsapp.messages[0][1]
        assert registry.get_nudge_context("whatsapp:+1") == habit.id
    finally:
        registry.close()


def test_morning_briefing_includes_habits_news_and_todos(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    whatsapp = _WhatsApp()
    try:
        habit_service = HabitService(registry)
        habit_service.add_habit("reading")
        registry.create_todo("Call dentist", due_at=datetime.now() + timedelta(hours=3))

        send_morning_briefing(habit_service, whatsapp, _News(), registry, "whatsapp:+1")

        body = whatsapp.messages[0][1]
        assert "reading" in body
        assert "OpenAI releases update" in body
        assert "Call dentist" in body
    finally:
        registry.close()
