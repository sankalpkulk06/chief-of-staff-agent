from pathlib import Path

import typer

from app.config import get_settings
from app.core.ingest_coordinator import IngestCoordinator
from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ingest_service import IngestService
from app.parsers.router import ParserRouter
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider, OllamaProviderError
from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


def create_ingest_coordinator() -> IngestCoordinator:
    settings = get_settings()
    paths = settings.resolve_paths()
    parser_router = ParserRouter()
    return IngestCoordinator(
        ingest_service=IngestService(
            parser_router=parser_router,
            chunker=Chunker(
                ChunkingConfig(
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                )
            ),
        ),
        embeddings_provider=OllamaEmbeddingsProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
        ),
        registry=SQLiteRegistry(paths.sqlite_db_path),
        vector_store=ChromaStore(paths.chroma_dir),
        supported_extensions=parser_router.supported_extensions,
    )


def ingest_command(
    path: Path = typer.Option(
        ...,
        "--path",
        "-p",
        help="File or directory path to ingest.",
    ),
) -> None:
    resolved = path.resolve()
    if not resolved.exists():
        typer.echo(f"Error: path does not exist: {resolved}")
        raise typer.Exit(code=1)

    coordinator = create_ingest_coordinator()
    try:
        summary = coordinator.ingest(resolved)
    except OllamaProviderError as exc:
        typer.echo(f"Error: Ollama unavailable: {exc}")
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"Error: ingestion failed: {exc}")
        raise typer.Exit(code=1)

    typer.echo("Ingestion summary")
    typer.echo(f"- files_discovered: {summary.files_discovered}")
    typer.echo(f"- files_processed: {summary.files_processed}")
    typer.echo(f"- files_skipped: {summary.files_skipped}")
    typer.echo(f"- chunks_created: {summary.chunks_created}")

    if summary.warnings:
        typer.echo("Warnings:")
        for warning in summary.warnings:
            typer.echo(f"- {warning}")

    if summary.errors:
        typer.echo("Errors:")
        for error in summary.errors:
            typer.echo(f"- {error}")
        raise typer.Exit(code=1)

