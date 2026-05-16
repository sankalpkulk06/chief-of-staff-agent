"""Tool definitions for the open source model to call."""
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.services.news_service import NewsService
from app.services.reminders_service import RemindersService
from app.services.web_search_service import WebSearchService
from app.retrieval.retriever import Retriever
from app.storage.sqlite_registry import SQLiteRegistry
from app.core.todo_parser import parse_due_date


class ToolParameter(BaseModel):
    """Describes a parameter for a tool."""

    name: str
    type: str  # "string", "integer", "boolean", etc.
    description: str
    required: bool = True
    enum: Optional[List[str]] = None


class Tool(ABC):
    """Base class for all tools."""

    def __init__(self, name: str, description: str, parameters: List[ToolParameter]):
        self.name = name
        self.description = description
        self.parameters = parameters

    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with given parameters.

        Returns:
            Dict with "success" and "result" or "error" keys
        """
        pass

    def to_schema(self) -> Dict[str, Any]:
        """Convert tool to JSON schema for prompt."""
        param_properties = {}
        required_params = []

        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            param_properties[param.name] = prop
            if param.required:
                required_params.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": param_properties,
                "required": required_params,
            },
        }


class FetchNewsTool(Tool):
    """Fetch live news about a topic."""

    def __init__(self, news_service: NewsService):
        super().__init__(
            name="fetch_news",
            description="Fetch the latest news articles about a specific topic. Returns a summary and list of articles.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The news topic to search for (e.g., 'Tesla', 'climate change')",
                    required=True,
                )
            ],
        )
        self.news_service = news_service

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        try:
            articles = self.news_service.search_news(query) if query else self.news_service.get_top_news()
            if articles:
                article_texts = "\n".join(
                    f"- {a.title} ({a.source}): {a.snippet[:100]}..." for a in articles[:5]
                )
                return {
                    "success": True,
                    "result": f"Found {len(articles)} articles about {query or 'top news'}:\n\n{article_texts}",
                }
            return {"success": True, "result": f"No news found for '{query}'"}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch news: {str(e)}"}


class RememberFactTool(Tool):
    """Remember a personal or work fact."""

    def __init__(self, fact_service: FactService):
        super().__init__(
            name="remember_fact",
            description="Save a fact to remember. Can be personal (about you) or work-related.",
            parameters=[
                ToolParameter(
                    name="fact",
                    type="string",
                    description="The fact to remember",
                    required=True,
                ),
                ToolParameter(
                    name="category",
                    type="string",
                    description="Category: personal or work",
                    required=True,
                    enum=["personal", "work"],
                ),
            ],
        )
        self.fact_service = fact_service

    def execute(self, fact: str = "", category: str = "personal", **kwargs) -> Dict[str, Any]:
        try:
            if not fact:
                return {"success": False, "error": "Fact cannot be empty"}
            self.fact_service.remember(content=fact, category=category)
            return {"success": True, "result": f"✓ {category.title()} fact saved: {fact}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to save fact: {str(e)}"}


class ListFactsTool(Tool):
    """List learned facts by category."""

    def __init__(self, fact_service: FactService):
        super().__init__(
            name="list_facts",
            description="List all learned facts, optionally filtered by category.",
            parameters=[
                ToolParameter(
                    name="category",
                    type="string",
                    description="Filter by category (personal, work, or all)",
                    required=False,
                    enum=["personal", "work", "all"],
                )
            ],
        )
        self.fact_service = fact_service

    def execute(self, category: str = "all", **kwargs) -> Dict[str, Any]:
        try:
            filter_cat = None if category == "all" else category
            facts = self.fact_service.list_facts(category=filter_cat)
            if facts:
                fact_list = "\n".join(f"- {f.content} ({f.category})" for f in facts[:10])
                return {
                    "success": True,
                    "result": f"Your learned facts ({category}):\n\n{fact_list}",
                }
            return {"success": True, "result": f"No {category} facts learned yet."}
        except Exception as e:
            return {"success": False, "error": f"Failed to list facts: {str(e)}"}


class AddTodoTool(Tool):
    """Add a Sage-owned todo/reminder to SQLite."""

    def __init__(
        self,
        registry: SQLiteRegistry,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        super().__init__(
            name="add_todo",
            description=(
                "Create a Sage-owned todo/reminder. Use this for requests like "
                "'remind me to take out the trash at 7pm today' unless the user "
                "explicitly asks for Apple Reminders."
            ),
            parameters=[
                ToolParameter(
                    name="task",
                    type="string",
                    description="The task to add",
                    required=True,
                ),
                ToolParameter(
                    name="list_name",
                    type="string",
                    description="Which Reminders list to add to (default: Reminders)",
                    required=False,
                ),
                ToolParameter(
                    name="due_date",
                    type="string",
                    description="Due date in natural language (e.g., tomorrow, next Tuesday, April 15 at 3pm)",
                    required=False,
                ),
            ],
        )
        self.registry = registry
        self.schedule_todo_callback = schedule_todo_callback

    def execute(self, task: str = "", list_name: Optional[str] = None, due_date: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            if not task:
                return {"success": False, "error": "Task cannot be empty"}

            parsed_due_date = parse_due_date(due_date)
            todo = self.registry.create_todo(
                title=task,
                list_name=list_name,
                due_at=parsed_due_date,
            )
            if parsed_due_date and self.schedule_todo_callback:
                self.schedule_todo_callback(todo)
            due_str = f" due {parsed_due_date.strftime('%a, %b %d at %I:%M%p')}" if parsed_due_date else ""
            return {"success": True, "result": f"✓ Added Sage reminder: {task}{due_str}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to add todo: {str(e)}"}


class AddAppleReminderTool(Tool):
    """Add a task to Apple Reminders only when explicitly requested."""

    def __init__(self, reminders_service: RemindersService):
        super().__init__(
            name="add_apple_reminder",
            description=(
                "Add a task to native Apple Reminders. Use only when the user explicitly "
                "says Apple Reminders or asks to add it to Apple Reminders."
            ),
            parameters=[
                ToolParameter(name="task", type="string", description="The task to add", required=True),
                ToolParameter(
                    name="list_name",
                    type="string",
                    description="Which Apple Reminders list to add to",
                    required=False,
                ),
                ToolParameter(
                    name="due_date",
                    type="string",
                    description="Due date in natural language",
                    required=False,
                ),
            ],
        )
        self.reminders_service = reminders_service

    def execute(self, task: str = "", list_name: Optional[str] = None, due_date: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            if not task:
                return {"success": False, "error": "Task cannot be empty"}
            parsed_due_date = parse_due_date(due_date)
            target_list = self.reminders_service.add_reminder(
                task=task, list_name=list_name, due_date=parsed_due_date
            )
            due_str = f" due {parsed_due_date.strftime('%a, %b %d at %I:%M%p')}" if parsed_due_date else ""
            return {"success": True, "result": f"✓ Added to Apple Reminders {target_list}: {task}{due_str}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to add Apple Reminder: {str(e)}"}


class SearchDocumentsTool(Tool):
    """Search through your documents for information."""

    def __init__(self, retriever: Retriever):
        super().__init__(
            name="search_documents",
            description="Search through your personal documents and notes using semantic search.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="What to search for in your documents",
                    required=True,
                ),
                ToolParameter(
                    name="top_k",
                    type="integer",
                    description="Number of results to return (default: 5)",
                    required=False,
                ),
            ],
        )
        self.retriever = retriever

    def execute(self, query: str = "", top_k: int = 5, **kwargs) -> Dict[str, Any]:
        try:
            if not query:
                return {"success": False, "error": "Query cannot be empty"}
            result = self.retriever.retrieve(query, top_k=top_k)
            if result.chunks:
                from urllib.parse import urlparse as _up
                lines = []
                for c in result.chunks[:5]:
                    if c.source_type == "url" and c.source_url:
                        label = f"{c.file_name} ({_up(c.source_url).netloc}) 🌐"
                    else:
                        label = f"{c.file_name or c.source_path or c.document_id} 📄"
                    lines.append(f"- [{label}]: {c.text[:150]}...")
                return {"success": True, "result": "Found in your documents:\n\n" + "\n".join(lines)}
            return {"success": True, "result": f"No documents found matching '{query}'"}
        except Exception as e:
            return {"success": False, "error": f"Failed to search documents: {str(e)}"}


class WebSearchTool(Tool):
    """Search the web for current information."""

    def __init__(self, web_search_service: WebSearchService):
        super().__init__(
            name="web_search",
            description=(
                "Search the web for current factual information, recent events, definitions, "
                "tutorials, or anything not in the user's documents."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The search query",
                    required=True,
                )
            ],
        )
        self.web_search_service = web_search_service

    def execute(self, query: str = "", **kwargs) -> Dict[str, Any]:
        try:
            if not query:
                return {"success": False, "error": "Query cannot be empty"}
            results = self.web_search_service.search(query)
            if results:
                formatted = self.web_search_service.format_for_context(results)
                return {
                    "success": True,
                    "result": formatted,
                    "raw_results": [{"title": r.title, "url": r.url} for r in results],
                }
            return {"success": True, "result": f"No web results found for '{query}'", "raw_results": []}
        except Exception as e:
            return {"success": False, "error": f"Web search failed: {str(e)}"}


class LogHabitTool(Tool):
    """Log a habit as done or skipped for today."""

    def __init__(self, habit_service: HabitService):
        super().__init__(
            name="log_habit",
            description=(
                "Log a tracked habit as done or skipped for today. "
                "Use this when the user says they did something habitual like 'I went to the gym', "
                "'I meditated today', 'log my reading habit', etc."
            ),
            parameters=[
                ToolParameter(
                    name="name",
                    type="string",
                    description="The habit name to log (e.g. 'gym', 'reading', 'meditation')",
                    required=True,
                ),
                ToolParameter(
                    name="status",
                    type="string",
                    description="Whether the habit was completed: 'done' or 'skipped'",
                    required=False,
                    enum=["done", "skipped"],
                ),
            ],
        )
        self.habit_service = habit_service

    def execute(self, name: str = "", status: str = "done", **kwargs) -> Dict[str, Any]:
        try:
            if not name:
                return {"success": False, "error": "Habit name cannot be empty"}
            log = self.habit_service.log_habit(name=name, status=status)
            verb = "skipped" if log.status == "skipped" else "logged as done"
            return {"success": True, "result": f"✓ Habit '{name}' {verb} for today"}
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Failed to log habit: {str(e)}"}


class GetHabitsTool(Tool):
    """Get the weekly habit summary."""

    def __init__(self, habit_service: HabitService):
        super().__init__(
            name="get_habits",
            description=(
                "Get the user's tracked habits and their weekly progress summary. "
                "Use this when the user asks about their habits, streaks, or habit status."
            ),
            parameters=[],
        )
        self.habit_service = habit_service

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            from datetime import date
            summaries = self.habit_service.get_weekly_summary()
            if not summaries:
                return {"success": True, "result": "No habits tracked yet. You can add one by telling me a habit you want to track."}
            today = date.today()
            week_label = today.strftime("Week of %b %-d, %Y")
            lines = [f"Habit summary — {week_label}:\n"]
            for s in summaries:
                streak_txt = f"🔥 {s.streak}-day streak" if s.streak > 0 else "streak broken"
                today_txt = "✓ logged today" if s.logged_today else "not logged today"
                lines.append(f"• {s.habit.name}: {s.days_done}/7 days this week | {streak_txt} | {today_txt}")
            return {"success": True, "result": "\n".join(lines)}
        except Exception as e:
            return {"success": False, "error": f"Failed to get habits: {str(e)}"}


class AddHabitTool(Tool):
    """Add a new habit to track."""

    def __init__(self, habit_service: HabitService):
        super().__init__(
            name="add_habit",
            description=(
                "Start tracking a new habit. Use this when the user wants to add or create a habit to track."
            ),
            parameters=[
                ToolParameter(
                    name="name",
                    type="string",
                    description="The name of the habit to track (e.g. 'gym', 'reading', 'meditation')",
                    required=True,
                ),
                ToolParameter(
                    name="reminder_time",
                    type="string",
                    description="Daily reminder time in 24h or 12h format (e.g. '21:00', '9pm'). Defaults to 21:00.",
                    required=False,
                ),
            ],
        )
        self.habit_service = habit_service

    def execute(self, name: str = "", reminder_time: str = "21:00", **kwargs) -> Dict[str, Any]:
        try:
            if not name:
                return {"success": False, "error": "Habit name cannot be empty"}
            habit = self.habit_service.add_habit(name=name, reminder_time=reminder_time)
            return {"success": True, "result": f"✓ Now tracking habit '{habit.name}' (reminder at {habit.reminder_time})"}
        except Exception as e:
            return {"success": False, "error": f"Failed to add habit: {str(e)}"}


class ToolRegistry:
    """Registry of all available tools."""

    def __init__(
        self,
        news_service: Optional[NewsService] = None,
        fact_service: Optional[FactService] = None,
        registry: Optional[SQLiteRegistry] = None,
        reminders_service: Optional[RemindersService] = None,
        schedule_todo_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        retriever: Optional[Retriever] = None,
        web_search_service: Optional[WebSearchService] = None,
        habit_service: Optional[HabitService] = None,
    ):
        self.tools: Dict[str, Tool] = {}

        if news_service:
            self.register(FetchNewsTool(news_service))
        if fact_service:
            self.register(RememberFactTool(fact_service))
            self.register(ListFactsTool(fact_service))
        if registry:
            self.register(AddTodoTool(registry, schedule_todo_callback=schedule_todo_callback))
        if reminders_service:
            self.register(AddAppleReminderTool(reminders_service))
        if retriever:
            self.register(SearchDocumentsTool(retriever))
        if web_search_service:
            self.register(WebSearchTool(web_search_service))
        if habit_service:
            self.register(GetHabitsTool(habit_service))
            self.register(AddHabitTool(habit_service))
            self.register(LogHabitTool(habit_service))

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self.tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self.tools.get(name)

    def get_all(self) -> List[Tool]:
        """Get all registered tools."""
        return list(self.tools.values())

    def to_schemas(self) -> List[Dict[str, Any]]:
        """Convert all tools to JSON schemas for prompt."""
        return [tool.to_schema() for tool in self.get_all()]

    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with parameters."""
        tool = self.get(tool_name)
        if not tool:
            return {"success": False, "error": f"Tool '{tool_name}' not found"}
        return tool.execute(**parameters)
