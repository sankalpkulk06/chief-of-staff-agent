from pathlib import Path
from typing import Optional

import typer

from app import __version__
from app.cli.commands_auth import login_command, logout_command, whoami_command
from app.cli.commands_chat import chat_command
from app.cli.commands_connect import (
    calendar_connect_command,
    calendar_disconnect_command,
    calendar_status_command,
    email_connect_command,
    email_disconnect_command,
    email_status_command,
)
from app.cli.commands_email import email_personal_command, email_work_command
from app.cli.commands_profile import profile_delete_command, profile_show_command
from app.cli.commands_sessions import (
    sessions_delete_command,
    sessions_list_command,
    sessions_rename_command,
    sessions_resume_command,
)
from app.cli.commands_stats import stats_command
from app.cli.oauth_flow import OAUTH_LOOPBACK_PORT
from app.config import get_settings
from app.cli.commands_ask import ask_command
from app.cli.commands_ingest import ingest_command
from app.cli.commands_serve import serve_command
from app.storage.factory import create_registry

cli = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local-first personal RAG agent CLI.",
)


@cli.callback(invoke_without_command=True)
def root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the CLI version and exit.",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(f"personal-rag-study-agent {__version__}")
        raise typer.Exit()


@cli.command("login")
def login() -> None:
    """Log in (or sign up) and persist the session for future commands."""
    login_command()


@cli.command("logout")
def logout() -> None:
    """Clear the persisted CLI session."""
    logout_command()


@cli.command("whoami")
def whoami() -> None:
    """Show the currently logged-in CLI user."""
    whoami_command()


@cli.command("config")
def show_config() -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    typer.echo(f"app_name={settings.app_name}")
    typer.echo(f"app_env={settings.app_env}")
    typer.echo(f"ollama_base_url={settings.ollama_base_url}")
    typer.echo(f"ollama_chat_model={settings.ollama_chat_model}")
    typer.echo(f"ollama_embedding_model={settings.ollama_embedding_model}")
    typer.echo(f"embeddings_provider={settings.embeddings_provider}")
    typer.echo(f"embedding_dimension={settings.embedding_dimension}")
    typer.echo(f"huggingface_api_key={'set' if settings.huggingface_api_key else 'not set'}")
    typer.echo(f"huggingface_embedding_model={settings.huggingface_embedding_model}")
    typer.echo(f"groq_api_key={'set' if settings.groq_api_key else 'not set'}")
    typer.echo(f"groq_chat_model={settings.groq_chat_model}")
    typer.echo(f"orchestrator_chat_model={settings.orchestrator_chat_model or 'ollama:' + settings.ollama_chat_model}")
    typer.echo(f"rag_chat_model={settings.rag_chat_model or 'ollama:' + settings.ollama_chat_model}")
    typer.echo(f"research_chat_model={settings.research_chat_model or 'ollama:' + settings.ollama_chat_model}")
    typer.echo(f"action_chat_model={settings.action_chat_model or 'ollama:' + settings.ollama_chat_model}")
    typer.echo(f"conversational_chat_model={settings.conversational_chat_model or 'ollama:' + settings.ollama_chat_model}")
    typer.echo(f"chunk_size={settings.chunk_size}")
    typer.echo(f"chunk_overlap={settings.chunk_overlap}")
    typer.echo(f"retrieval_top_k={settings.retrieval_top_k}")
    typer.echo(f"data_dir={paths.data_dir}")
    typer.echo(f"chroma_dir={paths.chroma_dir}")
    typer.echo(f"sqlite_db_path={paths.sqlite_db_path}")


@cli.command("ingest")
def ingest(path: str = typer.Option(..., "--path", "-p", help="File or directory path to ingest.")) -> None:
    ingest_command(path=Path(path))


@cli.command("ask")
def ask(
    question: str = typer.Argument(..., help="Question to ask about your local documents."),
    top_k: Optional[int] = typer.Option(None, "--top-k", help="Override number of retrieved chunks."),
    export: bool = typer.Option(False, "--export", help="Export answer to Markdown file."),
) -> None:
    ask_command(question=question, top_k=top_k, export=export)


@cli.command("stats")
def stats(
    window: int = typer.Option(30, "--window", "-w", help="Rolling window in days (e.g. 7 / 30 / 90)."),
) -> None:
    """Show your analytics dashboard (habits, todos, usage, feature usage)."""
    stats_command(window=window)


@cli.command("sources")
def sources() -> None:
    """List all ingested sources."""
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(getattr(settings, "database_url", ""), paths.sqlite_db_path)
    try:
        saved = registry.list_all_sources()
    finally:
        registry.close()

    if not saved:
        typer.echo("No sources saved yet.")
        return

    typer.echo(f"Saved sources ({len(saved)}):")
    idx = 1
    for source in [s for s in saved if s.get("source_type") == "url"]:
        from urllib.parse import urlparse

        domain = urlparse(source.get("source_url") or "").netloc or source.get("source_url", "")
        typer.echo(f"{idx}. {source.get('file_name', 'untitled')} — {domain} 🌐")
        idx += 1
    for source in [s for s in saved if s.get("source_type") != "url"]:
        typer.echo(f"{idx}. {source.get('file_name') or source.get('source_path') or 'untitled'} 📄")
        idx += 1


@cli.command("chat")
def chat(
    top_k: Optional[int] = typer.Option(None, "--top-k", help="Default retrieval depth for chat session."),
    resume: Optional[str] = typer.Option(None, "--resume", help="Resume a previous chat session by ID."),
) -> None:
    chat_command(top_k=top_k, session_id=resume)


@cli.command("email-personal")
def email_personal(
    max_results: Optional[int] = typer.Option(None, "--max-results", "-n", help="Max emails to fetch."),
    no_triage: bool = typer.Option(False, "--no-triage", help="Skip AI triage, list emails only."),
) -> None:
    email_personal_command(max_results=max_results, no_triage=no_triage)


@cli.command("serve")
def serve(
    port: int = typer.Option(8000, help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for dev"),
) -> None:
    """Start the WhatsApp webhook server."""
    serve_command(port=port, reload=reload)


@cli.command("email-work")
def email_work(
    max_results: Optional[int] = typer.Option(None, "--max-results", "-n", help="Max emails to fetch."),
    no_triage: bool = typer.Option(False, "--no-triage", help="Skip AI triage, list emails only."),
) -> None:
    email_work_command(max_results=max_results, no_triage=no_triage)


# ---------------------------------------------------------------------------
# Google account connects (loopback OAuth)
# ---------------------------------------------------------------------------

email_app = typer.Typer(no_args_is_help=True, help="Connect and manage Gmail.")
cli.add_typer(email_app, name="email")


@email_app.command("connect")
def email_connect(
    work: bool = typer.Option(False, "--work", help="Connect the work Gmail account instead of personal."),
    port: int = typer.Option(OAUTH_LOOPBACK_PORT, "--port", help="Localhost port for the OAuth redirect."),
) -> None:
    """Connect a Gmail account via browser OAuth."""
    email_connect_command(work=work, port=port)


@email_app.command("status")
def email_status(
    work: bool = typer.Option(False, "--work", help="Check the work account instead of personal."),
) -> None:
    """Show whether Gmail is connected."""
    email_status_command(work=work)


@email_app.command("disconnect")
def email_disconnect(
    work: bool = typer.Option(False, "--work", help="Disconnect the work account instead of personal."),
) -> None:
    """Remove the stored Gmail token."""
    email_disconnect_command(work=work)


profile_app = typer.Typer(no_args_is_help=True, help="View or delete your account.")
cli.add_typer(profile_app, name="profile")


@profile_app.command("show")
def profile_show() -> None:
    """Show your account summary."""
    profile_show_command()


@profile_app.command("delete")
def profile_delete() -> None:
    """Permanently delete your account and all its data."""
    profile_delete_command()


sessions_app = typer.Typer(no_args_is_help=True, help="List, rename, delete, and resume chat sessions.")
cli.add_typer(sessions_app, name="sessions")


@sessions_app.command("list")
def sessions_list() -> None:
    """List your chat sessions."""
    sessions_list_command()


@sessions_app.command("rename")
def sessions_rename(
    session: str = typer.Argument(..., help="Session id or unique prefix (see `sage sessions list`)."),
    title: str = typer.Argument(..., help="New title."),
) -> None:
    """Rename a chat session."""
    sessions_rename_command(session, title)


@sessions_app.command("delete")
def sessions_delete(
    session: str = typer.Argument(..., help="Session id or unique prefix."),
) -> None:
    """Delete a chat session and its messages."""
    sessions_delete_command(session)


@sessions_app.command("resume")
def sessions_resume(
    session: str = typer.Argument(..., help="Session id or unique prefix."),
) -> None:
    """Resume a chat session."""
    sessions_resume_command(session)


calendar_app = typer.Typer(no_args_is_help=True, help="Connect and manage Google Calendar + Tasks.")
cli.add_typer(calendar_app, name="calendar")


@calendar_app.command("connect")
def calendar_connect(
    port: int = typer.Option(OAUTH_LOOPBACK_PORT, "--port", help="Localhost port for the OAuth redirect."),
) -> None:
    """Connect Google Calendar + Tasks via browser OAuth."""
    calendar_connect_command(port=port)


@calendar_app.command("status")
def calendar_status() -> None:
    """Show whether Google Calendar is connected."""
    calendar_status_command()


@calendar_app.command("disconnect")
def calendar_disconnect() -> None:
    """Remove the stored Google Calendar token."""
    calendar_disconnect_command()
