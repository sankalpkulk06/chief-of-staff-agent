import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values
from pydantic import BaseModel, Field, model_validator

from app.config.paths import AppPaths, build_paths


class Settings(BaseModel):
    app_name: str = "Sage — Personal RAG Agent"
    app_env: str = "development"
    assistant_name: str = "Sage"
    # IANA timezone used when a user has not set their own (via user_settings "timezone").
    default_timezone: str = "UTC"

    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2:3b"
    ollama_embedding_model: str = "nomic-embed-text"

    embeddings_provider: str = "ollama"
    embedding_dimension: int = Field(default=768, gt=0)
    huggingface_api_key: str = ""
    huggingface_base_url: str = "https://api-inference.huggingface.co"
    huggingface_embedding_model: str = "BAAI/bge-small-en-v1.5"

    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_chat_model: str = "llama-3.3-70b-versatile"

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"

    orchestrator_chat_model: str = ""
    rag_chat_model: str = ""
    research_chat_model: str = ""
    action_chat_model: str = ""
    conversational_chat_model: str = ""

    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    retrieval_top_k: int = Field(default=5, gt=0)
    rag_fallback_distance_threshold: float = 0.5
    news_max_results: int = Field(default=5, gt=0)
    email_max_results: int = Field(default=20, gt=0)

    tavily_api_key: str = ""
    web_search_max_results: int = Field(default=5, gt=0)
    web_search_provider: str = "tavily"  # "tavily" | "duckduckgo"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""  # e.g. "whatsapp:+14155238886"
    twilio_daily_message_limit: int = Field(default=50, gt=0)
    whatsapp_enabled: bool = True
    scheduler_enabled: bool = True
    morning_briefing_time: str = "08:00"
    habit_nudge_time: str = "21:00"
    your_whatsapp_number: str = ""  # e.g. "whatsapp:+14155551234"

    url_ingestion_enabled: bool = True
    url_scrape_timeout: int = 10
    url_min_content_words: int = 100
    url_max_content_words: int = 50000

    database_url: str = ""  # PostgreSQL DSN — set to use Supabase/Postgres instead of SQLite+ChromaDB

    # Gmail OAuth — base64-encoded contents of Google OAuth credentials.json (Web app type)
    google_client_secrets_json: str = ""
    # Public URL of this deployment — used to build the OAuth redirect_uri
    sage_public_url: str = ""

    data_dir: Optional[Path] = None

    # Web UI auth — set SAGE_PASSPHRASE in .env to require a passphrase on login.
    # Leave empty to disable auth (local-only installs).
    sage_passphrase: str = ""
    sage_username: str = ""

    max_agent_steps: int = Field(default=5, gt=0)
    max_history_turns: int = Field(default=20, gt=0)
    llm_timeout_seconds: int = Field(default=30, gt=0)
    llm_max_retries: int = Field(default=3, gt=0)
    agent_parallelism_enabled: bool = True
    agent_parallelism_max_workers: int = Field(default=3, gt=0)

    ragas_enabled: bool = True

    security_enabled: bool = True
    max_input_length: int = Field(default=2000, gt=0)
    max_output_length: int = Field(default=8000, gt=0)
    rate_limit_per_minute: int = Field(default=10, gt=0)
    rate_limit_enabled: bool = True
    html_sanitization_enabled: bool = True

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self

    @classmethod
    def from_env(cls, project_root: Optional[Path] = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        env_file = root / ".env"

        values: dict[str, str] = {}
        dotenv_map = dotenv_values(env_file) if env_file.exists() else {}

        for field_name in cls.model_fields:
            env_key = field_name.upper()
            dotenv_value = dotenv_map.get(env_key)
            if dotenv_value is not None:
                values[field_name] = dotenv_value
            if env_key in os.environ:
                values[field_name] = os.environ[env_key]

        return cls.model_validate(values)

    def resolve_paths(self, project_root: Optional[Path] = None) -> AppPaths:
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        return build_paths(project_root=root, data_dir=self.data_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def get_google_client_secrets(settings: "Settings") -> Optional[dict]:
    """Decode and parse GOOGLE_CLIENT_SECRETS_JSON. Returns None if not configured."""
    import base64, json as _json
    raw = settings.google_client_secrets_json.strip()
    if not raw:
        return None
    try:
        return _json.loads(base64.b64decode(raw).decode())
    except Exception:
        try:
            return _json.loads(raw)
        except Exception:
            return None
