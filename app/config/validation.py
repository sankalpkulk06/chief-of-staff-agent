import re
from pathlib import Path
from typing import Optional

from app.config.settings import Settings
from app.providers.factory import ModelSpec, agent_model_specs, default_chat_model_spec
from app.storage.factory import create_registry, create_vector_store


class CloudConfigurationError(RuntimeError):
    """Raised when cloud runtime configuration is incomplete or inconsistent."""


def validate_runtime_configuration(settings: Settings, project_root: Optional[Path] = None) -> None:
    """Validate cloud/demo settings before serving user traffic."""
    paths = settings.resolve_paths(project_root=project_root)
    _validate_chat_models(settings)
    _validate_embeddings(settings)

    if settings.database_url:
        registry = create_registry(settings.database_url, paths.sqlite_db_path)
        vector_store = create_vector_store(
            settings.database_url,
            paths.chroma_dir,
            settings.embedding_dimension,
        )
        try:
            if type(registry).__name__ != "PostgresRegistry" or type(vector_store).__name__ != "PgVectorStore":
                raise CloudConfigurationError(
                    "DATABASE_URL is set but storage factories did not select PostgresRegistry/PgVectorStore."
                )
            try:
                vector_store.validate_database_dimension()
            except ValueError as exc:
                raise CloudConfigurationError(str(exc)) from exc
        finally:
            registry.close()
            vector_store.close()


def _validate_chat_models(settings: Settings) -> None:
    specs = [default_chat_model_spec(settings), *agent_model_specs(settings).values()]
    for raw in specs:
        spec = ModelSpec.parse(raw)
        if spec.provider == "groq" and not settings.groq_api_key:
            raise CloudConfigurationError(
                f"Chat model {spec.label()} requires GROQ_API_KEY, but GROQ_API_KEY is not set."
            )


def _validate_embeddings(settings: Settings) -> None:
    provider = settings.embeddings_provider.strip().lower()
    if provider in ("huggingface", "hf"):
        if not settings.huggingface_api_key:
            raise CloudConfigurationError(
                "EMBEDDINGS_PROVIDER=huggingface requires HUGGINGFACE_API_KEY, but HUGGINGFACE_API_KEY is not set."
            )
        expected = _known_embedding_dimension(settings.huggingface_embedding_model)
        if expected and expected != settings.embedding_dimension:
            raise CloudConfigurationError(
                f"Hugging Face embedding model {settings.huggingface_embedding_model} produces {expected} dimensions, "
                f"but EMBEDDING_DIMENSION={settings.embedding_dimension}. Set EMBEDDING_DIMENSION={expected}, "
                "run the pgvector dimension migration, then re-ingest documents."
            )
    elif provider in ("sentence-transformers", "st", "local"):
        pass  # no API key needed — runs locally
    elif provider != "ollama":
        raise CloudConfigurationError("Unsupported EMBEDDINGS_PROVIDER. Use ollama, huggingface, or sentence-transformers.")


def _known_embedding_dimension(model: str) -> Optional[int]:
    normalized = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    if normalized in ("baai-bge-small-en-v1-5", "sentence-transformers-all-minilm-l6-v2"):
        return 384
    return None
