import logging
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.habit_service import HabitService
from app.services.news_service import NewsService
from app.services.whatsapp_service import WhatsAppService
from app.storage.sqlite_registry import SQLiteRegistry

logger = logging.getLogger(__name__)


def build_scheduler(
    habit_service: HabitService,
    whatsapp_service: WhatsAppService,
    news_service: NewsService,
    registry: SQLiteRegistry,
    your_number: str,
    morning_briefing_time: str = "08:00",
    habit_nudge_time: str = "21:00",
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": None,
        }
    )

    habit_hour, habit_minute = _parse_hhmm(habit_nudge_time)
    scheduler.add_job(
        check_habits_and_nudge,
        "cron",
        hour=habit_hour,
        minute=habit_minute,
        args=[habit_service, whatsapp_service, registry, your_number],
        id="habit_nudges",
        replace_existing=True,
    )

    briefing_hour, briefing_minute = _parse_hhmm(morning_briefing_time)
    scheduler.add_job(
        send_morning_briefing,
        "cron",
        hour=briefing_hour,
        minute=briefing_minute,
        args=[habit_service, whatsapp_service, news_service, registry, your_number],
        id="morning_briefing",
        replace_existing=True,
    )

    scheduler.add_job(
        scan_due_todos,
        "interval",
        minutes=1,
        args=[whatsapp_service, registry, your_number],
        id="due_todo_scan",
        replace_existing=True,
    )

    rehydrate_todo_jobs(scheduler, whatsapp_service, registry, your_number)
    return scheduler


def schedule_todo_reminder(
    scheduler: BackgroundScheduler,
    todo: dict[str, Any],
    whatsapp_service: WhatsAppService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    due_at = _coerce_datetime(todo.get("due_at"))
    if due_at is None:
        return

    if due_at <= datetime.now():
        send_todo_reminder(todo["id"], whatsapp_service, registry, your_number)
        return

    scheduler.add_job(
        send_todo_reminder,
        "date",
        run_date=due_at,
        args=[todo["id"], whatsapp_service, registry, your_number],
        id=f"todo:{todo['id']}",
        replace_existing=True,
    )


def rehydrate_todo_jobs(
    scheduler: BackgroundScheduler,
    whatsapp_service: WhatsAppService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    for todo in registry.get_pending_todos():
        schedule_todo_reminder(scheduler, todo, whatsapp_service, registry, your_number)


def send_todo_reminder(
    todo_id: str,
    whatsapp_service: WhatsAppService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    todo = registry.get_todo(todo_id)
    if not todo or todo.get("completed_at") or todo.get("notified_at"):
        return

    if not _safe_send(
        whatsapp_service,
        your_number,
        f"Reminder: *{todo['title']}* is due now.",
    ):
        return
    registry.mark_todo_notified(todo_id)


def scan_due_todos(
    whatsapp_service: WhatsAppService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    for todo in registry.get_todos_due_soon(minutes_ahead=0):
        send_todo_reminder(todo["id"], whatsapp_service, registry, your_number)


def check_habits_and_nudge(
    habit_service: HabitService,
    whatsapp_service: WhatsAppService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    for habit in habit_service.get_unlogged_today():
        registry.set_nudge_context(your_number, habit.id)
        sent = _safe_send(
            whatsapp_service,
            your_number,
            (
                f"Hey - you haven't logged *{habit.name}* today. Still happening?\n\n"
                "Reply 'done' or 'skipped' to log it."
            ),
        )
        if not sent:
            registry.clear_nudge_context(your_number)


def send_morning_briefing(
    habit_service: HabitService,
    whatsapp_service: WhatsAppService,
    news_service: NewsService,
    registry: SQLiteRegistry,
    your_number: str,
) -> None:
    lines = ["Good morning!", "", "Today's Habits"]
    summaries = habit_service.get_weekly_summary()
    if summaries:
        for summary in summaries:
            streak = f" ({summary.streak}-day streak)" if summary.streak else ""
            status = "logged" if summary.logged_today else "pending"
            lines.append(f"- {summary.habit.name}: {status}{streak}")
    else:
        lines.append("- No habits tracked yet")

    lines.extend(["", "Top News"])
    articles = news_service.get_top_news(max_results=3)
    if articles:
        for index, article in enumerate(articles, 1):
            lines.append(f"- [{index}] {article.title} - {article.source}")
    else:
        lines.append("- No news available")

    lines.extend(["", "Due Today"])
    due_todos = registry.get_todos_due_soon(minutes_ahead=1440)
    if due_todos:
        for todo in due_todos:
            due_label = _format_due_label(todo.get("due_at"))
            lines.append(f"- {todo['title']}{due_label}")
    else:
        lines.append("- Nothing due")

    lines.extend(["", "Have a great day!"])
    _safe_send(whatsapp_service, your_number, "\n".join(lines))


def _parse_hhmm(raw: str) -> tuple[int, int]:
    hour, minute = raw.split(":", 1)
    return int(hour), int(minute)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _format_due_label(value: Any) -> str:
    due_at = _coerce_datetime(value)
    if due_at is None:
        return ""
    return f" ({due_at.strftime('%-I:%M %p')})"


def _safe_send(whatsapp_service: WhatsAppService, to: str, body: str) -> bool:
    try:
        whatsapp_service.send_message(to=to, body=body)
        return True
    except Exception:
        logger.exception("Failed to send WhatsApp message to %s", to)
        return False
