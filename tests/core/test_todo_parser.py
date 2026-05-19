from datetime import datetime

from app.core.todo_parser import parse_due_date


def test_parse_due_date_tomorrow_with_time_uses_tomorrow_date():
    now = datetime(2026, 5, 19, 10, 40)

    due_at = parse_due_date("tomorrow at 9:30 AM", now=now)

    assert due_at == datetime(2026, 5, 20, 9, 30)


def test_parse_due_date_today_with_time_keeps_today_date():
    now = datetime(2026, 5, 19, 10, 40)

    due_at = parse_due_date("today at 9pm", now=now)

    assert due_at == datetime(2026, 5, 19, 21, 0)

