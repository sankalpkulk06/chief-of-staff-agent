"""Per-user daily calorie budget helpers.

The budget is a single integer preference, so it lives in the ``user_settings``
k/v table under the key ``"calorie_budget"`` (no schema column) — the same
pattern as :mod:`app.core.timezone_util`. When unset, callers fall back to
``DEFAULT_CALORIE_BUDGET``.
"""
from __future__ import annotations

from typing import Any, Optional

CALORIE_BUDGET_KEY = "calorie_budget"
DEFAULT_CALORIE_BUDGET = 2000
MIN_CALORIE_BUDGET = 500
MAX_CALORIE_BUDGET = 20000


def get_calorie_budget(registry: Any, user_id: str, default: int = DEFAULT_CALORIE_BUDGET) -> int:
    """Return the user's stored daily calorie budget, or ``default`` if unset/invalid."""
    stored: Optional[str] = None
    try:
        stored = registry.get_user_setting(user_id, CALORIE_BUDGET_KEY)
    except Exception:
        stored = None
    if stored is None:
        return default
    try:
        return int(float(stored))
    except (TypeError, ValueError):
        return default


def set_calorie_budget(registry: Any, user_id: str, value: int) -> int:
    """Persist the user's daily calorie budget (clamped to a sane range). Returns the stored value."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Calorie budget must be a number, got {value!r}")
    if value < MIN_CALORIE_BUDGET or value > MAX_CALORIE_BUDGET:
        raise ValueError(
            f"Calorie budget must be between {MIN_CALORIE_BUDGET} and {MAX_CALORIE_BUDGET}."
        )
    registry.set_user_setting(user_id, CALORIE_BUDGET_KEY, str(value))
    return value


def has_calorie_budget(registry: Any, user_id: str) -> bool:
    """True if the user has explicitly set a budget (vs. relying on the default)."""
    try:
        return registry.get_user_setting(user_id, CALORIE_BUDGET_KEY) is not None
    except Exception:
        return False
