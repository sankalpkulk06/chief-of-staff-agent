import re
import uuid
from datetime import date, datetime
from typing import Any, Callable, List, Optional

from app.core.qa_service import QAResult
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.services.email_service import EmailService
from app.services.news_service import NewsService, NewsArticle
from app.core.todo_parser import parse_due_date, parse_reminder_request
from app.services.web_search_service import WebSearchService
from app.services.url_ingestion_service import URLIngestionService
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever, RetrievalResult
from app.agents.runner import AgentRunner
from app.agents.security_agent import SecurityAgent

_HISTORY_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"forget\s+everything", re.IGNORECASE),
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"\boverride\s*:", re.IGNORECASE),
    re.compile(r"\bDAN\b"),
]


def _looks_like_history_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _HISTORY_INJECTION_PATTERNS)


class ChatService:
    """Session-aware chat service with conversation history and persistence."""

    WHATSAPP_RESPONSE_STYLE = (
        "Format replies for WhatsApp: easy to scan on a phone, warm, casual, and concise. "
        "Use friendly emojis as visual anchors, especially at the start of short sections or status lines. "
        "Prefer short paragraphs or compact bullets. Avoid dense blocks of text. "
        "Use WhatsApp markdown lightly (*bold* for labels, backticks for commands). "
        "Do not overdo it: 1-4 useful emojis is usually enough unless the user is celebrating."
    )

    def __init__(
        self,
        retriever: Retriever,
        chat_provider: OllamaChatProvider,
        registry: Any,
        user_id: str,
        agent_chat_providers: Optional[dict[str, Any]] = None,
        agent_model_specs: Optional[dict[str, str]] = None,
        fact_service: Optional[FactService] = None,
        news_service: Optional[NewsService] = None,
        web_search_service: Optional[WebSearchService] = None,
        habit_service: Optional[HabitService] = None,
        url_ingestion_service: Optional[URLIngestionService] = None,
        email_service: Optional[EmailService] = None,
        schedule_todo_callback: Optional[Callable[[dict[str, Any]], None]] = None,
        twilio_daily_message_limit: int = 50,
        max_prompt_chunks: int = 5,
        assistant_name: str = "Sage",
        enable_tools: bool = True,
        rag_fallback_distance_threshold: float = 0.5,
        security_agent: Optional[SecurityAgent] = None,
    ):
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._agent_model_specs = agent_model_specs or {}
        self._registry = registry
        self._fact_service = fact_service
        self._news_service = news_service
        self._web_search_service = web_search_service
        self._habit_service = habit_service
        self._url_ingestion_service = url_ingestion_service
        self._email_service = email_service
        self._schedule_todo_callback = schedule_todo_callback
        self._twilio_daily_message_limit = twilio_daily_message_limit
        self._max_prompt_chunks = max_prompt_chunks
        self._assistant_name = assistant_name
        self._enable_tools = enable_tools
        self._user_id = user_id
        self._rag_fallback_distance_threshold = rag_fallback_distance_threshold
        self._security_agent = security_agent

        self._agent_runner = AgentRunner(
            chat_provider=chat_provider,
            agent_chat_providers=agent_chat_providers,
            agent_model_specs=self._agent_model_specs,
            retriever=retriever,
            registry=registry,
            fact_service=fact_service,
            news_service=news_service,
            web_search_service=web_search_service,
            habit_service=habit_service,
            email_service=email_service,
            schedule_todo_callback=schedule_todo_callback,
            assistant_name=assistant_name,
            rag_top_k=max_prompt_chunks,
            rag_fallback_threshold=rag_fallback_distance_threshold,
            security_agent=security_agent,
        )

        # In-memory cache of news articles per session for follow-up questions
        self._session_news: dict[str, list[dict]] = {}

    def answer_in_session(
        self,
        session_id: str,
        question: str,
        top_k: Optional[int] = None,
        response_style: Optional[str] = None,
        user_id: Optional[str] = None,
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
        effective_uid = user_id or self._user_id
        history = self.get_history(session_id)

        # URL + question → ingest the page, then answer from that page immediately.
        url_question_answer = self._answer_url_question(
            question,
            top_k=top_k,
            response_style=response_style,
        )
        if url_question_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=url_question_answer,
                history=history,
                sources_used=True,
            )

        # URL paste → ingest immediately, no agent needed
        url_answer = self._answer_url_ingestion(question, response_style=response_style)
        if url_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=url_answer,
                history=history,
            )

        # Explicit slash commands (/todo, /facts, /habits, etc.) → bypass agents
        direct_answer = self._answer_direct_command(
            question, response_style=response_style, user_id=effective_uid
        )
        if direct_answer is not None:
            return self._record_answer(
                session_id=session_id,
                question=question,
                answer=direct_answer,
                history=history,
            )

        # All other natural language → multi-agent orchestration: plan → dispatch → synthesize
        run = self._agent_runner.run(
            question=question,
            history=history,
            response_style=self._resolve_response_style(response_style),
            top_k=top_k,
            user_id=effective_uid,
        )

        if "blocked" in run.security_flags:
            retrieval = RetrievalResult(question=question, chunks=[], top_k=0)
            return QAResult(
                question=question,
                answer=run.output,
                sources=[],
                retrieval=retrieval,
                prompt="",
                sources_used=False,
                steps=run.steps_summary,
            )

        # Collect citations from agent results for the QAResult
        web_sources = [
            c for r in run.agent_results
            for c in r.citations
            if c.get("url") and not c.get("snippet")  # web / news citations have URLs
        ]

        # Detect HITL pending state from any action_agent result
        hitl_pending = False
        hitl_id = None
        for r in run.agent_results:
            if r.metadata.get("hitl_pending"):
                hitl_pending = True
                hitl_id = r.metadata.get("hitl_id")
                break

        return self._record_answer(
            session_id=session_id,
            question=question,
            answer=run.output,
            history=history,
            sources_used=bool(run.citations),
            web_sources=web_sources,
            agent_steps=run.steps_summary,
            hitl_pending=hitl_pending,
            hitl_id=hitl_id,
        )

    def _record_answer(
        self,
        session_id: str,
        question: str,
        answer: str,
        history: List[dict],
        sources_used: bool = False,
        news_articles: Optional[List[NewsArticle]] = None,
        web_sources: Optional[List[dict]] = None,
        agent_steps: Optional[list] = None,
        hitl_pending: bool = False,
        hitl_id: Optional[str] = None,
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

        news_articles = news_articles or []
        web_sources = web_sources or []
        retrieval = RetrievalResult(question=question, chunks=[], top_k=0)
        return QAResult(
            question=question,
            answer=answer,
            sources=[],
            retrieval=retrieval,
            prompt="",
            sources_used=sources_used,
            news_sources=[{"title": a.title, "source": a.source, "url": a.url} for a in news_articles],
            web_sources=web_sources,
            steps=agent_steps or [],
            hitl_pending=hitl_pending,
            hitl_id=hitl_id,
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
        result = self._url_ingestion_service.ingest(url, user_id=self._user_id)
        return self._url_ingestion_service.format_confirmation(
            result, whatsapp=self._is_whatsapp_style(response_style)
        )

    def _answer_url_question(
        self,
        question: str,
        top_k: Optional[int] = None,
        response_style: Optional[str] = None,
    ) -> Optional[str]:
        if not self._url_ingestion_service:
            return None

        url = self._url_ingestion_service.extract_url(question)
        if not url:
            return None

        if self._url_ingestion_service.is_url(question):
            return None

        if self._url_ingestion_service.is_ingest_intent(question):
            return None

        article_question = question.replace(url, " ", 1).strip(" \t\n\r,.-")
        if not article_question:
            return None

        ingest_result = self._url_ingestion_service.ingest(url, user_id=self._user_id)
        if not ingest_result.success and not ingest_result.already_existed:
            return self._url_ingestion_service.format_confirmation(
                ingest_result,
                whatsapp=self._is_whatsapp_style(response_style),
            )

        retrieval = self._retriever.retrieve(
            question=article_question,
            top_k=max(top_k or self._max_prompt_chunks, 10),
            user_id=self._user_id,
            source_url=url,
        )
        if not retrieval.chunks:
            return (
                "I saved the article, but I couldn't find enough relevant text in it "
                f"to answer: {article_question}"
            )

        chunk_lines = []
        for i, chunk in enumerate(retrieval.chunks, 1):
            source = chunk.file_name or chunk.source_url or "the article"
            chunk_lines.append(f"[{i}] {source}\n{chunk.text.strip()[:900]}")
        context = "\n\n".join(chunk_lines)

        style = self._resolve_response_style(response_style)
        style_instruction = f"\n\nResponse style:\n{style}" if style else ""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Answer using only the article excerpts provided. "
                    "If the excerpts do not contain the answer, say that clearly. "
                    "Keep the answer grounded in the article."
                    f"{style_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Article URL: {url}\n"
                    f"Question: {article_question}\n\n"
                    f"Article excerpts:\n{context}\n\n"
                    "Answer:"
                ),
            },
        ]
        return self._chat_provider.chat(messages=messages)

    def _answer_direct_command(
        self, question: str, response_style: Optional[str] = None, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Handle slash commands without relying on model tool selection."""
        from app.core.fact_service import FactService
        effective_uid = user_id or self._user_id
        # Create per-call user-scoped services so commands use the right user's data
        habit_svc = HabitService(self._registry, user_id=effective_uid)
        fact_svc = FactService(self._registry, user_id=effective_uid)

        command = question.strip()
        lowered = command.lower()

        if lowered in ("/sources",) or "what have you saved" in lowered or "what did you save" in lowered:
            return self._sources_command(response_style=response_style)

        if not lowered.startswith("/"):
            return None

        if lowered.startswith("/remember-personal "):
            return self._remember_fact_command(
                command, "/remember-personal ", "personal", response_style=response_style, fact_svc=fact_svc
            )
        if lowered.startswith("/remember-work "):
            return self._remember_fact_command(
                command, "/remember-work ", "work", response_style=response_style, fact_svc=fact_svc
            )
        if lowered in ("/remember-personal", "/remember-work"):
            usage = f"Usage: {lowered} <fact>"
            return f"📝 {usage}" if self._is_whatsapp_style(response_style) else usage

        if lowered.startswith("/facts"):
            return self._facts_command(lowered, response_style=response_style, fact_svc=fact_svc)

        if lowered == "/usage":
            return self._usage_command(response_style=response_style)

        if lowered.startswith("/forget "):
            fact_id = command[len("/forget "):].strip()
            if not fact_id:
                return self._style_status("Usage: /forget <fact-id>", "📝", response_style)
            fact_svc.forget(fact_id)
            return self._style_status("Fact forgotten.", "🗑️", response_style)

        if lowered == "/todo" or lowered.startswith("/todo "):
            args = command[len("/todo"):].strip()
            return self._todo_command(args, response_style=response_style)

        if lowered == "/apple-reminder" or lowered.startswith("/apple-reminder "):
            return self._style_status("Apple Reminders integration has been removed. Use /todo instead.", "ℹ️", response_style)

        if lowered == "/habits":
            return self._format_habit_summary(response_style=response_style, habit_svc=habit_svc)

        if lowered.startswith("/habit add "):
            args = command[len("/habit add "):].strip()
            name, reminder_time = self._parse_habit_reminder_time(args)
            if not name:
                return self._style_status("Usage: /habit add <name> [@time]", "📝", response_style)
            habit = habit_svc.add_habit(name=name, reminder_time=reminder_time)
            return self._style_status(
                f"Habit '{habit.name}' added (reminder at {habit.reminder_time}).",
                "✅",
                response_style,
            )

        if lowered.startswith("/habit log "):
            args = command[len("/habit log "):].strip()
            if not args:
                return self._style_status("Usage: /habit log <name> [skipped]", "📝", response_style)
            status = "done"
            name = args
            if args.lower().endswith(" skipped"):
                status = "skipped"
                name = args[:-len(" skipped")].strip()
            try:
                log = habit_svc.log_habit(name=name, status=status)
            except ValueError as exc:
                return str(exc)
            verb = "skipped" if log.status == "skipped" else "logged for today"
            return self._style_status(f"Habit '{name}' {verb}.", "✅", response_style)

        if lowered.startswith("/habit unlog "):
            name = command[len("/habit unlog "):].strip()
            if not name:
                return self._style_status("Usage: /habit unlog <name>", "📝", response_style)
            try:
                deleted = habit_svc.unlog_habit(name)
            except ValueError as exc:
                return str(exc)
            if deleted:
                return self._style_status(f"Removed today's log for '{name}'.", "↩️", response_style)
            return self._style_status(f"No log found for '{name}' today.", "ℹ️", response_style)

        if lowered.startswith("/habit delete "):
            name = command[len("/habit delete "):].strip()
            if not name:
                return self._style_status("Usage: /habit delete <name>", "📝", response_style)
            if habit_svc.delete_habit(name):
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

    def _remember_fact_command(
        self, command: str, prefix: str, category: str, response_style: Optional[str] = None, fact_svc=None
    ) -> str:
        svc = fact_svc or self._fact_service
        if not svc:
            return self._style_status("Fact memory is not configured.", "⚠️", response_style)
        fact_text = command[len(prefix):].strip()
        if not fact_text:
            return self._style_status(f"Usage: {prefix.strip()} <fact>", "📝", response_style)
        svc.remember(content=fact_text, category=category)
        return self._style_status(f"{category.title()} fact saved: {fact_text}", "🧠", response_style)

    def _facts_command(self, lowered: str, response_style: Optional[str] = None, fact_svc=None) -> str:
        svc = fact_svc or self._fact_service
        if not svc:
            return self._style_status("Fact memory is not configured.", "⚠️", response_style)
        parts = lowered.split()
        category = parts[1] if len(parts) > 1 else None
        if category == "all":
            category = None
        facts = svc.list_facts(category=category)
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

    def _format_habit_summary(self, response_style: Optional[str] = None, habit_svc=None) -> str:
        svc = habit_svc or self._habit_service
        summaries = svc.get_weekly_summary() if svc else []
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
        history = []
        skip_next_assistant = False
        for turn in turns:
            role = turn["role"]
            content = turn["content"]
            if role == "user" and _looks_like_history_injection(content):
                skip_next_assistant = True
                continue
            if role == "assistant" and skip_next_assistant:
                skip_next_assistant = False
                continue
            skip_next_assistant = False
            history.append({"role": role, "content": content})
        return history

    def get_fact_service(self) -> Optional[FactService]:
        """Get the fact service for external use."""
        return self._fact_service

    def get_web_search_service(self) -> Optional[WebSearchService]:
        """Get the web search service for external use."""
        return self._web_search_service

    def get_registry(self) -> Any:
        """Get the registry for external use."""
        return self._registry

    def get_user_id(self) -> str:
        return self._user_id

    def get_habit_service(self) -> Optional[HabitService]:
        """Get the habit service for external use."""
        return self._habit_service

    @staticmethod
    def normalize_agent_name(agent_name: str) -> str:
        normalized = agent_name.strip().lower().replace("-", "_")
        aliases = {
            "orchestrator": "orchestrator",
            "orchestrator_agent": "orchestrator",
            "orchestratoragent": "orchestrator",
            "planner": "orchestrator",
            "rag": "rag_agent",
            "ragagent": "rag_agent",
            "rag_agent": "rag_agent",
            "research": "research_agent",
            "researchagent": "research_agent",
            "research_agent": "research_agent",
            "action": "action_agent",
            "actionagent": "action_agent",
            "action_agent": "action_agent",
            "conversation": "conversational",
            "conversational_agent": "conversational",
            "conversationalagent": "conversational",
            "conversational": "conversational",
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(
            "Unknown agent. Use: orchestrator, rag, research, action, conversational."
        )

    def set_agent_chat_provider(self, agent_name: str, provider: Any, model_spec: str) -> str:
        normalized = self.normalize_agent_name(agent_name)
        self._agent_model_specs[normalized] = model_spec
        self._agent_runner.set_agent_provider(normalized, provider, model_spec)
        return normalized

    def get_agent_model_specs(self) -> dict[str, str]:
        return self._agent_runner.get_agent_model_specs()
