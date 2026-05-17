from contextlib import contextmanager

from typer.testing import CliRunner

from app.cli.app import cli
from app.core.qa_service import QAResult
from app.retrieval.retriever import RetrievalResult, RetrievedChunk


runner = CliRunner()


class _StubPromptSession:
    def __init__(self, responses):
        self._responses = iter(responses)

    def prompt(self, *args, **kwargs):
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise EOFError from exc


class _StubFactService:
    def remember(self, content, category):
        return None

    def list_facts(self, category=None):
        return []

    def forget(self, fact_id):
        return None


class _StubRegistry:
    """Minimal registry stub for HabitService initialization in tests."""
    def __init__(self):
        self.todos = []

    def get_or_create_named_session(self, name, user_id="default"):
        return f"session-{name}"

    def create_todo(self, title, list_name=None, due_at=None, user_id="default"):
        todo = {
            "id": f"todo-{len(self.todos) + 1}",
            "title": title,
            "list_name": list_name,
            "due_at": due_at,
        }
        self.todos.append(todo)
        return todo

    class _conn:
        row_factory = None

        @staticmethod
        def execute(sql, params=()):
            class _cur:
                def fetchone(self):
                    return None
                def fetchall(self):
                    return []
            return _cur()

        @staticmethod
        def commit():
            pass

    _connection = _conn()


class _StubChatService:
    def __init__(self, result: QAResult):
        self._result = result
        self.answer_calls = []
        self.created_sessions = []
        self.model_sets = []
        self.model_specs = {
            "orchestrator": "ollama:llama3.2:3b",
            "rag_agent": "ollama:llama3.2:3b",
            "research_agent": "ollama:llama3.2:3b",
            "action_agent": "ollama:llama3.2:3b",
            "conversational": "ollama:llama3.2:3b",
        }
        self.registry = _StubRegistry()

    def create_session(self, session_id, title=""):
        self.created_sessions.append((session_id, title))

    def get_fact_service(self):
        return _StubFactService()

    def get_web_search_service(self):
        return None

    def get_habit_service(self):
        return None

    def get_registry(self):
        return self.registry

    def list_sessions(self, limit=10):
        return []

    def answer_in_session(self, session_id, question, top_k=None):
        self.answer_calls.append((session_id, question, top_k))
        return self._result

    def get_agent_model_specs(self):
        return self.model_specs

    def set_agent_chat_provider(self, agent_name, provider, model_spec):
        self.model_sets.append((agent_name, provider, model_spec))
        self.model_specs["orchestrator"] = model_spec
        return "orchestrator"


class _StubNewsService:
    def search_news(self, query):
        return []

    def get_top_news(self):
        return []


class _StubRemindersService:
    def __init__(self, list_name="Reminders", error=None):
        self.default_list_name = list_name
        self.error = error
        self.calls = []

    def add_reminder(self, task, list_name=None, due_date=None):
        self.calls.append((task, list_name, due_date))
        if self.error:
            raise self.error
        return self.default_list_name


@contextmanager
def _noop_spinner(message="thinking..."):
    yield


def _qa_result(answer: str, sources=None) -> QAResult:
    sources = sources or []
    retrieval = RetrievalResult(question="q", chunks=sources, top_k=5)
    return QAResult(
        question="q",
        answer=answer,
        sources=sources,
        retrieval=retrieval,
        prompt="prompt",
    )


def _patch_chat_dependencies(monkeypatch, responses, chat_service, reminders_service=None):
    monkeypatch.setattr(
        "app.cli.commands_chat.PromptSession",
        lambda *args, **kwargs: _StubPromptSession(responses),
    )
    monkeypatch.setattr("app.cli.commands_chat.thinking_spinner", _noop_spinner)
    monkeypatch.setattr("app.cli.commands_chat.create_chat_service", lambda **kwargs: chat_service)
    monkeypatch.setattr("app.cli.commands_chat.create_news_service", lambda: _StubNewsService())


def test_chat_command_single_turn_and_exit(monkeypatch):
    sources = [
        RetrievedChunk(
            chunk_id="chk_1",
            document_id="doc_1",
            text="chunk text",
            score=0.1,
            source_path="/tmp/sample.md",
            file_name="sample.md",
            chunk_index=0,
            token_count=2,
            document_metadata={},
        )
    ]
    chat_service = _StubChatService(_qa_result("Chat answer", sources))
    _patch_chat_dependencies(monkeypatch, ["What is in sample.md?", "exit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "Sage" in result.stdout
    assert "Chat answer" in result.stdout
    assert "sample.md" in result.stdout
    assert "session-cli:default:default" in result.stdout
    session_id = "session-cli:default:default"
    assert chat_service.answer_calls == [(session_id, "What is in sample.md?", None)]


def test_chat_command_topk_command(monkeypatch):
    chat_service = _StubChatService(_qa_result("A"))
    _patch_chat_dependencies(monkeypatch, ["/topk 2", "What now?", "quit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "Retrieval depth set to" in result.stdout
    session_id = "session-cli:default:default"
    assert chat_service.answer_calls == [(session_id, "What now?", 2)]


def test_chat_command_models_command(monkeypatch):
    chat_service = _StubChatService(_qa_result("A"))
    _patch_chat_dependencies(monkeypatch, ["/models", "quit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "Agent Model Routing" in result.stdout
    assert "OrchestratorAgent" in result.stdout
    assert "ollama:llama3.2:3b" in result.stdout
    assert chat_service.answer_calls == []


def test_chat_command_model_set_command(monkeypatch):
    class _Provider:
        pass

    chat_service = _StubChatService(_qa_result("A"))
    _patch_chat_dependencies(
        monkeypatch,
        ["/model set orchestrator groq:llama-3.3-70b-versatile", "quit"],
        chat_service,
    )
    monkeypatch.setattr("app.cli.commands_chat.create_chat_provider", lambda settings, spec: _Provider())

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "OrchestratorAgent now uses" in result.stdout
    assert chat_service.model_sets[0][0] == "orchestrator"
    assert chat_service.model_sets[0][2] == "groq:llama-3.3-70b-versatile"
    assert chat_service.answer_calls == []


def test_chat_command_model_set_accepts_orchestrator_agent_label(monkeypatch):
    class _Provider:
        pass

    chat_service = _StubChatService(_qa_result("A"))
    _patch_chat_dependencies(
        monkeypatch,
        ["/model set OrchestratorAgent groq:llama-3.3-70b-versatile", "quit"],
        chat_service,
    )
    monkeypatch.setattr("app.cli.commands_chat.create_chat_provider", lambda settings, spec: _Provider())

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "OrchestratorAgent now uses" in result.stdout
    assert chat_service.model_sets[0][0] == "OrchestratorAgent"
    assert chat_service.model_sets[0][2] == "groq:llama-3.3-70b-versatile"


def test_chat_command_todo_adds_reminder_without_hitting_llm(monkeypatch):
    chat_service = _StubChatService(_qa_result("unused"))
    _patch_chat_dependencies(monkeypatch, ["/todo Buy oat milk", "quit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "Added Sage reminder" in result.stdout
    assert "Buy oat milk" in result.stdout
    assert chat_service.registry.todos[0]["title"] == "Buy oat milk"
    assert chat_service.answer_calls == []


def test_chat_command_todo_without_task_shows_usage(monkeypatch):
    chat_service = _StubChatService(_qa_result("unused"))
    _patch_chat_dependencies(monkeypatch, ["/todo", "quit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "/todo <task>" in result.stdout


def test_chat_command_apple_reminder_removed(monkeypatch):
    chat_service = _StubChatService(_qa_result("unused"))
    _patch_chat_dependencies(monkeypatch, ["/apple-reminder Buy oat milk", "quit"], chat_service)

    result = runner.invoke(cli, ["chat"])

    assert result.exit_code == 0
    assert "removed" in result.stdout.lower() or "todo" in result.stdout.lower()
    assert chat_service.registry.todos == []
    assert chat_service.answer_calls == []
