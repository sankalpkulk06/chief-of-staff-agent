from pathlib import Path

from typer.testing import CliRunner

from app.cli.app import cli
from app.config.settings import get_settings
from app.core.ingest_coordinator import IngestCoordinator
from app.core.qa_service import QAService
from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ingest_service import IngestService
from app.parsers.router import ParserRouter
from app.retrieval.retriever import Retriever
from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


runner = CliRunner()


class _FakeEmbeddingsProvider:
    def embed_texts(self, texts):
        return [[float(len(text.split())), 1.0, 0.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text.split())), 1.0, 0.0]


class _FakeChatProvider:
    def generate(self, prompt):
        return "Mocked grounded answer from local context."


def test_e2e_ingest_then_ask(monkeypatch, tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "notes.txt").write_text(
        "Vector databases store embeddings and support similarity search.",
        encoding="utf-8",
    )

    data_dir = tmp_path / "data"
    sqlite_registry = SQLiteRegistry(data_dir / "sqlite" / "registry.db")
    chroma_store = ChromaStore(data_dir / "chroma", collection_name="e2e_test")
    parser_router = ParserRouter()

    ingest_coordinator = IngestCoordinator(
        ingest_service=IngestService(
            parser_router=parser_router,
            chunker=Chunker(ChunkingConfig(chunk_size=80, chunk_overlap=10)),
        ),
        embeddings_provider=_FakeEmbeddingsProvider(),
        registry=sqlite_registry,
        vector_store=chroma_store,
        supported_extensions=parser_router.supported_extensions,
    )

    qa_service = QAService(
        retriever=Retriever(
            embeddings_provider=_FakeEmbeddingsProvider(),
            vector_store=chroma_store,
            metadata_registry=sqlite_registry,
            default_top_k=3,
        ),
        chat_provider=_FakeChatProvider(),
    )

    monkeypatch.setenv("DATA_DIR", data_dir.as_posix())
    get_settings.cache_clear()
    monkeypatch.setattr("app.cli.commands_ingest.create_ingest_coordinator", lambda: ingest_coordinator)
    monkeypatch.setattr("app.cli.commands_ask.create_qa_service", lambda: qa_service)

    ingest_result = runner.invoke(cli, ["ingest", "--path", docs_dir.as_posix()])
    ask_result = runner.invoke(cli, ["ask", "What do my notes say about embeddings?"])

    sqlite_registry.close()

    assert ingest_result.exit_code == 0
    assert "files_processed: 1" in ingest_result.stdout

    assert ask_result.exit_code == 0
    assert "Mocked grounded answer from local context." in ask_result.stdout
    assert "notes.txt" in ask_result.stdout

