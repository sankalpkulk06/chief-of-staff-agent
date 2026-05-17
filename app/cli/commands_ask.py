from typing import Any, Callable, Optional

import typer
from rich.console import Console

from app.config import get_settings
from app.core.analytics_service import AnalyticsService
from app.core.chat_service import ChatService
from app.core.fact_service import FactService
from app.core.habit_service import HabitService
from app.core.ingest_coordinator import IngestCoordinator
from app.core.qa_service import QAService
from app.ingestion.ingest_service import IngestService
from app.services.email_service import EmailService
from app.services.news_service import NewsService
from app.services.url_ingestion_service import URLIngestionService
from app.services.web_search_service import WebSearchService
from app.export.markdown_exporter import export_qa_to_markdown
from app.providers.factory import create_chat_provider
from app.providers.ollama_chat import OllamaChatProvider
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider, OllamaProviderError
from app.retrieval.retriever import Retriever
from app.storage.chroma_store import ChromaStore
from app.agents.security_agent import SecurityAgent
from app.agents.security_policy import SecurityPolicy
from app.storage.sqlite_registry import SQLiteRegistry
from app.ui.spinner import thinking_spinner

console = Console()


def _default_chat_model_spec(settings) -> str:
    return f"ollama:{settings.ollama_chat_model}"


def _agent_model_specs(settings) -> dict[str, str]:
    default = _default_chat_model_spec(settings)
    return {
        "orchestrator": settings.orchestrator_chat_model or default,
        "rag_agent": settings.rag_chat_model or default,
        "research_agent": settings.research_chat_model or default,
        "action_agent": settings.action_chat_model or default,
        "conversational": settings.conversational_chat_model or default,
    }


def create_agent_chat_providers(settings) -> tuple[dict[str, object], dict[str, str]]:
    specs = _agent_model_specs(settings)
    return {agent: create_chat_provider(settings, spec) for agent, spec in specs.items()}, specs


def create_qa_service() -> QAService:
    settings = get_settings()
    paths = settings.resolve_paths()
    retriever = Retriever(
        embeddings_provider=OllamaEmbeddingsProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        ),
        vector_store=ChromaStore(paths.chroma_dir),
        metadata_registry=SQLiteRegistry(paths.sqlite_db_path),
        default_top_k=settings.retrieval_top_k,
    )
    chat_provider = OllamaChatProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_chat_model,
    )
    return QAService(retriever=retriever, chat_provider=chat_provider)


def create_fact_service(user_id: str = "default") -> FactService:
    settings = get_settings()
    paths = settings.resolve_paths()
    registry = SQLiteRegistry(paths.sqlite_db_path)
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
    registry: SQLiteRegistry,
    chat_provider: OllamaChatProvider,
    vector_store: Optional[ChromaStore] = None,
) -> URLIngestionService:
    settings = get_settings()
    paths = settings.resolve_paths()
    coordinator = IngestCoordinator(
        ingest_service=IngestService(),
        embeddings_provider=OllamaEmbeddingsProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        ),
        registry=registry,
        vector_store=vector_store or ChromaStore(paths.chroma_dir),
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
    registry = SQLiteRegistry(paths.sqlite_db_path)
    return AnalyticsService(registry=registry)


def create_chat_service(
    schedule_todo_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    user_id: str = "default",
) -> ChatService:
    settings = get_settings()
    paths = settings.resolve_paths()
    shared_vector_store = ChromaStore(paths.chroma_dir)
    retriever = Retriever(
        embeddings_provider=OllamaEmbeddingsProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        ),
        vector_store=shared_vector_store,
        metadata_registry=SQLiteRegistry(paths.sqlite_db_path),
        default_top_k=settings.retrieval_top_k,
    )
    chat_provider = OllamaChatProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_chat_model,
    )
    agent_chat_providers, agent_model_specs = create_agent_chat_providers(settings)
    registry = SQLiteRegistry(paths.sqlite_db_path)
    fact_service = create_fact_service(user_id=user_id)
    news_service = create_news_service()

    web_search_service = create_web_search_service()
    habit_service = HabitService(registry, user_id=user_id)
    url_ingestion_service = create_url_ingestion_service(registry, chat_provider, shared_vector_store) if settings.url_ingestion_enabled else None

    # Per-user Gmail token stored under data/credentials/{user_id}/
    try:
        user_creds_dir = paths.user_credentials_dir(user_id)
        email_service = EmailService(
            credentials_dir=paths.credentials_dir,
            account_type="personal",
            token_dir=user_creds_dir,
        )
    except Exception:
        email_service = None
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
