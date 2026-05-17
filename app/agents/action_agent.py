"""Action agent — todos, habits, and facts."""
import json
import re
from typing import Any, Callable, Dict, List, Optional

from app.agents.base import AgentResult
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.core.todo_parser import parse_due_date
from app.providers.ollama_chat import OllamaChatProvider
from app.storage.sqlite_registry import SQLiteRegistry

_EXTRACT_SYSTEM = """\
You are an action extractor for a personal AI assistant. \
Given a task description, output JSON identifying the action and its parameters.

Possible actions:
- add_todo: create a reminder/task. Params: task (str), due_date (str, optional, natural language), list_name (str, optional)
- add_habit: start tracking a new habit. Params: name (str), reminder_time (str, optional, e.g. "21:00")
- log_habit: record a habit as done or skipped. Params: name (str), status ("done"|"skipped")
- get_habits: retrieve habit summary. Params: none
- remember_fact: save a personal or work fact. Params: fact (str), category ("personal"|"work")
- list_facts: list stored facts. Params: category ("personal"|"work"|"all")

Output ONLY valid JSON:
{"action": "action_name", "params": {...}}"""


class ActionAgent:
    """Executes state-changing actions: todos, habits, and facts."""

    def __init__(
        self,
        chat_provider: OllamaChatProvider,
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
    ) -> AgentResult:
        try:
            action, params = self._extract_action(task)
            return self._dispatch(action, params, task)
        except Exception as exc:
            return AgentResult(
                agent="action_agent",
                task=task,
                output="",
                success=False,
                error=f"Action failed: {exc}",
            )

    # ------------------------------------------------------------------

    def _extract_action(self, task: str) -> tuple[str, dict]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _EXTRACT_SYSTEM},
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

    def _dispatch(self, action: str, params: dict, task: str) -> AgentResult:
        handlers = {
            "add_todo": self._add_todo,
            "add_habit": self._add_habit,
            "log_habit": self._log_habit,
            "get_habits": self._get_habits,
            "remember_fact": self._remember_fact,
            "list_facts": self._list_facts,
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

    def _add_todo(self, params: dict, task: str) -> AgentResult:
        if not self._registry:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_registry")
        todo_task = params.get("task", task)
        list_name = params.get("list_name")
        due_at = parse_due_date(params.get("due_date", ""))
        todo = self._registry.create_todo(title=todo_task, list_name=list_name, due_at=due_at)
        if due_at and self._schedule_todo_callback:
            self._schedule_todo_callback(todo)
        due_str = f" due {due_at.strftime('%a, %b %d at %I:%M%p')}" if due_at else ""
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"Added reminder: {todo_task}{due_str}.",
            success=True,
        )

    def _add_habit(self, params: dict, task: str) -> AgentResult:
        if not self._habit_service:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_habit_service")
        name = params.get("name", "")
        if not name:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing habit name")
        reminder_time = params.get("reminder_time", "21:00")
        habit = self._habit_service.add_habit(name=name, reminder_time=reminder_time)
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"Now tracking habit '{habit.name}' (daily reminder at {habit.reminder_time}).",
            success=True,
        )

    def _log_habit(self, params: dict, task: str) -> AgentResult:
        if not self._habit_service:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_habit_service")
        name = params.get("name", "")
        status = params.get("status", "done")
        if not name:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing habit name")
        try:
            log = self._habit_service.log_habit(name=name, status=status)
            verb = "skipped" if log.status == "skipped" else "logged as done"
            return AgentResult(
                agent="action_agent",
                task=task,
                output=f"Habit '{name}' {verb} for today.",
                success=True,
            )
        except ValueError as exc:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error=str(exc))

    def _get_habits(self, params: dict, task: str) -> AgentResult:
        if not self._habit_service:
            return AgentResult(agent="action_agent", task=task, output="No habits configured.", success=True)
        from datetime import date
        summaries = self._habit_service.get_weekly_summary()
        if not summaries:
            return AgentResult(agent="action_agent", task=task, output="No habits tracked yet.", success=True)
        week = date.today().strftime("Week of %b %-d, %Y")
        lines = [f"Habit summary — {week}:"]
        for s in summaries:
            streak = f"🔥 {s.streak}-day streak" if s.streak > 0 else "streak broken"
            logged = "✓ logged today" if s.logged_today else "not yet today"
            lines.append(f"• {s.habit.name}: {s.days_done}/7 days | {streak} | {logged}")
        return AgentResult(agent="action_agent", task=task, output="\n".join(lines), success=True)

    def _remember_fact(self, params: dict, task: str) -> AgentResult:
        if not self._fact_service:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="no_fact_service")
        fact = params.get("fact", "")
        category = params.get("category", "personal")
        if not fact:
            return AgentResult(agent="action_agent", task=task, output="", success=False, error="missing fact")
        self._fact_service.remember(content=fact, category=category)
        return AgentResult(
            agent="action_agent",
            task=task,
            output=f"{category.title()} fact saved: {fact}",
            success=True,
        )

    def _list_facts(self, params: dict, task: str) -> AgentResult:
        if not self._fact_service:
            return AgentResult(agent="action_agent", task=task, output="No facts configured.", success=True)
        category = params.get("category", "all")
        filter_cat = None if category == "all" else category
        facts = self._fact_service.list_facts(category=filter_cat)
        if not facts:
            return AgentResult(agent="action_agent", task=task, output=f"No {category} facts saved yet.", success=True)
        lines = [f"Your {category} facts:"]
        for f in facts[:20]:
            lines.append(f"• {f.content} ({f.category})")
        return AgentResult(agent="action_agent", task=task, output="\n".join(lines), success=True)

