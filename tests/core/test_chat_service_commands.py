from pathlib import Path

import pytest

from app.core.chat_service import ChatService
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.schemas.document import ParsedDocument
from app.services.news_service import NewsArticle
from app.storage.sqlite_registry import SQLiteRegistry


class _UnusedRetriever:
    def retrieve(self, question, top_k=None):
        raise AssertionError("direct slash commands should not use retrieval")


class _UnusedChatProvider:
    def chat(self, messages):
        raise AssertionError("direct slash commands should not call the model")


class _ToolCallingChatProvider:
    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages):
        return next(self.responses)


@pytest.fixture
def registry(tmp_path):
    db = SQLiteRegistry(tmp_path / "registry.db")
    try:
        yield db
    finally:
        db.close()


def _service(registry):
    return ChatService(
        retriever=_UnusedRetriever(),
        chat_provider=_UnusedChatProvider(),
        registry=registry,
        fact_service=FactService(registry),
        habit_service=HabitService(registry),
    )


class _NewsChatProvider:
    def chat(self, messages):
        raise AssertionError("news queries should not rely on chat tool selection")

    def generate(self, prompt):
        return "Sam Altman and Elon Musk are in the news today."


class _StubNewsService:
    def __init__(self):
        self.queries = []

    def search_news(self, query):
        self.queries.append(query)
        return [
            NewsArticle(
                title="OpenAI Trial Live Updates",
                source="The New York Times",
                url="https://example.com/openai-trial",
                published="Wed, 29 Apr 2026 12:00:00 GMT",
                snippet="Elon Musk and Sam Altman are central to the latest coverage.",
            )
        ]

    def get_top_news(self):
        raise AssertionError("specific news queries should use search_news")

    def generate_summary(self, articles, chat_provider):
        return chat_provider.generate("summarize")


def _news_service(registry):
    return ChatService(
        retriever=_UnusedRetriever(),
        chat_provider=_NewsChatProvider(),
        registry=registry,
        fact_service=FactService(registry),
        habit_service=HabitService(registry),
        news_service=_StubNewsService(),
    )


def test_habits_command_reads_shared_habit_data(registry):
    service = _service(registry)
    service.create_session("session")

    habit_service = HabitService(registry)
    habit_service.add_habit("gym")
    habit_service.log_habit("gym")

    result = service.answer_in_session(session_id="session", question="/habits")

    assert "Habit Summary" in result.answer
    assert "gym" in result.answer
    assert "1/7 days" in result.answer


def test_habits_command_can_use_whatsapp_style(registry):
    service = _service(registry)
    service.create_session("session")

    habit_service = HabitService(registry)
    habit_service.add_habit("gym")
    habit_service.log_habit("gym")

    result = service.answer_in_session(
        session_id="session",
        question="/habits",
        response_style="whatsapp",
    )

    assert "📊 *Habit Summary*" in result.answer
    assert "🔥 1-day streak" in result.answer
    assert "✅ Total logged this week: 1/7" in result.answer


def test_remember_command_writes_shared_fact_data(registry):
    service = _service(registry)
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="/remember-personal I prefer morning workouts",
    )

    assert "Personal fact saved" in result.answer
    facts = FactService(registry).list_facts(category="personal")
    assert [fact.content for fact in facts] == ["I prefer morning workouts"]


def test_remember_command_can_use_whatsapp_style(registry):
    service = _service(registry)
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="/remember-personal I prefer morning workouts",
        response_style="whatsapp",
    )

    assert result.answer.startswith("🧠 Personal fact saved:")


def test_current_news_query_uses_news_service_with_whatsapp_style(registry):
    service = _news_service(registry)
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="what is the latest news on Sam Altman and Elon Musk",
        response_style="whatsapp",
    )

    assert result.sources_used is True
    assert result.news_sources[0]["title"] == "OpenAI Trial Live Updates"
    assert "📰 *Latest News: sam altman and elon musk*" in result.answer
    assert "⚡ *Summary*" in result.answer
    assert "🔗 *Sources*" in result.answer


def test_todo_command_writes_sqlite_not_apple(registry):
    service = _service(registry)
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="/todo Take trash out @today 7pm",
        response_style="whatsapp",
    )

    todos = registry.get_todos_due_soon(minutes_ahead=1440)
    assert "Added Sage reminder" in result.answer
    assert todos[0]["title"] == "Take trash out"


def test_usage_command_reports_chat_and_twilio_usage(registry):
    service = _service(registry)
    cli_session = registry.get_or_create_named_session("cli:default")
    whatsapp_session = registry.get_or_create_whatsapp_session("whatsapp:+1")
    service.create_session("session")
    registry.append_turn(cli_session, "cli-user", "user", "hello", 0)
    registry.append_turn(whatsapp_session, "wa-user", "user", "hello", 0)
    registry.append_turn(whatsapp_session, "wa-user-2", "user", "again", 1)
    registry.record_whatsapp_message_sent()
    registry.record_whatsapp_message_sent()

    result = service.answer_in_session(
        session_id="session",
        question="/usage",
        response_style="whatsapp",
    )

    assert "CLI chats: 1" in result.answer
    assert "WhatsApp chats: 2" in result.answer
    assert "2/50 messages used" in result.answer
    assert "48 remaining" in result.answer


def test_sources_intent_works_without_slash(registry):
    service = _service(registry)
    service.create_session("session")
    document = ParsedDocument(
        source_path=Path("/url/article"),
        filename="Saved Article",
        extension=".url",
        checksum_sha256="d" * 64,
        parser_name="url_scraper",
        content="saved content",
        char_count=13,
        metadata={"source_type": "url", "source_url": "https://example.com/a"},
    )
    registry.upsert_document("doc-url", document)
    registry.set_document_source("doc-url", source_type="url", source_url="https://example.com/a")

    result = service.answer_in_session(session_id="session", question="what have you saved?")

    assert "Your saved sources" in result.answer
    assert "Saved Article" in result.answer
    assert "example.com" in result.answer


def test_natural_language_reminder_uses_add_todo_tool(registry):
    scheduled = []
    service = ChatService(
        retriever=_UnusedRetriever(),
        chat_provider=_UnusedChatProvider(),
        registry=registry,
        fact_service=FactService(registry),
        habit_service=HabitService(registry),
        schedule_todo_callback=scheduled.append,
    )
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="remind me to take the trash out at 8PM today",
    )

    todos = registry.get_todos_due_soon(minutes_ahead=1440)
    assert "Added Sage reminder" in result.answer
    assert todos[0]["title"] == "take the trash out"
    assert scheduled[0]["id"] == todos[0]["id"]


def test_direct_reminder_without_due_date_still_writes_sqlite(registry):
    service = _service(registry)
    service.create_session("session")

    result = service.answer_in_session(
        session_id="session",
        question="remind me to buy oat milk",
    )

    row = registry._connection.execute("SELECT title, due_at FROM todos").fetchone()
    assert "Added Sage reminder" in result.answer
    assert row["title"] == "buy oat milk"
    assert row["due_at"] is None
