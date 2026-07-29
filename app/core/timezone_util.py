"""Per-user timezone helpers.

The rest of the app is timezone-naive (server-local ``datetime.now()``). The
daily-planner feature needs tz-aware RFC3339 datetimes for Google Calendar, so
all timezone logic is isolated here rather than threaded through the codebase.

A user's IANA timezone lives in the ``user_settings`` k/v table under the key
``"timezone"`` (no schema column). When unset, we fall back to
``settings.default_timezone`` (default ``"UTC"``).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_SETTING_KEY = "timezone"


def is_valid_timezone(name: str) -> bool:
    """True if ``name`` is a resolvable IANA timezone (e.g. 'America/New_York')."""
    if not name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def get_user_timezone_name(registry: Any, user_id: str, default: str = "UTC") -> str:
    """Return the user's stored IANA timezone name, or ``default`` if unset/invalid."""
    stored: Optional[str] = None
    try:
        stored = registry.get_user_setting(user_id, TIMEZONE_SETTING_KEY)
    except Exception:
        stored = None
    if stored and is_valid_timezone(stored):
        return stored
    return default


def set_user_timezone(registry: Any, user_id: str, name: str) -> None:
    """Persist the user's IANA timezone. Raises ValueError on an invalid name."""
    if not is_valid_timezone(name):
        raise ValueError(f"Invalid IANA timezone: {name!r}")
    registry.set_user_setting(user_id, TIMEZONE_SETTING_KEY, name)


def resolve_tz(registry: Any, user_id: str, default: str = "UTC") -> ZoneInfo:
    """Resolve the user's timezone to a ``ZoneInfo`` (never raises; falls back to UTC)."""
    name = get_user_timezone_name(registry, user_id, default=default)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def now_local(tz: ZoneInfo) -> datetime:
    """Current time as a tz-aware datetime in ``tz``."""
    return datetime.now(tz)


def local_now(registry: Any, user_id: str, default: str = "UTC") -> datetime:
    """Current wall-clock time in the user's timezone, as a NAIVE datetime.

    The rest of the app stores/compares naive local timestamps, so this returns the
    user's local wall-clock with tzinfo stripped — suitable for stamping ``eaten_at`` /
    ``logged_at`` and for building day boundaries that line up with the user's calendar day.
    """
    return datetime.now(resolve_tz(registry, user_id, default=default)).replace(tzinfo=None)


def local_today(registry: Any, user_id: str, default: str = "UTC") -> date:
    """The user's current calendar date (in their timezone), not the server's."""
    return datetime.now(resolve_tz(registry, user_id, default=default)).date()


def describe_now(registry: Any = None, user_id: str = "", default: str = "UTC") -> str:
    """Human-readable current date, day, and time in the user's timezone.

    Single source of truth for "what time/date is it" — used both by the
    ``get_current_datetime`` tool and as ambient context injected into agent
    prompts. Never raises: falls back to the server's local clock if the
    user's timezone can't be resolved.

    Example: ``"Thursday, July 23, 2026 at 10:02 PM PDT (America/Los_Angeles)"``.
    """
    tz: Optional[ZoneInfo] = None
    if registry is not None:
        tz = resolve_tz(registry, user_id or "", default=default)
    elif is_valid_timezone(default):
        tz = ZoneInfo(default)

    now = datetime.now(tz) if tz is not None else datetime.now()
    stamp = now.strftime("%A, %B %-d, %Y at %-I:%M %p")
    zone_abbrev = now.strftime("%Z")
    if zone_abbrev:
        stamp += f" {zone_abbrev}"
    if tz is not None:
        stamp += f" ({tz.key})"
    return stamp


def to_rfc3339(dt: datetime, tz: Optional[ZoneInfo] = None) -> str:
    """Serialize a datetime to an RFC3339 string with offset.

    A naive ``dt`` is interpreted as wall-clock time in ``tz`` (required in that
    case). Wall-clock interpretation via ``ZoneInfo`` handles DST correctly.
    """
    if dt.tzinfo is None:
        if tz is None:
            raise ValueError("naive datetime requires a tz to localize")
        dt = dt.replace(tzinfo=tz)
    return dt.isoformat()


def local_datetime(plan_date: date, hhmm: str, tz: ZoneInfo) -> datetime:
    """Build a tz-aware datetime from a date + 'HH:MM' wall-clock string in ``tz``.

    DST-safe: attaching ``ZoneInfo`` derives the correct UTC offset for that
    wall-clock instant rather than assuming a fixed offset.
    """
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    return datetime.combine(plan_date, time(hour=hour, minute=minute), tzinfo=tz)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 / ISO-8601 string into a tz-aware datetime.

    Accepts a trailing 'Z' (UTC). A value with no offset is returned naive —
    callers that require awareness should localize it themselves.
    """
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def day_bounds_rfc3339(plan_date: date, tz: ZoneInfo) -> tuple[str, str]:
    """RFC3339 [start, end) covering the whole local day — for calendar time-window queries."""
    start = datetime.combine(plan_date, time(0, 0), tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()
