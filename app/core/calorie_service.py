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
MAX_BACKDATE_DAYS = 31   # how far back a meal may be logged
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
        self._registry = registry
        self._db = getattr(registry, "_connection", None) or getattr(registry, "_conn")
        self._is_postgres = hasattr(registry, "_conn")
        self._user_id = user_id

    def _today(self) -> date:
        """The user's current calendar date (their timezone), not the server's."""
        from app.core import timezone_util as tzu
        from app.config.settings import get_settings
        return tzu.local_today(self._registry, self._user_id, default=get_settings().default_timezone)

    def _now(self) -> datetime:
        """Current wall-clock time in the user's timezone (naive, for storage)."""
        from app.core import timezone_util as tzu
        from app.config.settings import get_settings
        return tzu.local_now(self._registry, self._user_id, default=get_settings().default_timezone)

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
        eaten_at: Optional[datetime] = None, kind: str = "intake",
        dish: Optional[str] = None, protein_g=0, carbs_g=0, fat_g=0,
    ) -> dict:
        entry_id = str(uuid.uuid4())
        ts = (eaten_at or self._now()).isoformat()
        kind = "burned" if kind == "burned" else "intake"
        self._execute(
            "INSERT INTO calorie_entries "
            "(id, user_id, description, dish, calories, kind, protein_g, carbs_g, fat_g, items_json, eaten_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, self._user_id, description, dish, int(calories), kind,
             float(protein_g or 0), float(carbs_g or 0), float(fat_g or 0), items_json, ts),
        )
        self._commit()
        return {"id": entry_id, "description": description, "dish": dish,
                "calories": int(calories), "kind": kind, "eaten_at": ts,
                "protein_g": protein_g, "carbs_g": carbs_g, "fat_g": fat_g}

    def delete_entry(self, entry_id: str) -> bool:
        cur = self._execute(
            "DELETE FROM calorie_entries WHERE id = ? AND user_id = ?",
            (entry_id, self._user_id),
        )
        self._commit()
        return cur.rowcount > 0

    def delete_latest_today(self) -> Optional[dict]:
        """Delete today's most recent entry (for 'undo that'). Returns it, or None."""
        today = self._today().isoformat()
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

    def total_for(self, day: date, kind: str = "intake") -> int:
        row = self._execute(
            "SELECT COALESCE(SUM(calories), 0) AS total FROM calorie_entries "
            "WHERE user_id = ? AND DATE(eaten_at) = ? AND kind = ?",
            (self._user_id, day.isoformat(), kind),
        ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0

    def today_total(self) -> int:
        """Calories eaten today (intake)."""
        return self.total_for(self._today(), "intake")

    def today_burned(self) -> int:
        """Calories burned in workouts today."""
        return self.total_for(self._today(), "burned")

    def today_macros(self) -> dict:
        """Today's total protein/carbs/fat grams from eaten (intake) entries."""
        today = self._today().isoformat()
        row = self._execute(
            "SELECT COALESCE(SUM(protein_g),0) AS p, COALESCE(SUM(carbs_g),0) AS c, "
            "COALESCE(SUM(fat_g),0) AS f FROM calorie_entries "
            "WHERE user_id = ? AND DATE(eaten_at) = ? AND kind = 'intake'",
            (self._user_id, today),
        ).fetchone()
        return {"protein": round(float(row["p"] or 0)),
                "carbs": round(float(row["c"] or 0)),
                "fat": round(float(row["f"] or 0))}

    def remaining(self, budget: int) -> int:
        return int(budget) - self.today_total()

    def list_today(self, kind: Optional[str] = None) -> list[dict]:
        """Today's entries with dish, item breakdown, and macros. ``kind`` filters to
        'intake' or 'burned'; None returns both."""
        today = self._today().isoformat()
        sql = ("SELECT id, description, dish, calories, kind, protein_g, carbs_g, fat_g, "
               "items_json, eaten_at FROM calorie_entries WHERE user_id = ? AND DATE(eaten_at) = ?")
        params: tuple = (self._user_id, today)
        if kind in ("intake", "burned"):
            sql += " AND kind = ?"
            params = (self._user_id, today, kind)
        sql += " ORDER BY eaten_at ASC"
        rows = self._execute(sql, params).fetchall()

        def _items(raw):
            if not raw:
                return []
            try:
                data = json.loads(raw)
                return [{"name": str(i.get("name", "")), "calories": int(i.get("calories", 0) or 0)}
                        for i in data if isinstance(i, dict)]
            except Exception:
                return []

        out = []
        for r in rows:
            keys = r.keys()
            out.append({
                "id": r["id"],
                "description": r["description"],
                "dish": (r["dish"] if "dish" in keys and r["dish"] else None),
                "calories": int(r["calories"]),
                "kind": (r["kind"] if "kind" in keys else "intake"),
                "protein_g": round(float(r["protein_g"] or 0)) if "protein_g" in keys else 0,
                "carbs_g": round(float(r["carbs_g"] or 0)) if "carbs_g" in keys else 0,
                "fat_g": round(float(r["fat_g"] or 0)) if "fat_g" in keys else 0,
                "items": _items(r["items_json"] if "items_json" in keys else None),
                "eaten_at": r["eaten_at"],
            })
        return out

    def daily_totals(self, days: int = 7) -> list[dict]:
        """Per-day intake + burned for the last ``days`` days, oldest first, zero-filled.

        Each item is ``{day, intake, burned, net, total}`` where ``total`` == ``intake``
        (kept for back-compat) and ``net`` == intake - burned.
        """
        today = self._today()
        start = today - timedelta(days=days - 1)
        rows = self._execute(
            "SELECT DATE(eaten_at) AS day, kind, COALESCE(SUM(calories), 0) AS total "
            "FROM calorie_entries WHERE user_id = ? AND DATE(eaten_at) >= ? "
            "GROUP BY DATE(eaten_at), kind",
            (self._user_id, start.isoformat()),
        ).fetchall()
        intake: dict = {}
        burned: dict = {}
        for r in rows:
            day = self._date_from_db(r["day"]).isoformat()
            k = r["kind"] if "kind" in r.keys() else "intake"
            (burned if k == "burned" else intake)[day] = int(r["total"])
        out = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            ins, brn = intake.get(d, 0), burned.get(d, 0)
            out.append({"day": d, "intake": ins, "burned": brn, "net": ins - brn, "total": ins})
        return out


# ----------------------------------------------------------------------
# LLM estimation
# ----------------------------------------------------------------------

def estimate_calories(provider: Any, description: str, force: bool = False,
                      today: Optional[date] = None) -> dict:
    """Estimate calories + macros for a meal. Prefers native tool-calling (a forced
    ``record_meal`` structured call — no parse failures); falls back to prompt-for-JSON."""
    from app.config.settings import get_settings
    from app.providers.tool_types import supports_tools
    today = today or date.today()
    if get_settings().tool_calling_enabled and supports_tools(provider):
        try:
            return _estimate_calories_tools(provider, description, force, today)
        except Exception:
            pass
    return _estimate_calories_legacy(provider, description, force, today)


def _estimate_calories_tools(provider: Any, description: str, force: bool, today: date) -> dict:
    from app.providers.tool_types import Tool
    record = Tool(
        name="record_meal",
        description="Record the calorie + macro estimate for the meal, broken into items.",
        parameters={"type": "object", "properties": {
            "dish": {"type": "string", "description": "short human name for the meal"},
            "calories": {"type": "integer", "description": "total kcal for everything described"},
            "items": {"type": "array", "description": "component breakdown",
                      "items": {"type": "object", "properties": {
                          "name": {"type": "string"}, "calories": {"type": "integer"}}}},
            "protein_g": {"type": "integer"}, "carbs_g": {"type": "integer"}, "fat_g": {"type": "integer"},
            "eaten_on": {"type": "string", "description": "YYYY-MM-DD only if the user named a past day"},
        }, "required": ["dish", "calories"]},
    )
    tools = [record]
    if not force:
        tools.append(Tool(
            name="ask_followup",
            description="Ask ONE short follow-up when the meal is too vague to estimate.",
            parameters={"type": "object", "properties": {"question": {"type": "string"}},
                        "required": ["question"]}))
    system = (
        f"You estimate calories and macros for a meal. Today is {today.isoformat()}. "
        "Resolve any named day (yesterday / on Jul 27) into eaten_on as YYYY-MM-DD. Break the meal "
        "into items. If it is too vague to estimate and you may ask, call ask_followup; otherwise "
        "call record_meal with your best estimate.")
    result = provider.chat_tools(
        [{"role": "system", "content": system}, {"role": "user", "content": f"Meal: {description}"}],
        tools, tool_choice="required")
    if not result.tool_calls:
        raise ValueError("no tool call")
    tc = result.tool_calls[0]
    if tc.name == "ask_followup" and not force:
        q = (tc.arguments or {}).get("question")
        if q:
            return {"status": "need_info", "question": str(q).strip()}
        raise ValueError("empty follow-up question")
    a = tc.arguments or {}
    return {
        "status": "ready",
        "dish": (a.get("dish") or description).strip(),
        "calories": _coerce_calories(a),
        "items": a.get("items") if isinstance(a.get("items"), list) else None,
        "protein_g": _num(a.get("protein_g")), "carbs_g": _num(a.get("carbs_g")), "fat_g": _num(a.get("fat_g")),
        "eaten_on": _resolve_eaten_on(a.get("eaten_on"), today=today),
        "confidence": "medium",
    }


def _estimate_calories_legacy(provider: Any, description: str, force: bool = False,
                              today: Optional[date] = None) -> dict:
    """Prompt-for-JSON fallback for calorie/macro estimation."""
    force_clause = (
        "IMPORTANT: You have already gathered enough detail. You MUST return "
        '"status": "ready" with your best-effort estimate now — do NOT ask another question.'
        if force else ""
    )
    system = (
        _ESTIMATE_SYSTEM
        .replace("{force_clause}", force_clause)
        .replace("{today}", (today or date.today()).isoformat())
    )
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
                "items": None, "protein_g": 0, "carbs_g": 0, "fat_g": 0,
                "eaten_on": None, "confidence": "low"}

    status = data.get("status")
    if status == "need_info" and not force and data.get("question"):
        return {"status": "need_info", "question": str(data["question"]).strip()}

    calories = _coerce_calories(data)
    return {
        "status": "ready",
        "dish": (data.get("dish") or description).strip(),
        "calories": calories,
        "items": data.get("items") if isinstance(data.get("items"), list) else None,
        "protein_g": _num(data.get("protein_g")),
        "carbs_g": _num(data.get("carbs_g")),
        "fat_g": _num(data.get("fat_g")),
        "eaten_on": _resolve_eaten_on(data.get("eaten_on"), today=today),
        "confidence": data.get("confidence", "medium"),
    }


def _num(x) -> int:
    try:
        return max(0, int(round(float(x))))
    except (TypeError, ValueError):
        return 0


def _resolve_eaten_on(raw, today: Optional[date] = None) -> Optional[str]:
    """Validate a model-supplied 'YYYY-MM-DD'. Accept only dates within the allowed
    backdating window (not in the future, at most MAX_BACKDATE_DAYS ago); else None (today)."""
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(str(raw).strip()[:10])
    except (TypeError, ValueError):
        return None
    today = today or date.today()
    if parsed > today or parsed < today - timedelta(days=MAX_BACKDATE_DAYS):
        return None
    if parsed == today:
        return None
    return parsed.isoformat()


_BURN_ESTIMATE_SYSTEM = (
    "You estimate how many calories a person burned in a workout or activity.\n"
    "Return ONLY JSON: {\"calories\": <integer kcal burned>, \"activity\": \"<short name>\"}.\n"
    "Base it on typical values for the described activity, duration, and intensity. "
    "If unsure, give a reasonable middle estimate. Never return 0."
)


def estimate_burn(provider: Any, description: str) -> dict:
    """Estimate calories burned from a workout description. Returns {calories, activity}.
    Prefers a forced ``record_burn`` tool call; falls back to prompt-for-JSON."""
    from app.config.settings import get_settings
    from app.providers.tool_types import Tool, supports_tools
    if get_settings().tool_calling_enabled and supports_tools(provider):
        try:
            tool = Tool(
                name="record_burn",
                description="Record the calories burned in the described activity.",
                parameters={"type": "object", "properties": {
                    "calories": {"type": "integer", "description": "kcal burned"},
                    "activity": {"type": "string", "description": "short activity name"}},
                    "required": ["calories"]})
            result = provider.chat_tools(
                [{"role": "system", "content": _BURN_ESTIMATE_SYSTEM},
                 {"role": "user", "content": f"Activity: {description}"}],
                [tool], tool_choice="required")
            if result.tool_calls:
                a = result.tool_calls[0].arguments or {}
                cals = _num(a.get("calories"))
                return {"calories": cals if cals > 0 else 200,
                        "activity": (a.get("activity") or description).strip()}
        except Exception:
            pass
    return _estimate_burn_legacy(provider, description)


def _estimate_burn_legacy(provider: Any, description: str) -> dict:
    messages = [
        {"role": "system", "content": _BURN_ESTIMATE_SYSTEM},
        {"role": "user", "content": f"Activity: {description}"},
    ]
    try:
        response = provider.chat(messages=messages)
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        data = json.loads(match.group())
        cals = int(float(data.get("calories") or 0))
        return {"calories": cals if cals > 0 else 200,
                "activity": (data.get("activity") or description).strip()}
    except Exception:
        return {"calories": 200, "activity": description}


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
    today: Optional[date] = None,
) -> dict:
    """Advance one turn of the log-a-meal conversation.

    ``state`` is ``None`` for the first turn, or the ``pending`` dict returned by a prior
    call. Returns ``{"reply", "pending", "logged", "entry"}`` where ``pending`` is either
    the next state to persist or ``None`` when the conversation is over. ``today`` is the
    user's local date (for "yesterday" resolution); defaults to the server date.
    """
    state = state or {}
    stage = state.get("stage")

    if stage == "confirming":
        return _handle_confirm(calorie_service, budget, state, message, today=today)

    # Fresh turn or a clarifying follow-up: accumulate the description.
    prior = state.get("description", "")
    addition = (message or "").strip()
    description = f"{prior}; {addition}".strip("; ").strip() if prior else addition
    rounds = int(state.get("clarify_rounds", 0))
    force = rounds >= MAX_CLARIFY_ROUNDS

    est = estimate_calories(provider, description, force=force, today=today)
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
    eaten_on = est.get("eaten_on")
    return {
        "reply": _confirm_reply(dish, calories, eaten_on, today),
        "pending": {"stage": "confirming", "description": description, "dish": dish,
                    "calories": int(calories), "items_json": items_json,
                    "protein_g": est.get("protein_g", 0), "carbs_g": est.get("carbs_g", 0),
                    "fat_g": est.get("fat_g", 0),
                    "eaten_on": eaten_on, "confirm_attempts": 0},
        "logged": False,
        "entry": None,
    }


def _handle_confirm(
    calorie_service: Optional[CalorieService], budget: int, state: dict, message: str,
    today: Optional[date] = None,
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
        eaten_on = state.get("eaten_on")
        eaten_at = _eaten_at_from(eaten_on)
        entry = calorie_service.add_entry(
            state.get("description", dish), calories,
            items_json=state.get("items_json"), eaten_at=eaten_at,
            dish=state.get("dish"),
            protein_g=state.get("protein_g", 0), carbs_g=state.get("carbs_g", 0),
            fat_g=state.get("fat_g", 0),
        )
        # Fallback day must match the service's timezone (add_entry stamps in the user's tz).
        default_day = today or (calorie_service._today() if calorie_service else date.today())
        day = eaten_at.date() if eaten_at else default_day
        total = calorie_service.total_for(day)
        remaining = int(budget) - total
        return {"reply": _logged_reply(dish, calories, total, budget, remaining, eaten_on, today),
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


def _eaten_at_from(eaten_on: Optional[str]) -> Optional[datetime]:
    """Build the eaten_at timestamp for a backdated entry (date + current wall-clock
    time so it sorts naturally), or None to let add_entry stamp 'now'."""
    if not eaten_on:
        return None
    try:
        return datetime.combine(date.fromisoformat(eaten_on), datetime.now().time())
    except (TypeError, ValueError):
        return None


def _friendly_day(eaten_on: Optional[str], today: Optional[date] = None) -> str:
    """'Jul 27' / 'yesterday' style label for a backdated day, or '' for today."""
    if not eaten_on:
        return ""
    try:
        d = date.fromisoformat(eaten_on)
    except (TypeError, ValueError):
        return ""
    if d == (today or date.today()) - timedelta(days=1):
        return "yesterday"
    return d.strftime("%b %-d")


def _confirm_reply(dish: str, calories: int, eaten_on: Optional[str] = None,
                   today: Optional[date] = None) -> str:
    when = _friendly_day(eaten_on, today)
    for_when = f" for *{when}*" if when else ""
    return (f"🍽️ *{dish}* is about *{calories} cal*{for_when}.\n"
            "Reply *yes* to log it, or tell me what to adjust.")


def _logged_reply(
    dish: str, calories: int, total: int, budget: int, remaining: int,
    eaten_on: Optional[str] = None, today: Optional[date] = None,
) -> str:
    when = _friendly_day(eaten_on, today)
    if when:
        # Backdated: report that day's total, not "today", and skip the today-centric "left".
        return f"✅ Logged *{dish}* ({calories} cal) for *{when}*. That day: *{total}/{budget} cal*. 📅"
    head = f"✅ Logged *{dish}* ({calories} cal). Today: *{total}/{budget} cal*"
    if remaining >= 0:
        return f"{head} — *{remaining} left*. 🎯"
    return f"{head} — *{abs(remaining)} over*. ⚠️"
