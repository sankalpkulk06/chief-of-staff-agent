import re
import uuid
from datetime import date, datetime
from typing import Any, Callable, List, Optional

from app.core.qa_service import QAResult
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.services.email_service import EmailService
from app.services.news_service import NewsService, NewsArticle
from app.services.reminders_service import RemindersService
from app.core.tool_executor import ToolExecutor
from app.core.tools import ToolRegistry
from app.core.todo_parser import parse_due_date, parse_reminder_request
from app.services.web_search_service import WebSearchService
from app.services.url_ingestion_service import URLIngestionService
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.prompt_builder import build_chat_messages, build_system_message_with_tools
from app.retrieval.retriever import Retriever, RetrievalResult
from app.storage.sqlite_registry import SQLiteRegistry

_EMAIL_TRIGGERS = {
    "check my email", "check email", "any emails", "any email",
    "show my email", "show email", "read my email", "read email",
    "email inbox", "check inbox", "what emails", "do i have email",
    "email triage", "triage email", "triage my email",
    "check my gmail", "check gmail", "my gmail",
}
_EMAIL_ACTION_WORDS = {"check", "any", "show", "read", "triage", "fetch", "get", "what", "action", "gmail"}


def _is_email_request(text: str) -> bool:
    t = text.lower().strip()
    if t in _EMAIL_TRIGGERS:
        return True
    if ("/email" in t or "/email-personal" in t):
        return True
    if ("email" in t or "inbox" in t or "gmail" in t) and any(w in t for w in _EMAIL_ACTION_WORDS):
        return True
    return False


class ChatService:
    """Session-aware chat service with conversation history and persistence."""

    WHATSAPP_RESPONSE_STYLE = (
        "Format replies for WhatsApp: easy to scan on a phone, warm, casual, and concise. "
        "Use friendly emojis as visual anchors, especially at the start of short sections or status lines. "
        "Prefer short paragraphs or compact bullets. Avoid dense blocks of text. "
        "Use WhatsApp markdown lightly (*bold* for labels, backticks for commands). "
        "Do not overdo it: 1-4 useful emojis is usually enough unless the user is celebrating."
    )

    CONVERSATIONAL_PATTERNS = [
        r"^(hi|hello|hey|howdy|sup|yo)\b",
        r"^how are you",
        r"^good (morning|afternoon|evening|night)",
        r"what (is |'s )?(your |my )?(name|last name|surname|birthday|birth year|age|location|job|work)",
        r"where (do |)i (live|work)",
        r"what (do |)i (do|work as)",
        r"who are you",
        r"what are you",
        r"are you (an? )?(ai|bot|assistant)",
        r"what did (i|you) (ask|say|tell)",
        r"\b(earlier|previous(ly)?|before|last (time|message|question))\b",
        r"^(thanks|thank you|cheers|great|nice|cool|awesome|ok|okay|sure|got it|understood|perfect)\b",
        r"^bye|^see you|^goodbye|^farewell",
    ]

    NEWS_PATTERNS = [
        r"(what('s| is) the news (on|about|for|regarding))",
        r"(latest|recent|current|today'?s?) news (on|about|for|regarding)",
        r"(any )?news (on|about|for|regarding)",
        r"what happened (to|with|in)",
        r"news (on|about)",
    ]

    def __init__(
        self,
        retriever: Retriever,
        chat_provider: OllamaChatProvider,
        registry: SQLiteRegistry,
        fact_service: Optional[FactService] = None,
        news_service: Optional[NewsService] = None,
        reminders_service: Optional[RemindersService] = None,
        web_search_service: Optional[WebSearchService] = None,
        habit_service: Optional[HabitService] = None,
        url_ingestion_service: Optional[URLIngestionService] = None,
        email_service: Optional[EmailService] = None,
        schedule_todo_callback: Optional[Callable[[dict[str, Any]], None]] = None,
        twilio_daily_message_limit: int = 50,
        max_prompt_chunks: int = 5,
        assistant_name: str = "Sage",
        enable_tools: bool = True,
    ):
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._registry = registry
        self._fact_service = fact_service
        self._news_service = news_service
        self._reminders_service = reminders_service
        self._web_search_service = web_search_service
        self._habit_service = habit_service
        self._url_ingestion_service = url_ingestion_service
        self._email_service = email_service
        self._schedule_todo_callback = schedule_todo_callback
        self._twilio_daily_message_limit = twilio_daily_message_limit
        self._max_prompt_chunks = max_prompt_chunks
        self._assistant_name = assistant_name
        self._enable_tools = enable_tools

        # Tool registry and executor for open source model
        self._tool_registry = ToolRegistry(
            news_service=news_service,
            fact_service=fact_service,
            registry=registry,
            reminders_service=reminders_service,
            schedule_todo_callback=schedule_todo_callback,
            retriever=retriever,
            web_search_service=web_search_service,
            habit_service=habit_service,
        )
        self._tool_executor = ToolExecutor(self._tool_registry)

        # In-memory cache of news articles per session for follow-up questions
        self._session_news: dict[str, list[dict]] = {}

    @staticmethod
    def _is_conversational(question: str) -> bool:
        """Check if a question is conversational (doesn't need document retrieval).

        Uses regex patterns to detect greetings, meta-questions, and identity questions.
        """
        q_lower = question.lower().strip()
        for pattern in ChatService.CONVERSATIONAL_PATTERNS:
            if re.search(pattern, q_lower):
                return True
        return False

    @staticmethod
    def _is_news_query(question: str) -> bool:
        """Check if a question is asking for news.

        Uses regex patterns to detect news-related queries.
        """
        q_lower = question.lower().strip()
        for pattern in ChatService.NEWS_PATTERNS:
            if re.search(pattern, q_lower):
                return True
        return False

    @staticmethod
    def _extract_news_topic(question: str) -> Optional[str]:
        """Extract the news topic from a question.

        Args:
            question: The user question

        Returns:
            The extracted topic or None if not found
        """
        q_lower = question.lower().strip()

        # Try to extract from common patterns
        patterns = [
            r"(what('s| is) the news (on|about|for|regarding)\s+)(.+)",
            r"(latest|recent|current|today'?s?) news (on|about|for|regarding)\s+(.+)",
            r"(any )?news (on|about|for|regarding)\s+(.+)",
            r"what happened (to|with|in)\s+(.+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, q_lower)
            if match:
                topic = match.group(match.lastindex)  # Get last capture group
                # Clean up common suffixes
                topic = re.sub(r"\s+(today|yesterday|this week|right now)", "", topic).strip()
                return topic

        return None

    def answer_in_session(
        self,
        session_id: str,
        question: str,
        top_k: Optional[int] = None,
        response_style: Optional[str] = None,
    ) -> QAResult:
        """Answer a question within a chat session with full conversation history.

        Uses tool calling if enabled, allowing the model to call tools (news, todos, etc.)
        based on natural language understanding. Falls back to document retrieval for
        research questions.

        Args:
            session_id: The chat session ID
            question: The user's question
            top_k: Override number of retrieved chunks

        Returns:
            QAResult with the answer and sources
        """
        history = self.get_history(session_id)

        url_answer = self._answer_url_ingestion(question, response_style=response_style)
        if url_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=url_answer,
                history=history,
            )

        reminder_answer = self._answer_direct_reminder_request(question, response_style=response_style)
        if reminder_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=reminder_answer,
                history=history,
            )

        direct_answer = self._answer_direct_command(question, response_style=response_style)
        if direct_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=direct_answer,
                history=history,
            )

        email_answer = self._answer_email_request(question, response_style=response_style)
        if email_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=email_answer,
                history=history,
            )

        news_result = self._answer_news_query(question, response_style=response_style)
        if news_result is not None:
            answer, news_articles = news_result
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=answer,
                history=history,
                sources_used=bool(news_articles),
                news_articles=news_articles,
            )

        # Build messages with tool-aware system prompt
        if self._enable_tools:
            tool_schemas = self._tool_registry.to_schemas()
            learned_facts_list = []
            if self._fact_service:
                personal = self._fact_service.get_relevant_facts("personal", limit=5)
                work = self._fact_service.get_relevant_facts("work", limit=5)
                learned_facts_list = [{"content": f.content} for f in (personal + work)]
            system_message = build_system_message_with_tools(
                assistant_name=self._assistant_name,
                tools_schemas=tool_schemas,
                learned_facts=learned_facts_list if learned_facts_list else None,
                response_style=self._resolve_response_style(response_style),
            )
        else:
            system_message = (
                f"You are {self._assistant_name} — a wise, knowledgeable personal companion.\n"
                "Be thoughtful, direct, and helpful."
            )

        messages = [{"role": "system", "content": system_message}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        # Tool-calling loop
        answer = ""
        news_articles: List[NewsArticle] = []
        web_sources: List[dict] = []
        chunks = []
        sources = []
        retrieval = RetrievalResult(question=question, chunks=[], top_k=0)

        if self._enable_tools:
            self._tool_executor.reset()
            tool_calls_made = []

            # Loop until model stops calling tools or max calls reached
            while self._tool_executor.call_count < self._tool_executor.max_calls:
                # Get model response
                response = self._chat_provider.chat(messages=messages)

                # Check for tool calls
                tool_result, response_text = self._tool_executor.process_model_output(response)

                if tool_result:
                    # Tool was called: add to conversation history and loop
                    tool_calls_made.append((tool_result.tool_name, tool_result.parameters))

                    # Track news articles if fetch_news was called
                    if tool_result.tool_name == "fetch_news" and self._news_service:
                        query = tool_result.parameters.get("query", "")
                        fetched_articles = (
                            self._news_service.search_news(query)
                            if query
                            else self._news_service.get_top_news()
                        )
                        if fetched_articles:
                            news_articles.extend(fetched_articles)

                    # Track web search results if web_search was called
                    if tool_result.tool_name == "web_search":
                        raw = tool_result.result.get("raw_results", [])
                        web_sources.extend(raw)

                    tool_output_msg = f"Tool result for {tool_result.tool_name}:\n{tool_result.output}"
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": tool_output_msg})
                else:
                    # No tool call: model gave final response
                    answer = response_text or response
                    break
            else:
                # Max calls reached
                answer = response_text or response

            # If no answer was generated, use the last response
            if not answer and messages and messages[-1].get("role") == "assistant":
                answer = messages[-1].get("content", "I couldn't generate a response.")
        else:
            # No tool calling: use traditional approach with RAG
            if self._is_conversational(question):
                chunks = []
                retrieval = RetrievalResult(question=question, chunks=[], top_k=0)
            else:
                retrieval = self._retriever.retrieve(question=question, top_k=top_k)
                chunks = retrieval.chunks

            learned_facts_list = []
            if self._fact_service:
                personal_facts = self._fact_service.get_relevant_facts("personal", limit=3)
                work_facts = self._fact_service.get_relevant_facts("work", limit=3)
                learned_facts_list = [
                    {"content": f.content} for f in (personal_facts + work_facts)
                ]

            messages = build_chat_messages(
                question=question,
                chunks=chunks,
                history=history,
                max_chunks=self._max_prompt_chunks,
                assistant_name=self._assistant_name,
                learned_facts=learned_facts_list if learned_facts_list else None,
                response_style=self._resolve_response_style(response_style),
            )

            answer = self._chat_provider.chat(messages=messages)

        return self._record_answer(
            session_id=session_id,
            question=question,
            answer=answer,
            history=history,
            sources=sources,
            retrieval=retrieval,
            sources_used=bool(news_articles or web_sources or chunks),
            news_articles=news_articles,
            web_sources=web_sources,
        )

    def _record_answer(
        self,
        session_id: str,
        question: str,
        answer: str,
        history: List[dict],
        sources: Optional[list] = None,
        retrieval: Optional[RetrievalResult] = None,
        sources_used: bool = False,
        news_articles: Optional[List[NewsArticle]] = None,
        web_sources: Optional[List[dict]] = None,
    ) -> QAResult:
        """Persist a user/assistant exchange and return the standard result shape."""
        user_turn_id = str(uuid.uuid4())
        assistant_turn_id = str(uuid.uuid4())

        turn_index = len(history)
        self._registry.append_turn(
            session_id=session_id,
            turn_id=user_turn_id,
            role="user",
            content=question,
            turn_index=turn_index,
        )

        self._registry.append_turn(
            session_id=session_id,
            turn_id=assistant_turn_id,
            role="assistant",
            content=answer,
            turn_index=turn_index + 1,
        )

        retrieval = retrieval or RetrievalResult(question=question, chunks=[], top_k=0)
        news_articles = news_articles or []
        web_sources = web_sources or []
        return QAResult(
            question=question,
            answer=answer,
            sources=sources or [],
            retrieval=retrieval,
            prompt="",
            sources_used=sources_used,
            news_sources=[{"title": a.title, "source": a.source, "url": a.url} for a in news_articles],
            web_sources=web_sources,
        )

    @classmethod
    def _resolve_response_style(cls, response_style: Optional[str]) -> Optional[str]:
        if response_style == "whatsapp":
            return cls.WHATSAPP_RESPONSE_STYLE
        return response_style

    @staticmethod
    def _is_whatsapp_style(response_style: Optional[str]) -> bool:
        return response_style == "whatsapp"

    def _answer_url_ingestion(
        self, question: str, response_style: Optional[str] = None
    ) -> Optional[str]:
        if not self._url_ingestion_service:
            return None
        if not self._url_ingestion_service.is_ingest_intent(question):
            return None
        url = self._url_ingestion_service.extract_url(question)
        if not url:
            return None
        result = self._url_ingestion_service.ingest(url)
        return self._url_ingestion_service.format_confirmation(
            result, whatsapp=self._is_whatsapp_style(response_style)
        )

    def _answer_direct_command(
        self, question: str, response_style: Optional[str] = None
    ) -> Optional[str]:
        """Handle slash commands without relying on model tool selection."""
        command = question.strip()
        lowered = command.lower()

        if lowered in ("/sources",) or "what have you saved" in lowered or "what did you save" in lowered:
            return self._sources_command(response_style=response_style)

        if not lowered.startswith("/"):
            return None

        if lowered.startswith("/remember-personal "):
            return self._remember_fact_command(
                command, "/remember-personal ", "personal", response_style=response_style
            )
        if lowered.startswith("/remember-work "):
            return self._remember_fact_command(
                command, "/remember-work ", "work", response_style=response_style
            )
        if lowered in ("/remember-personal", "/remember-work"):
            usage = f"Usage: {lowered} <fact>"
            return f"📝 {usage}" if self._is_whatsapp_style(response_style) else usage

        if lowered.startswith("/facts"):
            return self._facts_command(lowered, response_style=response_style)

        if lowered == "/usage":
            return self._usage_command(response_style=response_style)

        if lowered.startswith("/forget "):
            if not self._fact_service:
                return self._style_status("Fact memory is not configured.", "⚠️", response_style)
            fact_id = command[len("/forget "):].strip()
            if not fact_id:
                return self._style_status("Usage: /forget <fact-id>", "📝", response_style)
            self._fact_service.forget(fact_id)
            return self._style_status("Fact forgotten.", "🗑️", response_style)

        if lowered == "/todo" or lowered.startswith("/todo "):
            args = command[len("/todo"):].strip()
            return self._todo_command(args, response_style=response_style)

        if lowered == "/apple-reminder" or lowered.startswith("/apple-reminder "):
            args = command[len("/apple-reminder"):].strip()
            return self._apple_reminder_command(args, response_style=response_style)

        if lowered == "/habits":
            if not self._habit_service:
                return self._style_status("Habit tracking is not configured.", "⚠️", response_style)
            return self._format_habit_summary(response_style=response_style)

        if lowered.startswith("/habit add "):
            if not self._habit_service:
                return self._style_status("Habit tracking is not configured.", "⚠️", response_style)
            args = command[len("/habit add "):].strip()
            name, reminder_time = self._parse_habit_reminder_time(args)
            if not name:
                return self._style_status("Usage: /habit add <name> [@time]", "📝", response_style)
            habit = self._habit_service.add_habit(name=name, reminder_time=reminder_time)
            return self._style_status(
                f"Habit '{habit.name}' added (reminder at {habit.reminder_time}).",
                "✅",
                response_style,
            )

        if lowered.startswith("/habit log "):
            if not self._habit_service:
                return self._style_status("Habit tracking is not configured.", "⚠️", response_style)
            args = command[len("/habit log "):].strip()
            if not args:
                return self._style_status("Usage: /habit log <name> [skipped]", "📝", response_style)
            status = "done"
            name = args
            if args.lower().endswith(" skipped"):
                status = "skipped"
                name = args[:-len(" skipped")].strip()
            try:
                log = self._habit_service.log_habit(name=name, status=status)
            except ValueError as exc:
                return str(exc)
            verb = "skipped" if log.status == "skipped" else "logged for today"
            return self._style_status(f"Habit '{name}' {verb}.", "✅", response_style)

        if lowered.startswith("/habit unlog "):
            if not self._habit_service:
                return self._style_status("Habit tracking is not configured.", "⚠️", response_style)
            name = command[len("/habit unlog "):].strip()
            if not name:
                return self._style_status("Usage: /habit unlog <name>", "📝", response_style)
            try:
                deleted = self._habit_service.unlog_habit(name)
            except ValueError as exc:
                return str(exc)
            if deleted:
                return self._style_status(f"Removed today's log for '{name}'.", "↩️", response_style)
            return self._style_status(f"No log found for '{name}' today.", "ℹ️", response_style)

        if lowered.startswith("/habit delete "):
            if not self._habit_service:
                return self._style_status("Habit tracking is not configured.", "⚠️", response_style)
            name = command[len("/habit delete "):].strip()
            if not name:
                return self._style_status("Usage: /habit delete <name>", "📝", response_style)
            if self._habit_service.delete_habit(name):
                return self._style_status(f"Habit '{name}' removed.", "🗑️", response_style)
            return self._style_status(f"Habit '{name}' not found.", "ℹ️", response_style)

        if lowered.startswith("/habit"):
            help_text = (
                "Habit commands:\n"
                "/habit add <name> [@time]\n"
                "/habit log <name> [skipped]\n"
                "/habit unlog <name>\n"
                "/habit delete <name>\n"
                "/habits"
            )
            return f"🌱 *Habit commands*\n{help_text}" if self._is_whatsapp_style(response_style) else help_text

        return None

    def _sources_command(self, response_style: Optional[str] = None) -> str:
        sources = self._registry.list_all_sources()
        if not sources:
            return self._style_status("Nothing saved yet. Paste a URL or ingest a file to get started.", "📚", response_style)
        url_sources = [s for s in sources if s.get("source_type") == "url"]
        local_sources = [s for s in sources if s.get("source_type") != "url"]
        lines = []
        if self._is_whatsapp_style(response_style):
            lines.append(f"📚 *Your saved sources ({len(sources)})*")
        else:
            lines.append(f"📚 Your saved sources ({len(sources)}):")
        idx = 1
        for s in url_sources:
            from urllib.parse import urlparse as _up
            domain = _up(s.get("source_url") or "").netloc or s.get("source_url", "")
            lines.append(f"[{idx}] {s.get('file_name', 'untitled')} — {domain} 🌐")
            idx += 1
        for s in local_sources:
            lines.append(f"[{idx}] {s.get('file_name', s.get('source_path', 'untitled'))} 📄")
            idx += 1
        return "\n".join(lines)

    def _answer_direct_reminder_request(
        self, question: str, response_style: Optional[str] = None
    ) -> Optional[str]:
        if "apple reminder" in question.lower() or "apple reminders" in question.lower():
            return None

        parsed = parse_reminder_request(question)
        if parsed is None:
            return None

        task, due_at = parsed
        todo = self._registry.create_todo(title=task, due_at=due_at)
        if due_at and self._schedule_todo_callback:
            self._schedule_todo_callback(todo)
        due_str = f" due {due_at.strftime('%a, %b %d at %I:%M%p')}" if due_at else ""
        return self._style_status(f"Added Sage reminder: {todo['title']}{due_str}.", "✅", response_style)

    def _todo_command(self, args: str, response_style: Optional[str] = None) -> str:
        if not args:
            return self._style_status("Usage: /todo <task> [#list] [@due-date]", "📝", response_style)
        task, list_name, due_at = self._parse_task_list_and_due_date(args)
        if not task:
            return self._style_status("Usage: /todo <task> [#list] [@due-date]", "📝", response_style)
        todo = self._registry.create_todo(title=task, list_name=list_name, due_at=due_at)
        if due_at and self._schedule_todo_callback:
            self._schedule_todo_callback(todo)
        due_str = f" due {due_at.strftime('%a, %b %d at %I:%M%p')}" if due_at else ""
        return self._style_status(f"Added Sage reminder: {todo['title']}{due_str}.", "✅", response_style)

    def _apple_reminder_command(self, args: str, response_style: Optional[str] = None) -> str:
        if not self._reminders_service:
            return self._style_status("Apple Reminders is not configured.", "⚠️", response_style)
        if not args:
            return self._style_status("Usage: /apple-reminder <task> [#list] [@due-date]", "📝", response_style)
        task, list_name, due_at = self._parse_task_list_and_due_date(args)
        if not task:
            return self._style_status("Usage: /apple-reminder <task> [#list] [@due-date]", "📝", response_style)
        target_list = self._reminders_service.add_reminder(task=task, list_name=list_name, due_date=due_at)
        due_str = f" due {due_at.strftime('%a, %b %d at %I:%M%p')}" if due_at else ""
        return self._style_status(f"Added to Apple Reminders {target_list}: {task}{due_str}.", "✅", response_style)

    def _style_status(self, text: str, emoji: str, response_style: Optional[str]) -> str:
        if self._is_whatsapp_style(response_style):
            return f"{emoji} {text}"
        return text

    def _usage_command(self, response_style: Optional[str] = None) -> str:
        chat_usage = self._registry.get_chat_usage_today()
        twilio_usage = self._registry.get_whatsapp_usage_today(
            daily_limit=self._twilio_daily_message_limit
        )
        text = "\n".join(
            [
                "Usage today",
                f"CLI chats: {chat_usage['cli']}",
                f"WhatsApp chats: {chat_usage['whatsapp']}",
                (
                    "Twilio WhatsApp outbound before this reply: "
                    f"{twilio_usage['sent_count']}/{twilio_usage['daily_limit']} "
                    f"messages used, {twilio_usage['remaining']} remaining."
                ),
            ]
        )
        if chat_usage["other"]:
            text += f"\nOther chats: {chat_usage['other']}"
        return self._style_status(text, "📊", response_style)

    def _answer_email_request(
        self, question: str, response_style: Optional[str] = None
    ) -> Optional[str]:
        """Fetch and triage Gmail if the question is an email request."""
        if not _is_email_request(question):
            return None
        if not self._email_service:
            return self._style_status(
                "Gmail isn't configured yet. Run `sage email-personal` from the CLI to set up OAuth, "
                "then restart the server.",
                "📭",
                response_style,
            )
        try:
            emails = self._email_service.fetch_recent()
        except FileNotFoundError:
            return self._style_status(
                "Gmail credentials not found. Run `sage email-personal` from the CLI first to authorise access.",
                "🔑",
                response_style,
            )
        except Exception as exc:
            return self._style_status(f"Failed to fetch emails: {exc}", "⚠️", response_style)

        if not emails:
            return self._style_status("Your inbox is empty — nothing to report.", "📭", response_style)

        try:
            triaged = self._email_service.triage(emails, self._chat_provider)
        except Exception as exc:
            return self._style_status(f"Fetched {len(emails)} emails but triage failed: {exc}", "⚠️", response_style)

        action_items = [t for t in triaged if t.category == "action"]
        fyi_items    = [t for t in triaged if t.category == "fyi"]

        lines = [f"Checked {len(emails)} emails. Here's what needs your attention:\n"]
        if action_items:
            lines.append(f"**ACTION NEEDED ({len(action_items)})**")
            for i, item in enumerate(action_items, 1):
                lines.append(f"{i}. **{item.email.sender}** — {item.email.subject}\n   → {item.reason}")
            lines.append("")
        else:
            lines.append("No action needed.\n")

        if fyi_items:
            lines.append(f"**FYI ({len(fyi_items)})**")
            for i, item in enumerate(fyi_items, 1):
                lines.append(f"{i}. **{item.email.sender}** — {item.email.subject}\n   → {item.reason}")

        return self._style_status("\n".join(lines), None, response_style)

    def _answer_news_query(
        self, question: str, response_style: Optional[str] = None
    ) -> Optional[tuple[str, List[NewsArticle]]]:
        if not self._news_service:
            return None

        stripped = question.strip()
        lowered = stripped.lower()
        explicit_news = lowered == "/news" or lowered.startswith("/news ")
        if not explicit_news and not self._is_news_query(stripped):
            return None

        query = stripped[len("/news"):].strip() if explicit_news else (self._extract_news_topic(stripped) or stripped)
        articles = self._news_service.search_news(query) if query else self._news_service.get_top_news()
        if not articles:
            topic = query or "top news"
            return self._style_status(f"No news found for '{topic}'.", "📰", response_style), []

        summary = self._news_service.generate_summary(articles, self._chat_provider)
        return self._format_news_answer(query=query, summary=summary, articles=articles, response_style=response_style), articles

    def _format_news_answer(
        self,
        query: str,
        summary: str,
        articles: List[NewsArticle],
        response_style: Optional[str] = None,
    ) -> str:
        title = f"Latest News: {query}" if query else "Top News Today"
        if self._is_whatsapp_style(response_style):
            lines = [f"📰 *{title}*", "", f"⚡ *Summary*\n{summary.strip()}", "", "🔗 *Sources*"]
            for i, article in enumerate(articles, 1):
                lines.append(f"{i}. *{article.title}*")
                lines.append(f"   {article.source}")
                lines.append(f"   {article.url}")
            return "\n".join(lines)

        lines = [title, "", "Summary:", summary.strip(), "", "Sources:"]
        for i, article in enumerate(articles, 1):
            lines.append(f"{i}. {article.title}")
            lines.append(f"   {article.source} | {article.published}")
            lines.append(f"   {article.url}")
        return "\n".join(lines)

    def _remember_fact_command(
        self, command: str, prefix: str, category: str, response_style: Optional[str] = None
    ) -> str:
        if not self._fact_service:
            return self._style_status("Fact memory is not configured.", "⚠️", response_style)
        fact_text = command[len(prefix):].strip()
        if not fact_text:
            return self._style_status(f"Usage: {prefix.strip()} <fact>", "📝", response_style)
        self._fact_service.remember(content=fact_text, category=category)
        return self._style_status(f"{category.title()} fact saved: {fact_text}", "🧠", response_style)

    def _facts_command(self, lowered: str, response_style: Optional[str] = None) -> str:
        if not self._fact_service:
            return self._style_status("Fact memory is not configured.", "⚠️", response_style)
        parts = lowered.split()
        category = parts[1] if len(parts) > 1 else None
        if category == "all":
            category = None
        facts = self._fact_service.list_facts(category=category)
        if not facts:
            return self._style_status(
                "No facts learned yet. Use /remember-personal or /remember-work.",
                "ℹ️",
                response_style,
            )
        label = category.title() if category else "All"
        if self._is_whatsapp_style(response_style):
            lines = [f"🧠 *Learned Facts - {label}*"]
        else:
            lines = [f"Learned Facts - {label}:"]
        for i, fact in enumerate(facts[:20], 1):
            lines.append(f"{i}. {fact.content} ({fact.category})")
        return "\n".join(lines)

    @staticmethod
    def _parse_habit_reminder_time(args: str) -> tuple[str, str]:
        match = re.search(r"@(\S+)", args)
        if match:
            time_str = match.group(1)
            name = args[: match.start()].strip()
            return name, time_str
        return args.strip(), "21:00"

    @staticmethod
    def _parse_task_list_and_due_date(args: str) -> tuple[str, Optional[str], Optional[datetime]]:
        working = args.strip()
        list_name = None
        due_at = None

        if "#" in working:
            task_part, list_part = working.rsplit("#", 1)
            if "@" in list_part:
                list_only, date_only = list_part.split("@", 1)
                list_name = list_only.strip() or None
                working = f"{task_part.strip()} @{date_only}"
            else:
                list_name = list_part.strip() or None
                working = task_part.strip()

        if "@" in working:
            task_part, date_part = working.rsplit("@", 1)
            due_at = parse_due_date(date_part.strip())
            working = task_part.strip()

        return working, list_name, due_at

    def _format_habit_summary(self, response_style: Optional[str] = None) -> str:
        summaries = self._habit_service.get_weekly_summary() if self._habit_service else []
        week_label = date.today().strftime("Week of %b %-d, %Y")
        if self._is_whatsapp_style(response_style):
            lines = [f"📊 *Habit Summary* — {week_label}"]
        else:
            lines = [f"Habit Summary - {week_label}"]

        if not summaries:
            lines.append("")
            prefix = "🌱 " if self._is_whatsapp_style(response_style) else ""
            lines.append(f"{prefix}No habits tracked yet. Add one with /habit add <name>.")
            return "\n".join(lines)

        total_done = 0
        total_possible = len(summaries) * 7
        lines.append("")
        for summary in summaries:
            filled = round(summary.days_done / 7 * 10)
            bar = "█" * filled + "░" * (10 - filled)
            total_done += summary.days_done
            if summary.streak > 0:
                streak_label = f"🔥 {summary.streak}-day streak" if self._is_whatsapp_style(response_style) else f"{summary.streak}-day streak"
            else:
                streak_label = "💤 streak broken" if self._is_whatsapp_style(response_style) else "streak broken"
            lines.append(
                f"{summary.habit.name:<14} {bar}   "
                f"{summary.days_done}/7 days   {streak_label}"
            )
        lines.append("")
        total_prefix = "✅ " if self._is_whatsapp_style(response_style) else ""
        lines.append(f"{total_prefix}Total logged this week: {total_done}/{total_possible}")
        return "\n".join(lines)

    def create_session(self, session_id: str, title: str = "") -> None:
        """Create a new chat session.

        Args:
            session_id: Unique session identifier
            title: Optional session title
        """
        self._registry.create_session(session_id=session_id, title=title)

    def generate_session_title(self, session_id: str) -> str:
        """Ask the LLM to summarise the session in ≤5 words, persist, and return it."""
        turns = self._registry.get_session_turns(session_id)
        if not turns:
            return ""

        # Build a compact transcript (first 6 turns max to keep the prompt tiny)
        lines = []
        for t in turns[:6]:
            role = "User" if t["role"] == "user" else "Sage"
            lines.append(f"{role}: {t['content'][:200]}")
        transcript = "\n".join(lines)

        prompt = (
            "Generate a short title for this conversation. "
            "The title must be 5 words or fewer, sentence case, no punctuation, no quotes.\n\n"
            f"{transcript}\n\nTitle:"
        )
        try:
            raw = self._chat_provider.generate(prompt)
            title = raw.strip().strip('"\'').strip()
            # Hard-cap at 60 chars in case the model misbehaves
            title = title[:60]
        except Exception:
            return ""

        if title:
            self._registry.update_session_title(session_id=session_id, title=title)
        return title

    def list_sessions(self, limit: int = 20) -> List[dict]:
        """List recent chat sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of session dicts with session_id, title, created_at, updated_at
        """
        return self._registry.list_sessions(limit=limit)

    def get_history(self, session_id: str) -> List[dict]:
        """Get conversation history for a session as messages.

        Args:
            session_id: The chat session ID

        Returns:
            List of dicts with "role" and "content" keys
        """
        turns = self._registry.get_session_turns(session_id)
        return [{"role": turn["role"], "content": turn["content"]} for turn in turns]

    def get_fact_service(self) -> Optional[FactService]:
        """Get the fact service for external use."""
        return self._fact_service

    def get_web_search_service(self) -> Optional[WebSearchService]:
        """Get the web search service for external use."""
        return self._web_search_service

    def get_registry(self) -> SQLiteRegistry:
        """Get the registry for external use."""
        return self._registry

    def get_habit_service(self) -> Optional[HabitService]:
        """Get the habit service for external use."""
        return self._habit_service
