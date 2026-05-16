from pathlib import Path
from typing import Optional

import typer

from app import __version__
from app.cli.commands_chat import chat_command
from app.cli.commands_email import email_personal_command, email_work_command
from app.config import get_settings
from app.cli.commands_ask import ask_command
from app.cli.commands_ingest import ingest_command
from app.cli.commands_serve import serve_command
from app.storage.sqlite_registry import SQLiteRegistry

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


@cli.command("config")
def show_config() -> None:
    settings = get_settings()
    paths = settings.resolve_paths()
    typer.echo(f"app_name={settings.app_name}")
    typer.echo(f"app_env={settings.app_env}")
    typer.echo(f"ollama_base_url={settings.ollama_base_url}")
    typer.echo(f"ollama_chat_model={settings.ollama_chat_model}")
    typer.echo(f"ollama_embedding_model={settings.ollama_embedding_model}")
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


@cli.command("sources")
def sources() -> None:
    """List all ingested sources."""
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = SQLiteRegistry(paths.sqlite_db_path)
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
