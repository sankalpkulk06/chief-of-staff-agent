from pathlib import Path
from typing import Iterable, List, Optional

from pydantic import BaseModel, Field

from app.ingestion.chunker import Chunker
from app.ingestion.ids import build_document_id
from app.parsers.router import ParserRouter
from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument


class IngestionResult(BaseModel):
    file_path: Path
    document_id: str
    document: ParsedDocument
    chunks: List[DocumentChunk] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


class IngestionBatchResult(BaseModel):
    results: List[IngestionResult] = Field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.results)

    @property
    def total_chunk_count(self) -> int:
        return sum(result.chunk_count for result in self.results)


class IngestService:
    def __init__(self, parser_router: Optional[ParserRouter] = None, chunker: Optional[Chunker] = None):
        self._parser_router = parser_router or ParserRouter()
        self._chunker = chunker or Chunker()

    def ingest_file(self, file_path: Path) -> IngestionResult:
        parsed_document = self._parser_router.parse(file_path)
        document_id = build_document_id(parsed_document.source_path, parsed_document.checksum_sha256)
        chunks = self._chunker.chunk_document(parsed_document, document_id=document_id)

        warnings: List[str] = []
        if not parsed_document.content.strip():
            warnings.append("Document content is empty after parsing.")

        return IngestionResult(
            file_path=file_path.resolve(),
            document_id=document_id,
            document=parsed_document,
            chunks=chunks,
            warnings=warnings,
        )

    def ingest_files(self, file_paths: Iterable[Path]) -> IngestionBatchResult:
        results = [self.ingest_file(path) for path in file_paths]
        return IngestionBatchResult(results=results)

