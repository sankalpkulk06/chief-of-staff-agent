"""Action agent — todos, habits, and facts."""
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from app.agents.base import AgentResult
from app.agents.prompts import load
from app.config.settings import get_settings
from app.core import calorie_util
from app.core import timezone_util as tzu
from app.core.calorie_service import CalorieService, advance_calorie_flow
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.core.todo_parser import parse_due_date
from app.providers.factory import ChatProvider
from app.storage.sqlite_registry import SQLiteRegistry

_WRITE_ACTIONS = {"add_todo", "add_habit", "log_habit", "remember_fact"}

_EXTRACT_SYSTEM = load("action_extract")


class ActionAgent:
    """Executes state-changing actions: todos, habits, and facts."""

    def __init__(
        self,
        chat_provider: ChatProvider,
        registry: Optional[SQLiteRegistry] = None,
        fact_service: Optional[FactService] = None,
        habit_service: Optional[HabitService] = None,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._provider = chat_provider
        self._registry = registry
        self._fact_service = fact_service
        self._habit_service = habit_service
        self._schedule_todo_callback = schedule_todo_callback

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict[str, Any]],
        previous_results: Optional[List[AgentResult]] = None,
        user_id: Optional[str] = None,
    ) -> AgentResult:
        fact_svc = (
            FactService(self._registry, user_id=user_id)
            if user_id and self._registry
            else self._fact_service
        )
        habit_svc = (
            HabitService(self._registry, user_id=user_id)
            if user_id and self._registry
            else self._habit_service
        )
        calorie_svc = (
            CalorieService(self._registry, user_id=user_id)
            if user_id and self._registry
            else None
        )
        try:
            habit_names = [h.name for h in habit_svc._get_all_active()] if habit_svc else []
            action, params = self._extract_action(task, habit_names=habit_names)
            return self._dispatch(
                action, params, task,
                fact_svc=fact_svc, habit_svc=habit_svc, calorie_svc=calorie_svc,
                user_id=user_id,
            )
        except Exception as exc:
            return AgentResult(
                agent="action_agent",
                task=task,
                output="",
                success=False,
                error=f"Action failed: {exc}",
            )

    def execute_approved(self, hitl_id: str, user_id: str) -> AgentResult:
        """Execute a previously deferred write action after user approval."""
        if not self._registry:
            return AgentResult(agent="action_agent", task="", output="",
                               success=False, error="no_registry")
        row = self._registry.get_hitl_request(hitl_id)
        if not row:
            return AgentResult(agent="action_agent", task="", output="",
                               success=False, error="hitl_not_found")
        fact_svc = FactService(self._registry, user_id=user_id)
        habit_svc = HabitService(self._registry, user_id=user_id)
        try:
            return self._dispatch(
                row["action_type"], row["action_payload"], row["action_type"],
                fact_svc=fact_svc, habit_svc=habit_svc,
                user_id=user_id,
                hitl_bypass=True,
            )
        except Exception as exc:
            return AgentResult(agent="action_agent", task=row["action_type"], output="",
                               success=False, error=f"Action failed: {exc}")

    # ------------------------------------------------------------------

    def _extract_action(self, task: str, habit_names: Optional[list] = None) -> tuple[str, dict]:
        if habit_names:
            habits_context = "Existing habits (use exact name when logging): " + ", ".join(f'"{n}"' for n in habit_names)
        else:
            habits_context = ""
        system = _EXTRACT_SYSTEM.replace("{habits_context}", habits_context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Task: {task}"},
        ]
        response = self._provider.chat(messages=messages)

        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in action response: {response!r}")

        data = json.loads(match.group())
        action = data.get("action", "")
        params = data.get("params", {})
        if not action:
            raise ValueError("No action found in extracted JSON")
        return action, params

    def _dispatch(
        self,
        action: str,
        params: dict,
        task: str,
        fact_svc: Optional[FactService] = None,
        habit_svc: Optional[HabitService] = None,
        calorie_svc: Optional[CalorieService] = None,
        user_id: Optional[str] = None,
        hitl_bypass: bool = False,
    ) -> AgentResult:
        fact_svc = fact_svc or self._fact_service
        habit_svc = habit_svc or self._habit_service

        # Gate all write actions behind HITL approval
        if action in _WRITE_ACTIONS and not hitl_bypass and self._registry:
            hitl_id = str(uuid.uuid4())
            self._registry.create_hitl_request(
                id=hitl_id,
                user_id=user_id or "",
                action_type=action,
                action_payload=params,
            )
            summary = self._describe_action(action, params)
            return AgentResult(
                agent="action_agent",
                task=task,
                output=f"I'm about to {summary}. Please confirm.",
                success=True,
                metadata={"hitl_pending": True, "hitl_id": hitl_id},
            )

        handlers = {
            "add_todo": lambda p, t: self._add_todo(p, t, user_id=user_id),
            "list_todos": lambda p, t: self._list_todos(p, t, user_id=user_id),
            "add_habit": lambda p, t: self._add_habit(p, t, habit_svc),
            "log_habit": lambda p, t: self._log_habit(p, t, habit_svc),
            "get_habits": lambda p, t: self._get_habits(p, t, habit_svc),
            "remember_fact": lambda p, t: self._remember_fact(p, t, fact_svc),
            "list_facts": lambda p, t: self._list_facts(p, t, fact_svc),
            "get_current_datetime": lambda p, t: self._get_current_datetime(p, t, user_id=user_id),
            "log_meal": lambda p, t: self._log_meal(p, t, calorie_svc, user_id),
            "calories_remaining": lambda p, t: self._calories_remaining(p, t, calorie_svc, user_id),
            "set_calorie_budget": lambda p, t: self._set_calorie_budget(p, t, user_id),
            "undo_meal": lambda p, t: self._undo_meal(p, t, calorie_svc, user_id),
        }
        handler = handlers.get(action)
        if not handler:
            return AgentResult(
                agent="action_agent",
                task=task,
                output=f"Unknown action '{action}'.",
                success=False,
                error=f"unknown_action: {action}",
            )
        return handler(params, task)

    @staticmethod
    def _describe_action(action: str, params: dict) -> str:
        """Return a clean human-readable description of a write action for HITL confirmation."""
        if action == "add_todo":
            task = params.get("task", "unknown task")
            due = params.get("due_date")
            due_str = f" — due {due}" if due else ""
            return f"add a reminder: {task}{due_str}"
        if action == "add_habit":
            name = params.get("name", "unknown habit")
            time = params.get("reminder_time", "21:00")
            return f"start tracking habit '{name}' with a daily reminder at {time}"
        if action == "log_habit":
            name = params.get("name", "unknown habit")
            status = params.get("status", "done")
            verb = "mark" if status == "done" else "skip"
            return f"{verb} '{name}' as {status} for today"
        if action == "remember_fact":
            fact = params.get("fact", "unknown fact")
            category = params.get("category", "personal")
            return f"save {category} fact: {fact}"
        return f"perform action: {action}"

    def _add_todo(self, params: dict, task: str, user_id: Optional[str] = None) -> AgentResult:
        if not self._registry:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_registry")
        todo_task = params.get("task", task)
        list_name = params.get("list_name")
        due_at = parse_due_date(params.get("due_date", ""))
        todo = self._registry.create_todo(title=todo_task, list_name=list_name, due_at=due_at, user_id=user_id or "")
        if due_at and self._schedule_todo_callback:
            self._schedule_todo_callback(todo)
        due_str = f" due {due_at.strftime('%a, %b %d at %I:%M%p')}" if due_at else ""
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"Added reminder: {todo_task}{due_str}.",
            success=True,
        )

    def _list_todos(self, params: dict, task: str, user_id: Optional[str] = None) -> AgentResult:
        if not self._registry:
            return AgentResult(agent="action_agent", task=task, output="No reminders configured.", success=True)

        scope = str(params.get("scope") or "").lower()
        if not scope and "today" in task.lower():
            scope = "today"

        todos = self._registry.list_todos(user_id=user_id or "")
        if scope == "today":
            today = date.today()
            todos = [todo for todo in todos if self._todo_due_date(todo.get("due_at")) == today]

        if not todos:
            label = "today" if scope == "today" else "pending"
            return AgentResult(agent="action_agent", task=task, output=f"No {label} reminders or tasks.", success=True)

        label = "Today's reminders and tasks" if scope == "today" else "Pending reminders and tasks"
        lines = [f"{label}:"]
        for todo in todos[:20]:
            title = str(todo.get("title", "")).strip() or "Untitled"
            due = self._format_due(todo.get("due_at"))
            lines.append(f"• {title}{due}")
        return AgentResult(agent="action_agent", task=task, output="\n".join(lines), success=True)

    @staticmethod
    def _todo_due_date(value: Any) -> Optional[date]:
        due_at = ActionAgent._coerce_datetime(value)
        return due_at.date() if due_at else None

    @staticmethod
    def _format_due(value: Any) -> str:
        due_at = ActionAgent._coerce_datetime(value)
        if not due_at:
            return ""
        today = date.today()
        if due_at.date() == today:
            label = due_at.strftime("%-I:%M %p")
        elif due_at.date() == today + timedelta(days=1):
            label = due_at.strftime("tomorrow at %-I:%M %p")
        else:
            label = due_at.strftime("%b %-d at %-I:%M %p")
        return f" — due {label}"

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value.astimezone().replace(tzinfo=None) if value.tzinfo is not None else value
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo is not None else parsed

    def _add_habit(self, params: dict, task: str, habit_svc: Optional[HabitService] = None) -> AgentResult:
        svc = habit_svc or self._habit_service
        if not svc:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_habit_service")
        name = params.get("name", "")
        if not name:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing habit name")
        reminder_time = params.get("reminder_time", "21:00")
        habit = svc.add_habit(name=name, reminder_time=reminder_time)
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"Now tracking habit '{habit.name}' (daily reminder at {habit.reminder_time}).",
            success=True,
        )

    def _log_habit(self, params: dict, task: str, habit_svc: Optional[HabitService] = None) -> AgentResult:
        svc = habit_svc or self._habit_service
        if not svc:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_habit_service")
        name = params.get("name", "")
        status = params.get("status", "done")
        if not name:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing habit name")
        try:
            log = svc.log_habit(name=name, status=status)
            verb = "skipped" if log.status == "skipped" else "logged as done"
            return AgentResult(
                agent="action_agent",
                task=task,
                output=f"Habit '{name}' {verb} for today.",
                success=True,
            )
        except ValueError as exc:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error=str(exc))

    def _get_habits(self, params: dict, task: str, habit_svc: Optional[HabitService] = None) -> AgentResult:
        svc = habit_svc or self._habit_service
        if not svc:
            return AgentResult(agent="action_agent", task=task, output="No habits configured.", success=True)
        from datetime import date
        summaries = svc.get_weekly_summary()
        if not summaries:
            return AgentResult(agent="action_agent", task=task, output="No habits tracked yet.", success=True)
        week = date.today().strftime("Week of %b %-d, %Y")
        lines = [f"Habit summary — {week}:"]
        for s in summaries:
            streak = f"🔥 {s.streak}-day streak" if s.streak > 0 else "streak broken"
            logged = "✓ logged today" if s.logged_today else "not yet today"
            lines.append(f"• {s.habit.name}: {s.days_done}/7 days | {streak} | {logged}")
        return AgentResult(agent="action_agent", task=task, output="\n".join(lines), success=True)

    def _remember_fact(self, params: dict, task: str, fact_svc: Optional[FactService] = None) -> AgentResult:
        svc = fact_svc or self._fact_service
        if not svc:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_fact_service")
        fact = params.get("fact", "")
        category = params.get("category", "personal")
        if not fact:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing fact")
        svc.remember(content=fact, category=category)
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"{category.title()} fact saved: {fact}",
            success=True,
        )

    def _list_facts(self, params: dict, task: str, fact_svc: Optional[FactService] = None) -> AgentResult:
        svc = fact_svc or self._fact_service
        if not svc:
            return AgentResult(agent="action_agent", task=task, output="No facts configured.", success=True)
        category = params.get("category", "all")
        filter_cat = None if category == "all" else category
        facts = svc.list_facts(category=filter_cat)
        if not facts:
            return AgentResult(agent="action_agent", task=task, output=f"No {category} facts saved yet.", success=True)
        lines = [f"Your {category} facts:"]
        for f in facts[:20]:
            lines.append(f"• {f.content} ({f.category})")
        return AgentResult(agent="action_agent", task=task, output="\n".join(lines), success=True)

    def _get_current_datetime(self, params: dict, task: str, user_id: Optional[str] = None) -> AgentResult:
        """Return the current date and time in the user's timezone.

        Read-only, no HITL. The clock is read here in Python — the LLM only
        picks the action — so the answer is always factual, never hallucinated.
        """
        stamp = tzu.describe_now(
            self._registry, user_id or "", default=get_settings().default_timezone
        )
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"The current date and time is {stamp}.",
            success=True,
        )

    # ------------------------------------------------------------------
    # Calorie counter
    # ------------------------------------------------------------------

    def _log_meal(
        self, params: dict, task: str,
        calorie_svc: Optional[CalorieService], user_id: Optional[str],
    ) -> AgentResult:
        """Start the log-a-meal flow: estimate, then return a clarify or confirm question.

        This is the FIRST turn only. The returned ``calorie_pending`` metadata is picked up
        by ChatService and persisted to ``pending_prompts``; follow-up turns (the user's
        answer / confirmation) are handled in ``ChatService._maybe_continue_calorie``.
        """
        if not calorie_svc:
            return AgentResult(agent="action_agent", task=task, output="",
                               success=False, error="no_calorie_service")
        description = (params.get("description") or params.get("meal") or task or "").strip()
        if not description:
            return AgentResult(
                agent="action_agent", task=task, success=True,
                output="What did you eat? Tell me the dish and roughly how much.",
            )
        budget = calorie_util.get_calorie_budget(self._registry, user_id or "")
        result = advance_calorie_flow(self._provider, calorie_svc, budget, None, description)
        metadata = {}
        if result.get("pending"):
            metadata["calorie_pending"] = result["pending"]
        return AgentResult(
            agent="action_agent", task=task, output=result["reply"],
            success=True, metadata=metadata,
        )

    def _calories_remaining(
        self, params: dict, task: str,
        calorie_svc: Optional[CalorieService], user_id: Optional[str],
    ) -> AgentResult:
        if not calorie_svc:
            return AgentResult(agent="action_agent", task=task, output="",
                               success=False, error="no_calorie_service")
        budget = calorie_util.get_calorie_budget(self._registry, user_id or "")
        total = calorie_svc.today_total()
        remaining = budget - total
        if remaining >= 0:
            out = f"Today: {total}/{budget} cal — {remaining} left."
        else:
            out = f"Today: {total}/{budget} cal — {abs(remaining)} over budget."
        if not calorie_util.has_calorie_budget(self._registry, user_id or ""):
            out += (f" (Using the default {budget} cal budget — say "
                    "'set my calorie budget to 1800' to change it.)")
        return AgentResult(agent="action_agent", task=task, output=out, success=True)

    def _set_calorie_budget(
        self, params: dict, task: str, user_id: Optional[str],
    ) -> AgentResult:
        amount = params.get("amount", params.get("budget"))
        if amount is None:
            return AgentResult(
                agent="action_agent", task=task, success=True,
                output="How many calories per day? e.g. 'set my calorie budget to 2000'.",
            )
        try:
            value = calorie_util.set_calorie_budget(self._registry, user_id or "", amount)
        except (TypeError, ValueError) as exc:
            return AgentResult(agent="action_agent", task=task, success=True,
                               output=str(exc) or "That doesn't look like a valid number.")
        return AgentResult(agent="action_agent", task=task, success=True,
                           output=f"Daily calorie budget set to {value} cal. 🎯")

    def _undo_meal(
        self, params: dict, task: str,
        calorie_svc: Optional[CalorieService], user_id: Optional[str],
    ) -> AgentResult:
        if not calorie_svc:
            return AgentResult(agent="action_agent", task=task, output="",
                               success=False, error="no_calorie_service")
        removed = calorie_svc.delete_latest_today()
        if not removed:
            return AgentResult(agent="action_agent", task=task, success=True,
                               output="There's nothing logged today to undo.")
        budget = calorie_util.get_calorie_budget(self._registry, user_id or "")
        total = calorie_svc.today_total()
        remaining = budget - total
        return AgentResult(
            agent="action_agent", task=task, success=True,
            output=(f"Removed {removed['description']} ({removed['calories']} cal). "
                    f"Today: {total}/{budget} cal — {remaining} left."),
        )
