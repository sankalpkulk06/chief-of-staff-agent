import pytest

from app.config.settings import Settings
from app.config.validation import CloudConfigurationError, validate_runtime_configuration


class PostgresRegistry:
    def close(self):
        pass


class PgVectorStore:
    def __init__(self, should_fail=False):
        self._should_fail = should_fail

    def validate_database_dimension(self):
        if self._should_fail:
            raise ValueError(
                "Supabase pgvector dimension mismatch: configured EMBEDDING_DIMENSION=384, "
                "database chunk_embeddings.embedding=768."
            )

    def close(self):
        pass


def test_runtime_validation_passes_for_cloud_config(monkeypatch):
    monkeypatch.setattr("app.config.validation.create_registry", lambda *_args: PostgresRegistry())
    monkeypatch.setattr("app.config.validation.create_vector_store", lambda *_args: PgVectorStore())
    settings = Settings(
        database_url="postgresql://example",
        groq_api_key="gsk_test",
        embeddings_provider="huggingface",
        huggingface_api_key="hf_test",
        embedding_dimension=384,
    )

    validate_runtime_configuration(settings)


def test_runtime_validation_fails_missing_huggingface_key():
    settings = Settings(
        embeddings_provider="huggingface",
        embedding_dimension=384,
    )

    with pytest.raises(CloudConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    assert "HUGGINGFACE_API_KEY" in str(exc_info.value)


def test_runtime_validation_fails_missing_groq_key():
    settings = Settings(orchestrator_chat_model="groq:llama-3.3-70b-versatile")

    with pytest.raises(CloudConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    assert "GROQ_API_KEY" in str(exc_info.value)


def test_runtime_validation_fails_huggingface_dimension_mismatch():
    settings = Settings(
        embeddings_provider="huggingface",
        huggingface_api_key="hf_test",
        embedding_dimension=768,
    )

    with pytest.raises(CloudConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    assert "produces 384 dimensions" in str(exc_info.value)


def test_runtime_validation_fails_database_dimension_mismatch(monkeypatch):
    monkeypatch.setattr("app.config.validation.create_registry", lambda *_args: PostgresRegistry())
    monkeypatch.setattr("app.config.validation.create_vector_store", lambda *_args: PgVectorStore(should_fail=True))
    settings = Settings(
        database_url="postgresql://example",
        groq_api_key="gsk_test",
        embeddings_provider="huggingface",
        huggingface_api_key="hf_test",
        embedding_dimension=384,
    )

    with pytest.raises(CloudConfigurationError) as exc_info:
        validate_runtime_configuration(settings)

    assert "database chunk_embeddings.embedding=768" in str(exc_info.value)
