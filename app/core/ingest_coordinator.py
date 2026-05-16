import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from app.ingestion.ingest_service import IngestService
from app.providers.ollama_embeddings import OllamaEmbeddingsProvider
from app.schemas.document import ParsedDocument
from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


class IngestSummary(BaseModel):
    files_discovered: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    chunks_created: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class IngestCoordinator:
    def __init__(
        self,
        ingest_service: IngestService,
        embeddings_provider: OllamaEmbeddingsProvider,
        registry: SQLiteRegistry,
        vector_store: ChromaStore,
        supported_extensions: Optional[List[str]] = None,
    ):
        self._ingest_service = ingest_service
        self._embeddings_provider = embeddings_provider
        self._registry = registry
        self._vector_store = vector_store
        self._supported_extensions = supported_extensions or [".md", ".pdf", ".txt"]

    def ingest(self, input_path: Path) -> IngestSummary:
        files = self._discover_files(input_path)
        summary = IngestSummary(files_discovered=len(files))
        if not files:
            summary.errors.append(f"No supported files found under: {input_path}")
            return summary

        for file_path in files:
            try:
                ingest_result = self._ingest_service.ingest_file(file_path)
                existing = self._registry.get_document(ingest_result.document_id)
                if existing and existing.get("checksum_sha256") == ingest_result.document.checksum_sha256:
                    summary.files_skipped += 1
                    summary.warnings.append(f"Skipped unchanged file: {file_path}")
                    continue

                self._registry.upsert_document(ingest_result.document_id, ingest_result.document)
                if ingest_result.chunk_count == 0:
                    summary.files_processed += 1
                    summary.warnings.extend(ingest_result.warnings or [f"No chunks produced for: {file_path}"])
                    continue

                for chunk in ingest_result.chunks:
                    self._registry.upsert_chunk(chunk)

                embeddings = self._embeddings_provider.embed_texts([chunk.text for chunk in ingest_result.chunks])
                self._vector_store.upsert_chunks(ingest_result.chunks, embeddings)

                summary.files_processed += 1
                summary.chunks_created += ingest_result.chunk_count
                summary.warnings.extend(ingest_result.warnings)
            except Exception as exc:
                summary.files_skipped += 1
                summary.errors.append(f"{file_path}: {exc}")
        return summary

    def ingest_text(
        self,
        content: str,
        title: str,
        source_url: str,
        extra_metadata: Optional[dict] = None,
    ) -> Tuple[str, int]:
        """Chunk, embed, and persist scraped text from a URL.

        Returns (document_id, chunks_created). Returns (doc_id, 0) if already ingested.
        """
        url_hash = hashlib.sha256(source_url.encode()).hexdigest()[:16]
        fake_path = Path(f"/url/{url_hash}")
        checksum = hashlib.sha256(content.encode()).hexdigest()
        from app.ingestion.ids import build_document_id
        document_id = build_document_id(fake_path, checksum)

        existing = self._registry.get_document(document_id)
        if existing:
            return document_id, 0

        parsed = ParsedDocument(
            source_path=fake_path,
            filename=title,
            extension=".url",
            checksum_sha256=checksum,
            parser_name="url_scraper",
            content=content,
            char_count=len(content),
            metadata={
                "source_type": "url",
                "source_url": source_url,
                "title": title,
                "ingested_at": datetime.utcnow().isoformat(),
                **(extra_metadata or {}),
            },
        )

        self._registry.upsert_document(document_id, parsed)
        self._registry.set_document_source(document_id, source_type="url", source_url=source_url)

        chunks = self._ingest_service._chunker.chunk_document(parsed, document_id=document_id)
        if not chunks:
            return document_id, 0

        for chunk in chunks:
            chunk.metadata.update({
                "source_type": "url",
                "source_url": source_url,
                "title": title,
                "ingested_at": parsed.metadata.get("ingested_at", ""),
            })
            self._registry.upsert_chunk(chunk)

        embeddings = self._embeddings_provider.embed_texts([c.text for c in chunks])
        self._vector_store.upsert_chunks(chunks, embeddings)

        return document_id, len(chunks)

    def _discover_files(self, input_path: Path) -> List[Path]:
        resolved = input_path.resolve()
        if resolved.is_file():
            return [resolved] if resolved.suffix.lower() in self._supported_extensions else []

        if not resolved.is_dir():
            return []

        discovered: List[Path] = []
        for extension in self._supported_extensions:
            discovered.extend(path for path in resolved.rglob(f"*{extension}") if path.is_file())
        return sorted(set(path.resolve() for path in discovered))
