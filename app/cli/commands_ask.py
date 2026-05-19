from typing import Any, Callable, Optional

import typer
from rich.console import Console

from app.config import get_settings
from app.config.validation import CloudConfigurationError, validate_runtime_configuration
from app.core.analytics_service import AnalyticsService
from app.core.chat_service import ChatService
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.core.ingest_coordinator import IngestCoordinator
from app.core.qa_service import QAService
from app.ingestion.ingest_service import IngestService
from app.config.settings import get_google_client_secrets
from app.services.email_service import EmailService
from app.services.news_service import NewsService
from app.services.url_ingestion_service import URLIngestionService
from app.services.web_search_service import WebSearchService
from app.export.markdown_exporter import export_qa_to_markdown
from app.providers.factory import (
    agent_model_specs,
    create_chat_provider,
    create_default_chat_provider,
    create_embeddings_provider,
)
from app.providers.ollama_embeddings import OllamaProviderError
from app.retrieval.retriever import Retriever
from app.storage.factory import create_registry, create_vector_store
from app.agents.security_agent import SecurityAgent
from app.agents.security_policy import SecurityPolicy
from app.ui.spinner import thinking_spinner

console = Console()


def _agent_model_specs(settings) -> dict[str, str]:
    return agent_model_specs(settings)


def create_agent_chat_providers(settings) -> tuple[dict[str, object], dict[str, str]]:
    specs = _agent_model_specs(settings)
    return {agent: create_chat_provider(settings, spec) for agent, spec in specs.items()}, specs


def create_qa_service() -> QAService:
    settings = get_settings()
    paths = settings.resolve_paths()
    retriever = Retriever(
        embeddings_provider=create_embeddings_provider(settings),
        vector_store=create_vector_store(settings.database_url, paths.chroma_dir, settings.embedding_dimension),
        metadata_registry=create_registry(settings.database_url, paths.sqlite_db_path),
        default_top_k=settings.retrieval_top_k,
    )
    chat_provider = create_default_chat_provider(settings)
    return QAService(retriever=retriever, chat_provider=chat_provider)


def create_fact_service(user_id: str) -> FactService:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    return FactService(registry=registry, user_id=user_id)


def create_news_service() -> NewsService:
    settings = get_settings()
    return NewsService(max_results=settings.news_max_results)


def create_web_search_service() -> WebSearchService:
    settings = get_settings()
    return WebSearchService(
        api_key=settings.tavily_api_key or None,
        provider=settings.web_search_provider,
        max_results=settings.web_search_max_results,
    )


def create_url_ingestion_service(
    registry,
    chat_provider,
    vector_store=None,
) -> URLIngestionService:
    settings = get_settings()
    paths = settings.resolve_paths()
    coordinator = IngestCoordinator(
        ingest_service=IngestService(),
        embeddings_provider=create_embeddings_provider(settings),
        registry=registry,
        vector_store=vector_store or create_vector_store(settings.database_url, paths.chroma_dir, settings.embedding_dimension),
    )
    return URLIngestionService(
        ingest_coordinator=coordinator,
        registry=registry,
        chat_provider=chat_provider,
        timeout=settings.url_scrape_timeout,
        min_words=settings.url_min_content_words,
        max_words=settings.url_max_content_words,
    )


def create_analytics_service() -> AnalyticsService:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    return AnalyticsService(registry=registry)


def create_chat_service(
    schedule_todo_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    user_id: str = "",
) -> ChatService:
    settings = get_settings()
    paths = settings.resolve_paths()
    retrieval_vector_store = create_vector_store(settings.database_url, paths.chroma_dir, settings.embedding_dimension)
    retriever = Retriever(
        embeddings_provider=create_embeddings_provider(settings),
        vector_store=retrieval_vector_store,
        metadata_registry=create_registry(settings.database_url, paths.sqlite_db_path),
        default_top_k=settings.retrieval_top_k,
    )
    chat_provider = create_default_chat_provider(settings)
    agent_chat_providers, agent_model_specs = create_agent_chat_providers(settings)
    registry = create_registry(settings.database_url, paths.sqlite_db_path)
    fact_service = create_fact_service(user_id=user_id)
    news_service = create_news_service()

    web_search_service = create_web_search_service()
    habit_service = HabitService(registry, user_id=user_id)
    # URL ingestion gets its own vector store connection so a failed write can't
    # corrupt the retriever's connection state.
    url_ingestion_service = create_url_ingestion_service(registry, chat_provider) if settings.url_ingestion_enabled else None

    # Gmail service — tokens are stored per-user in Supabase; client secret from env var.
    client_secrets = get_google_client_secrets(settings)
    email_service = EmailService(client_secrets=client_secrets) if client_secrets else None
    return ChatService(
        retriever=retriever,
        chat_provider=chat_provider,
        registry=registry,
        agent_chat_providers=agent_chat_providers,
        agent_model_specs=agent_model_specs,
        fact_service=fact_service,
        news_service=news_service,
        web_search_service=web_search_service,
        habit_service=habit_service,
        url_ingestion_service=url_ingestion_service,
        email_service=email_service,
        schedule_todo_callback=schedule_todo_callback,
        twilio_daily_message_limit=settings.twilio_daily_message_limit,
        assistant_name=settings.assistant_name,
        enable_tools=True,
        user_id=user_id,
        rag_fallback_distance_threshold=settings.rag_fallback_distance_threshold,
        agent_parallelism_enabled=settings.agent_parallelism_enabled,
        agent_parallelism_max_workers=settings.agent_parallelism_max_workers,
        security_agent=SecurityAgent(
            registry=registry,
            chat_provider=chat_provider,
            policy=SecurityPolicy.from_settings(settings),
        ),
    )


def ask_command(
    question: str = typer.Argument(..., help="Question to ask about your local documents."),
    top_k: Optional[int] = typer.Option(None, "--top-k", help="Override number of retrieved chunks."),
    export: bool = typer.Option(False, "--export", help="Export answer to Markdown file."),
) -> None:
    settings = get_settings()
    try:
        validate_runtime_configuration(settings)
    except CloudConfigurationError as exc:
        typer.echo(f"Configuration error: {exc}")
        raise typer.Exit(code=1)
    service = create_qa_service()
    try:
        with thinking_spinner("thinking..."):
            result = service.answer_question(question=question, top_k=top_k)
    except OllamaProviderError as exc:
        typer.echo(f"Error: Ollama unavailable: {exc}")
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: ask failed: {exc}")
        raise typer.Exit(code=1)

    console.print("[bold magenta]Answer:[/bold magenta]")
    console.print(result.answer)

    if result.sources:
        console.print("\n[bold magenta]Sources:[/bold magenta]")
        seen = set()
        for source in result.sources:
            if source.source_type == "url" and source.source_url:
                from urllib.parse import urlparse

                domain = urlparse(source.source_url).netloc
                source_label = f"{source.file_name or 'untitled'} — {domain} 🌐"
            else:
                source_label = f"{source.file_name or source.source_path or source.document_id} 📄"
            if source_label in seen:
                continue
            seen.add(source_label)
            console.print(f"[dim]- {source_label}[/dim]")
    else:
        console.print("\n[dim]No relevant sources found in indexed documents.[/dim]")

    if export:
        settings = get_settings()
        paths = settings.resolve_paths()
        filepath = export_qa_to_markdown(result, paths.reports_dir)
        console.print(f"\n[green]✓ Exported to: {filepath}[/green]")
