from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.providers.ollama_embeddings import OllamaEmbeddingsProvider
from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float
    source_path: str = ""
    file_name: str = ""
    chunk_index: int = 0
    token_count: int = 0
    source_type: str = "local"
    source_url: str = ""
    document_metadata: Dict[str, object] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    question: str
    chunks: List[RetrievedChunk] = Field(default_factory=list)
    top_k: int

    @property
    def is_empty(self) -> bool:
        return len(self.chunks) == 0


class Retriever:
    def __init__(
        self,
        embeddings_provider: OllamaEmbeddingsProvider,
        vector_store: ChromaStore,
        metadata_registry: Optional[SQLiteRegistry] = None,
        default_top_k: int = 5,
    ):
        self._embeddings_provider = embeddings_provider
        self._vector_store = vector_store
        self._metadata_registry = metadata_registry
        self._default_top_k = default_top_k

    def retrieve(self, question: str, top_k: Optional[int] = None) -> RetrievalResult:
        effective_top_k = top_k or self._default_top_k
        query_embedding = self._embeddings_provider.embed_query(question)
        vector_records = self._vector_store.query_similar(query_embedding=query_embedding, n_results=effective_top_k)

        chunks: List[RetrievedChunk] = []
        for record in vector_records:
            metadata = record.metadata or {}
            document_id = str(metadata.get("document_id", record.document_id))
            document_metadata: Dict[str, object] = {}
            if self._metadata_registry is not None:
                stored_doc = self._metadata_registry.get_document(document_id)
                if stored_doc is not None:
                    doc_meta = stored_doc.get("metadata_json")
                    document_metadata = doc_meta if isinstance(doc_meta, dict) else {}
                    document_metadata.setdefault("source_type", stored_doc.get("source_type", "local"))
                    document_metadata.setdefault("source_url", stored_doc.get("source_url", ""))
                    document_metadata.setdefault("ingested_at", stored_doc.get("ingested_at", ""))

            chunks.append(
                RetrievedChunk(
                    chunk_id=record.chunk_id,
                    document_id=document_id,
                    text=record.text,
                    score=record.distance,
                    source_path=str(metadata.get("source_path", "")),
                    file_name=str(metadata.get("file_name", "")),
                    chunk_index=int(metadata.get("chunk_index", 0)),
                    token_count=int(metadata.get("token_count", 0)),
                    source_type=str(metadata.get("source_type", document_metadata.get("source_type", "local"))),
                    source_url=str(metadata.get("source_url", document_metadata.get("source_url", "")) or ""),
                    document_metadata=document_metadata,
                )
            )

        return RetrievalResult(question=question, chunks=chunks, top_k=effective_top_k)
