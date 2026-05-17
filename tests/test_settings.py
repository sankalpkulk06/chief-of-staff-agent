from pathlib import Path

from app.config.settings import Settings


def test_settings_load_from_dotenv_and_env_override(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OLLAMA_BASE_URL=http://127.0.0.1:11434\n"
        "CHUNK_SIZE=900\n"
        "CHUNK_OVERLAP=100\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CHUNK_SIZE", "1024")
    settings = Settings.from_env(project_root=tmp_path)

    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.chunk_size == 1024
    assert settings.chunk_overlap == 100
    assert settings.reminders_default_list == "Reminders"
    assert settings.scheduler_enabled is True
    assert settings.morning_briefing_time == "08:00"
    assert settings.habit_nudge_time == "21:00"
    assert settings.twilio_daily_message_limit == 50
    assert settings.groq_api_key == ""
    assert settings.groq_chat_model == "llama-3.3-70b-versatile"


def test_settings_loads_reminders_list_override(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "REMINDERS_DEFAULT_LIST=Errands\n",
        encoding="utf-8",
    )

    settings = Settings.from_env(project_root=tmp_path)

    assert settings.reminders_default_list == "Errands"


def test_settings_loads_scheduler_overrides(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "SCHEDULER_ENABLED=false\n"
        "MORNING_BRIEFING_TIME=07:30\n"
        "HABIT_NUDGE_TIME=20:45\n"
        "YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234\n"
        "TWILIO_DAILY_MESSAGE_LIMIT=75\n",
        encoding="utf-8",
    )

    settings = Settings.from_env(project_root=tmp_path)

    assert settings.scheduler_enabled is False
    assert settings.morning_briefing_time == "07:30"
    assert settings.habit_nudge_time == "20:45"
    assert settings.your_whatsapp_number == "whatsapp:+14155551234"
    assert settings.twilio_daily_message_limit == 75


def test_settings_loads_agent_model_overrides(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "GROQ_API_KEY=gsk_test\n"
        "ORCHESTRATOR_CHAT_MODEL=groq:llama-3.3-70b-versatile\n"
        "ACTION_CHAT_MODEL=groq:llama-3.3-70b-versatile\n",
        encoding="utf-8",
    )

    settings = Settings.from_env(project_root=tmp_path)

    assert settings.groq_api_key == "gsk_test"
    assert settings.orchestrator_chat_model == "groq:llama-3.3-70b-versatile"
    assert settings.action_chat_model == "groq:llama-3.3-70b-versatile"


def test_settings_resolve_local_paths(tmp_path):
    settings = Settings.from_env(project_root=tmp_path)
    paths = settings.resolve_paths(project_root=tmp_path)

    assert paths.project_root == tmp_path.resolve()
    assert paths.data_dir == (tmp_path / "data").resolve()
    assert paths.chroma_dir == (tmp_path / "data" / "chroma").resolve()
    assert paths.sqlite_db_path == (tmp_path / "data" / "sqlite" / "registry.db").resolve()
    assert Path(paths.chroma_dir).exists()
    assert Path(paths.sqlite_dir).exists()
