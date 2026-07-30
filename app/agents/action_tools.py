"""Native tool definitions for the ActionAgent's verbs.

One Tool per verb — the model calls these directly (and can call several in one turn),
replacing the prompt-for-JSON action list. The tool NAMES and argument keys match the
existing handler dispatch + HITL payloads exactly, so the returned tool calls feed straight
into ActionAgent._dispatch / _dispatch_many unchanged.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.providers.tool_types import Tool

_OBJ = {"type": "object", "properties": {}}


def _tool(name, description, properties=None, required=None):
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    return Tool(name=name, description=description, parameters=params)


ACTION_TOOLS = [
    _tool("add_todo", "Create a reminder or task.",
          {"task": {"type": "string"},
           "due_date": {"type": "string", "description": "natural language, e.g. 'tomorrow 5pm'"},
           "list_name": {"type": "string"}},
          ["task"]),
    _tool("list_todos", "List saved reminders/tasks.",
          {"scope": {"type": "string", "enum": ["today", "all"]}}),
    _tool("add_habit", "Start tracking a NEW habit.",
          {"name": {"type": "string"}, "reminder_time": {"type": "string", "description": "HH:MM"}},
          ["name"]),
    _tool("log_habit",
          "Record that a tracked habit was done/skipped. Use for reporting an activity that "
          "matches an existing habit (verbs like log/did/went/hit/finished) — NEVER remember_fact.",
          {"name": {"type": "string", "description": "the EXACT stored habit name"},
           "status": {"type": "string", "enum": ["done", "skipped"]},
           "logged_on": {"type": "string", "description": "YYYY-MM-DD if the user named a past day; omit for today"}},
          ["name"]),
    _tool("get_habits", "Retrieve the habit summary / streaks."),
    _tool("remember_fact",
          "Save a DURABLE fact about the user — identity, job, relationships, stable preferences "
          "(e.g. 'my name is Sam', 'I work at Acme'). NOT for one-off activities or anything that "
          "matches a tracked habit — those are log_habit.",
          {"fact": {"type": "string"}, "category": {"type": "string", "enum": ["personal", "work"]}},
          ["fact"]),
    _tool("list_facts", "List stored facts.",
          {"category": {"type": "string", "enum": ["personal", "work", "all"]}}),
    _tool("get_current_datetime", "Return the current date, day of week, and time."),
    _tool("log_meal",
          "The user ate/drank something to count toward calories. Include quantities AND any "
          "timing ('yesterday', 'for dinner on Jul 27') verbatim in description.",
          {"description": {"type": "string"}},
          ["description"]),
    _tool("log_burn",
          "The user burned calories in a workout/exercise.",
          {"calories": {"type": "integer", "description": "ONLY if the user gave a number"},
           "description": {"type": "string", "description": "the workout/activity"}}),
    _tool("calories_remaining", "Report today's calorie total and how many are left."),
    _tool("set_calorie_budget", "Set the user's daily calorie target.",
          {"amount": {"type": "integer"}}, ["amount"]),
    _tool("undo_meal", "Remove today's most recently logged meal."),
]


def action_system(habits_context: str, today: Optional[date] = None) -> str:
    """System message for tool-based action extraction — carries the cross-cutting routing
    rules the individual tool descriptions can't (today's date, existing habits, and the
    log_habit-vs-remember_fact rule)."""
    today = today or date.today()
    lines = [
        "You turn the user's message into one or more tool calls for a personal assistant.",
        f"Today's date is {today.isoformat()}. Resolve any day the user names "
        "('yesterday', 'last night', 'on Jul 27') to an absolute YYYY-MM-DD in the relevant argument.",
        "Call one tool per DISTINCT thing the user asked for (usually just one).",
        "If the user reports DOING something that matches an existing habit below — even loosely or "
        "with typos — call log_habit with the EXACT stored name, never remember_fact.",
    ]
    if habits_context:
        lines.append(habits_context)
    return "\n".join(lines)
