import requests
import pytest

from app.providers.ollama_chat import OllamaChatProvider
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider, OllamaProviderError


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses=None, error=None):
        self._responses = responses or []
        self._error = error
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)


def test_ollama_embeddings_provider_embed_query_and_texts():
    session = _FakeSession(
        responses=[
            _FakeResponse(200, {"embedding": [0.1, 0.2]}),
            _FakeResponse(200, {"embedding": [0.3, 0.4]}),
            _FakeResponse(200, {"embedding": [0.5, 0.6]}),
        ]
    )
    provider = OllamaEmbeddingsProvider(
        base_url="http://localhost:11434",
        model="nomic-embed-text",
        session=session,
    )

    single = provider.embed_query("hello")
    multiple = provider.embed_texts(["first", "second"])

    assert single == [0.1, 0.2]
    assert multiple == [[0.3, 0.4], [0.5, 0.6]]
    assert session.calls[0]["url"].endswith("/api/embeddings")


def test_ollama_chat_provider_generate():
    session = _FakeSession(responses=[_FakeResponse(200, {"response": " grounded answer "})])
    provider = OllamaChatProvider(
        base_url="http://localhost:11434",
        model="llama3.2:3b",
        session=session,
    )

    answer = provider.generate("prompt text")

    assert answer == "grounded answer"
    assert session.calls[0]["json"]["stream"] is False


def test_ollama_provider_connection_error_handled():
    session = _FakeSession(error=requests.ConnectionError("connection refused"))
    provider = OllamaEmbeddingsProvider(
        base_url="http://localhost:11434",
        model="nomic-embed-text",
        session=session,
    )

    with pytest.raises(OllamaProviderError) as exc_info:
        provider.embed_query("hello")

    assert "Failed to connect to Ollama embeddings API" in str(exc_info.value)


def test_ollama_chat_bad_status_handled():
    session = _FakeSession(responses=[_FakeResponse(500, {"error": "boom"})])
    provider = OllamaChatProvider(
        base_url="http://localhost:11434",
        model="llama3.2:3b",
        session=session,
    )

    with pytest.raises(OllamaProviderError) as exc_info:
        provider.generate("prompt")

    assert "status 500" in str(exc_info.value)

