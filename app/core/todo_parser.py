from datetime import datetime, timedelta
from typing import Optional
import re

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta


def parse_due_date(raw: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    if not raw:
        return None

    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    value = raw.strip().lower()
    if not value:
        return None

    if value == "today":
        return now
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "tonight":
        return today.replace(hour=21)
    if value == "tomorrow morning":
        return (today + timedelta(days=1)).replace(hour=9)
    if value == "tomorrow afternoon":
        return (today + timedelta(days=1)).replace(hour=13)
    if value == "tomorrow evening":
        return (today + timedelta(days=1)).replace(hour=18)
    if value == "tomorrow night":
        return (today + timedelta(days=1)).replace(hour=21)

    try:
        default = today
        parsed = date_parser.parse(value, fuzzy=True, default=default)
    except (ValueError, TypeError, OverflowError):
        return None

    if value.startswith("next ") and parsed <= now:
        parsed += relativedelta(weeks=1)
    return parsed


def parse_reminder_request(raw: str) -> Optional[tuple[str, Optional[datetime]]]:
    text = raw.strip().rstrip(".!?")
    match = re.match(r"^(?:please\s+)?remind me to\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return None

    body = match.group(1).strip()
    split = _split_task_and_due_date(body)
    if split is None:
        return body, None
    task, due_text = split
    return task, parse_due_date(due_text)


def _split_task_and_due_date(body: str) -> Optional[tuple[str, str]]:
    marker_matches = list(re.finditer(r"\s+(at|on|by)\s+", body, flags=re.IGNORECASE))
    if marker_matches:
        marker = marker_matches[-1]
        task = body[: marker.start()].strip()
        due_text = body[marker.end():].strip()
        if task and due_text:
            return task, due_text

    date_suffix = re.search(
        r"\s+((?:today|tonight|tomorrow)(?:\s+(?:morning|afternoon|evening|night))?|next\s+.+)$",
        body,
        flags=re.IGNORECASE,
    )
    if date_suffix:
        task = body[: date_suffix.start()].strip()
        due_text = date_suffix.group(1).strip()
        if task and due_text:
            return task, due_text

    return None
