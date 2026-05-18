from dataclasses import dataclass
from typing import Protocol, Union

from app.config.settings import Settings
from app.providers.groq_chat import GroqChatProvider
from app.providers.huggingface_embeddings import HuggingFaceEmbeddingsProvider
from app.providers.ollama_chat import OllamaChatProvider
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider


class ChatProvider(Protocol):
    def generate(self, prompt: str) -> str:
        ...

    def chat(self, messages: list[dict]) -> str:
        ...


class EmbeddingsProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @classmethod
    def parse(cls, raw: str, default_provider: str = "ollama") -> "ModelSpec":
        value = raw.strip()
        if not value:
            raise ValueError("model spec cannot be empty")
        if ":" not in value:
            return cls(provider=default_provider, model=value)
        provider, model = value.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if not provider or not model:
            raise ValueError("model spec must be '<provider>:<model>'")
        return cls(provider=provider, model=model)

    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def create_chat_provider(settings: Settings, spec: Union[str, ModelSpec]) -> ChatProvider:
    model_spec = ModelSpec.parse(spec) if isinstance(spec, str) else spec

    if model_spec.provider == "ollama":
        return OllamaChatProvider(
            base_url=settings.ollama_base_url,
            model=model_spec.model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    if model_spec.provider == "groq":
        return GroqChatProvider(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
            model=model_spec.model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    raise ValueError(f"Unsupported chat provider '{model_spec.provider}'. Use: ollama, groq.")


def default_chat_model_spec(settings: Settings) -> str:
    if settings.groq_api_key:
        return f"groq:{settings.groq_chat_model}"
    return f"ollama:{settings.ollama_chat_model}"


def agent_model_specs(settings: Settings) -> dict[str, str]:
    default = default_chat_model_spec(settings)
    return {
        "orchestrator": settings.orchestrator_chat_model or default,
        "rag_agent": settings.rag_chat_model or default,
        "research_agent": settings.research_chat_model or default,
        "action_agent": settings.action_chat_model or default,
        "conversational": settings.conversational_chat_model or default,
    }


def create_default_chat_provider(settings: Settings) -> ChatProvider:
    return create_chat_provider(settings, default_chat_model_spec(settings))


def create_embeddings_provider(settings: Settings) -> EmbeddingsProvider:
    provider = settings.embeddings_provider.strip().lower()
    if provider == "ollama":
        return OllamaEmbeddingsProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider in ("huggingface", "hf"):
        return HuggingFaceEmbeddingsProvider(
            api_key=settings.huggingface_api_key,
            model=settings.huggingface_embedding_model,
            base_url=settings.huggingface_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider in ("sentence-transformers", "st", "local"):
        from app.providers.sentence_transformers_embeddings import SentenceTransformersEmbeddingsProvider
        return SentenceTransformersEmbeddingsProvider(model=settings.huggingface_embedding_model)
    raise ValueError("Unsupported embeddings provider. Use: ollama, huggingface, sentence-transformers.")
