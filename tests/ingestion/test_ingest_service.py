from pathlib import Path

from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ids import build_document_id
from app.ingestion.ingest_service import IngestService


def _docs_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "docs"


def test_ingest_service_happy_path_single_file():
    service = IngestService(chunker=Chunker(ChunkingConfig(chunk_size=20, chunk_overlap=5)))
    file_path = _docs_dir() / "sample.txt"

    result = service.ingest_file(file_path)

    assert result.file_path == file_path.resolve()
    assert result.document.filename == "sample.txt"
    assert result.document_id == build_document_id(result.document.source_path, result.document.checksum_sha256)
    assert result.chunk_count >= 1
    assert result.warnings == []
    assert all(chunk.document_id == result.document_id for chunk in result.chunks)


def test_ingest_service_ingests_multiple_files():
    service = IngestService(chunker=Chunker(ChunkingConfig(chunk_size=30, chunk_overlap=5)))
    docs_dir = _docs_dir()

    batch = service.ingest_files([docs_dir / "sample.txt", docs_dir / "sample.md"])

    assert batch.file_count == 2
    assert batch.total_chunk_count >= 2


def test_ingest_service_empty_content_warning(tmp_path):
    file_path = tmp_path / "empty.txt"
    file_path.write_text("", encoding="utf-8")
    service = IngestService(chunker=Chunker(ChunkingConfig(chunk_size=20, chunk_overlap=5)))

    result = service.ingest_file(file_path)

    assert result.chunk_count == 0
    assert "Document content is empty after parsing." in result.warnings

