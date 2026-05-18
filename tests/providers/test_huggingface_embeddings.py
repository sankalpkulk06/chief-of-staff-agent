import pytest

from app.config.settings import Settings
from app.providers.factory import (
    create_default_chat_provider,
    create_embeddings_provider,
    default_chat_model_spec,
)
from app.providers.groq_chat import GroqChatProvider
from app.providers.huggingface_embeddings import HuggingFaceEmbeddingsProvider
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider, OllamaProviderError


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses=None):
        self._responses = responses or []
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)


def test_huggingface_embeddings_provider_embed_texts_and_query():
    session = _FakeSession(
        responses=[
            _FakeResponse(200, [[0.1, 0.2], [0.3, 0.4]]),
            _FakeResponse(200, [[0.5, 0.6]]),
        ]
    )
    provider = HuggingFaceEmbeddingsProvider(
        api_key="hf_test",
        model="BAAI/bge-small-en-v1.5",
        session=session,
    )

    assert provider.embed_texts(["first", "second"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert provider.embed_query("needle") == [0.5, 0.6]
    assert session.calls[0]["url"].endswith("/pipeline/feature-extraction/BAAI/bge-small-en-v1.5")
    assert session.calls[0]["headers"]["Authorization"] == "Bearer hf_test"
    assert session.calls[1]["json"]["inputs"][0].startswith("Represent this sentence for searching relevant passages:")


def test_huggingface_embeddings_provider_pools_token_embeddings():
    session = _FakeSession(responses=[_FakeResponse(200, [[[1.0, 3.0], [3.0, 5.0]]])])
    provider = HuggingFaceEmbeddingsProvider(api_key="hf_test", session=session)

    assert provider.embed_texts(["chunk"]) == [[2.0, 4.0]]


def test_huggingface_embeddings_provider_pools_single_input_token_matrix():
    session = _FakeSession(responses=[_FakeResponse(200, [[1.0, 3.0], [3.0, 5.0]])])
    provider = HuggingFaceEmbeddingsProvider(api_key="hf_test", session=session)

    assert provider.embed_texts(["chunk"]) == [[2.0, 4.0]]


def test_huggingface_embeddings_provider_requires_api_key():
    provider = HuggingFaceEmbeddingsProvider(api_key="")

    with pytest.raises(OllamaProviderError) as exc_info:
        provider.embed_query("hello")

    assert "HUGGINGFACE_API_KEY" in str(exc_info.value)


def test_embedding_factory_selects_huggingface_and_ollama():
    hf_settings = Settings(embeddings_provider="huggingface", huggingface_api_key="hf_test")
    ollama_settings = Settings(embeddings_provider="ollama")

    assert isinstance(create_embeddings_provider(hf_settings), HuggingFaceEmbeddingsProvider)
    assert isinstance(create_embeddings_provider(ollama_settings), OllamaEmbeddingsProvider)


def test_default_chat_provider_prefers_groq_when_key_set():
    settings = Settings(groq_api_key="gsk_test")

    assert default_chat_model_spec(settings) == "groq:llama-3.3-70b-versatile"
    assert isinstance(create_default_chat_provider(settings), GroqChatProvider)
