"""Shared helpers for backdating user-logged events (habits, calories, ...).

Resolving a natural-language day ("yesterday", "on Jul 27") to a date is done by the
LLM (which is good at it when given today's date); these helpers only VALIDATE the
resulting ISO date and render it for confirmation text — that stays in Python so a bad
or out-of-range model value can never write a wrong row.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

DEFAULT_MAX_BACKDATE_DAYS = 31


def resolve_backdate_iso(raw, *, max_days: int = DEFAULT_MAX_BACKDATE_DAYS) -> Optional[str]:
    """Return a validated 'YYYY-MM-DD' for a genuine past date, else None (= today/default).

    Rejects non-dates, today, future dates, and anything older than ``max_days``.
    """
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(str(raw).strip()[:10])
    except (TypeError, ValueError):
        return None
    today = date.today()
    if parsed >= today or parsed < today - timedelta(days=max_days):
        return None
    return parsed.isoformat()


def friendly_day(iso: Optional[str]) -> str:
    """'yesterday' / 'Jul 27' label for a backdated day, or '' when it's today/unset."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if d == date.today() - timedelta(days=1):
        return "yesterday"
    return d.strftime("%b %-d")
