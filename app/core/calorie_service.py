"""Calorie counter — persistence, LLM estimation, and the clarify→confirm flow.

Storage mirrors :class:`app.core.habit_service.HabitService`: an append-only event
table (``calorie_entries``, one row per logged meal) with "today's total" derived at
read time via ``DATE(eaten_at) = today``. The daily budget lives in ``user_settings``
via :mod:`app.core.calorie_util`.

The conversational flow (estimate → maybe ask a follow-up → confirm → log) is a small
state machine in :func:`advance_calorie_flow`. It is driven from two entry points that
both stay stateless about the session: ``ActionAgent`` starts it, and ``ChatService``
persists the returned ``pending`` state in ``pending_prompts`` and feeds follow-up turns
back in. See docs in ``ChatService._maybe_continue_calorie``.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.agents.prompts import load

_ESTIMATE_SYSTEM = load("calorie_estimate")

MAX_CLARIFY_ROUNDS = 2   # follow-up questions before we force a best-effort estimate
MAX_CONFIRM_ROUNDS = 2   # ambiguous yes/no replies before we give up on confirming
_FALLBACK_CALORIES = 400  # last resort when the model returns no usable number

# Affirmative / negative detection for the confirm step. Kept deliberately tight so a
# question like "now what?" isn't read as "no".
_AFFIRM_EXACT = {"yes", "y", "yep", "yeah", "yup", "sure", "ok", "okay", "confirm",
                 "correct", "right", "do it", "log it", "please", "go ahead", "add it"}
_AFFIRM_PREFIX = ("yes", "yep", "yeah", "yup", "sure", "ok", "okay", "log it",
                  "do it", "go ahead", "add it", "correct", "confirm", "sounds good")
_NEG_EXACT = {"no", "n", "nope", "nah", "cancel", "stop", "don't", "dont", "skip"}
_NEG_PREFIX = ("nope", "nah", "cancel", "don't ", "dont ", "no ", "no,", "skip", "forget it")


def _norm(message: str) -> str:
    return (message or "").strip().lower().rstrip("!.? ")


def _is_affirmative(message: str) -> bool:
    t = _norm(message)
    return t in _AFFIRM_EXACT or t.startswith(_AFFIRM_PREFIX)


def _is_negative(message: str) -> bool:
    t = _norm(message)
    return t in _NEG_EXACT or t.startswith(_NEG_PREFIX)


class CalorieService:
    """Reads/writes ``calorie_entries``. Dual-backend, like ``HabitService``."""

    def __init__(self, registry: Any, user_id: str = ""):
        self._db = getattr(registry, "_connection", None) or getattr(registry, "_conn")
        self._is_postgres = hasattr(registry, "_conn")
        self._user_id = user_id

    def _execute(self, sql: str, params: tuple = ()):
        if self._is_postgres:
            sql = sql.replace("?", "%s")
            cursor = self._db.cursor()
            cursor.execute(sql, params)
            return cursor
        return self._db.execute(sql, params)

    def _commit(self) -> None:
        self._db.commit()

    @staticmethod
    def _date_from_db(value) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_entry(
        self, description: str, calories: int, items_json: Optional[str] = None,
        eaten_at: Optional[datetime] = None,
    ) -> dict:
        entry_id = str(uuid.uuid4())
        ts = (eaten_at or datetime.now()).isoformat()
        self._execute(
            "INSERT INTO calorie_entries (id, user_id, description, calories, items_json, eaten_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, self._user_id, description, int(calories), items_json, ts),
        )
        self._commit()
        return {"id": entry_id, "description": description, "calories": int(calories), "eaten_at": ts}

    def delete_entry(self, entry_id: str) -> bool:
        cur = self._execute(
            "DELETE FROM calorie_entries WHERE id = ? AND user_id = ?",
            (entry_id, self._user_id),
        )
        self._commit()
        return cur.rowcount > 0

    def delete_latest_today(self) -> Optional[dict]:
        """Delete today's most recent entry (for 'undo that'). Returns it, or None."""
        today = date.today().isoformat()
        row = self._execute(
            "SELECT id, description, calories FROM calorie_entries "
            "WHERE user_id = ? AND DATE(eaten_at) = ? ORDER BY eaten_at DESC LIMIT 1",
            (self._user_id, today),
        ).fetchone()
        if row is None:
            return None
        self._execute("DELETE FROM calorie_entries WHERE id = ?", (row["id"],))
        self._commit()
        return {"id": row["id"], "description": row["description"], "calories": int(row["calories"])}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def today_total(self) -> int:
        today = date.today().isoformat()
        row = self._execute(
            "SELECT COALESCE(SUM(calories), 0) AS total FROM calorie_entries "
            "WHERE user_id = ? AND DATE(eaten_at) = ?",
            (self._user_id, today),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0

    def remaining(self, budget: int) -> int:
        return int(budget) - self.today_total()

    def list_today(self) -> list[dict]:
        today = date.today().isoformat()
        rows = self._execute(
            "SELECT id, description, calories, eaten_at FROM calorie_entries "
            "WHERE user_id = ? AND DATE(eaten_at) = ? ORDER BY eaten_at ASC",
            (self._user_id, today),
        ).fetchall()
        return [
            {"id": r["id"], "description": r["description"],
             "calories": int(r["calories"]), "eaten_at": r["eaten_at"]}
            for r in rows
        ]

    def daily_totals(self, days: int = 7) -> list[dict]:
        """Per-day totals for the last ``days`` days, oldest first, zero-filled."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        rows = self._execute(
            "SELECT DATE(eaten_at) AS day, COALESCE(SUM(calories), 0) AS total "
            "FROM calorie_entries WHERE user_id = ? AND DATE(eaten_at) >= ? "
            "GROUP BY DATE(eaten_at)",
            (self._user_id, start.isoformat()),
        ).fetchall()
        totals = {self._date_from_db(r["day"]).isoformat(): int(r["total"]) for r in rows}
        return [
            {"day": (start + timedelta(days=i)).isoformat(),
             "total": totals.get((start + timedelta(days=i)).isoformat(), 0)}
            for i in range(days)
        ]


# ----------------------------------------------------------------------
# LLM estimation
# ----------------------------------------------------------------------

def estimate_calories(provider: Any, description: str, force: bool = False) -> dict:
    """Ask the model to estimate calories from a meal description.

    Returns a dict with ``status`` == ``"need_info"`` (+ ``question``) or ``"ready"``
    (+ ``dish``, ``calories``, ``items``, ``confidence``). On any parse failure returns
    a best-effort ``ready`` dict so the flow never hard-crashes on the user.
    """
    force_clause = (
        "IMPORTANT: You have already gathered enough detail. You MUST return "
        '"status": "ready" with your best-effort estimate now — do NOT ask another question.'
        if force else ""
    )
    system = _ESTIMATE_SYSTEM.replace("{force_clause}", force_clause)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Meal: {description}"},
    ]
    try:
        response = provider.chat(messages=messages)
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("no JSON in estimate response")
        data = json.loads(match.group())
    except Exception:
        return {"status": "ready", "dish": description, "calories": _FALLBACK_CALORIES,
                "items": None, "confidence": "low"}

    status = data.get("status")
    if status == "need_info" and not force and data.get("question"):
        return {"status": "need_info", "question": str(data["question"]).strip()}

    calories = _coerce_calories(data)
    return {
        "status": "ready",
        "dish": (data.get("dish") or description).strip(),
        "calories": calories,
        "items": data.get("items") if isinstance(data.get("items"), list) else None,
        "confidence": data.get("confidence", "medium"),
    }


def _coerce_calories(data: dict) -> int:
    raw = data.get("calories")
    try:
        value = int(float(raw))
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    items = data.get("items")
    if isinstance(items, list):
        total = 0
        for item in items:
            try:
                total += int(float(item.get("calories", 0)))
            except (TypeError, ValueError, AttributeError):
                continue
        if total > 0:
            return total
    return _FALLBACK_CALORIES


# ----------------------------------------------------------------------
# Conversational state machine (session-agnostic; caller persists `pending`)
# ----------------------------------------------------------------------

def advance_calorie_flow(
    provider: Any,
    calorie_service: Optional[CalorieService],
    budget: int,
    state: Optional[dict],
    message: str,
) -> dict:
    """Advance one turn of the log-a-meal conversation.

    ``state`` is ``None`` for the first turn, or the ``pending`` dict returned by a prior
    call. Returns ``{"reply", "pending", "logged", "entry"}`` where ``pending`` is either
    the next state to persist or ``None`` when the conversation is over.
    """
    state = state or {}
    stage = state.get("stage")

    if stage == "confirming":
        return _handle_confirm(calorie_service, budget, state, message)

    # Fresh turn or a clarifying follow-up: accumulate the description.
    prior = state.get("description", "")
    addition = (message or "").strip()
    description = f"{prior}; {addition}".strip("; ").strip() if prior else addition
    rounds = int(state.get("clarify_rounds", 0))
    force = rounds >= MAX_CLARIFY_ROUNDS

    est = estimate_calories(provider, description, force=force)
    if est.get("status") == "need_info" and not force:
        question = est.get("question") or "Could you tell me the rough amount you had?"
        return {
            "reply": f"🍽️ {question}",
            "pending": {"stage": "clarifying", "description": description,
                        "clarify_rounds": rounds + 1},
            "logged": False,
            "entry": None,
        }

    calories = est.get("calories") or _FALLBACK_CALORIES
    dish = est.get("dish") or description
    items_json = json.dumps(est["items"]) if est.get("items") else None
    return {
        "reply": _confirm_reply(dish, calories),
        "pending": {"stage": "confirming", "description": description, "dish": dish,
                    "calories": int(calories), "items_json": items_json, "confirm_attempts": 0},
        "logged": False,
        "entry": None,
    }


def _handle_confirm(
    calorie_service: Optional[CalorieService], budget: int, state: dict, message: str,
) -> dict:
    dish = state.get("dish") or state.get("description") or "that meal"
    calories = int(state.get("calories", 0))

    if _is_negative(message):
        return {"reply": "No worries — I didn't log that. 🙂", "pending": None,
                "logged": False, "entry": None}

    if _is_affirmative(message):
        if calorie_service is None:
            return {"reply": "I couldn't log that right now — the calorie store isn't available.",
                    "pending": None, "logged": False, "entry": None}
        entry = calorie_service.add_entry(
            state.get("description", dish), calories, items_json=state.get("items_json")
        )
        total = calorie_service.today_total()
        remaining = int(budget) - total
        return {"reply": _logged_reply(dish, calories, total, budget, remaining),
                "pending": None, "logged": True, "entry": entry}

    # Ambiguous reply — re-ask once, then give up so we never trap the conversation.
    attempts = int(state.get("confirm_attempts", 0)) + 1
    if attempts >= MAX_CONFIRM_ROUNDS:
        return {"reply": "Okay, I'll leave that one out for now.", "pending": None,
                "logged": False, "entry": None}
    return {
        "reply": f"Just to confirm — log *{dish}* at about *{calories} cal*? Reply *yes* or *no*.",
        "pending": {**state, "confirm_attempts": attempts},
        "logged": False,
        "entry": None,
    }


def _confirm_reply(dish: str, calories: int) -> str:
    return (f"🍽️ *{dish}* is about *{calories} cal*.\n"
            "Reply *yes* to log it, or tell me what to adjust.")


def _logged_reply(dish: str, calories: int, total: int, budget: int, remaining: int) -> str:
    head = f"✅ Logged *{dish}* ({calories} cal). Today: *{total}/{budget} cal*"
    if remaining >= 0:
        return f"{head} — *{remaining} left*. 🎯"
    return f"{head} — *{abs(remaining)} over*. ⚠️"
