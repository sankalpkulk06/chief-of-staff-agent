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

    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2:3b"
    ollama_embedding_model: str = "nomic-embed-text"

    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=120, ge=0)
    retrieval_top_k: int = Field(default=5, gt=0)
    news_max_results: int = Field(default=5, gt=0)
    email_max_results: int = Field(default=20, gt=0)
    reminders_default_list: str = "Reminders"
    habit_default_reminder_time: str = "21:00"

    tavily_api_key: str = ""
    web_search_max_results: int = Field(default=5, gt=0)
    web_search_provider: str = "tavily"  # "tavily" | "duckduckgo"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""  # e.g. "whatsapp:+14155238886"
    twilio_daily_message_limit: int = Field(default=50, gt=0)
    webhook_port: int = 8000
    whatsapp_enabled: bool = True
    scheduler_enabled: bool = True
    morning_briefing_time: str = "08:00"
    habit_nudge_time: str = "21:00"
    your_whatsapp_number: str = ""  # e.g. "whatsapp:+14155551234"

    url_ingestion_enabled: bool = True
    url_scrape_timeout: int = 10
    url_min_content_words: int = 100
    url_max_content_words: int = 50000

    data_dir: Optional[Path] = None

    # Web UI auth — set SAGE_PASSPHRASE in .env to require a passphrase on login.
    # Leave empty to disable auth (local-only installs).
    sage_passphrase: str = ""
    sage_username: str = ""

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
