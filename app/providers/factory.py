from dataclasses import dataclass
from typing import Protocol, Union

from app.config.settings import Settings
from app.providers.groq_chat import GroqChatProvider
from app.providers.ollama_chat import OllamaChatProvider


class ChatProvider(Protocol):
    def generate(self, prompt: str) -> str:
        ...

    def chat(self, messages: list[dict]) -> str:
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
