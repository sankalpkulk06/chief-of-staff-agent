from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence

from app.schemas.chunk import DocumentChunk
from app.schemas.document import ParsedDocument


class DocumentRegistryRepository(Protocol):
    def upsert_document(self, document_id: str, document: ParsedDocument) -> None:
        ...

    def upsert_chunk(self, chunk: DocumentChunk) -> None:
        ...

    def get_document(self, document_id: str) -> Optional[Dict[str, object]]:
        ...

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, object]]:
        ...

    def get_chunks_for_document(self, document_id: str) -> List[Dict[str, object]]:
        ...


class VectorStoreRepository(Protocol):
    def upsert_chunks(self, chunks: Sequence[DocumentChunk], embeddings: Sequence[Sequence[float]]) -> None:
        ...

    def query_similar(self, query_embedding: Sequence[float], n_results: int = 5) -> List[Dict[str, object]]:
        ...

    def get_by_ids(self, chunk_ids: Sequence[str]) -> List[Dict[str, object]]:
        ...

    @property
    def persist_dir(self) -> Path:
        ...

