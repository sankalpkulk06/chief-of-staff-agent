from pathlib import Path
from typing import Dict, List, Sequence

import chromadb
from pydantic import BaseModel

from app.schemas.chunk import DocumentChunk


class ChromaVectorRecord(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    metadata: Dict[str, object]
    distance: float = 0.0


class ChromaStore:
    def __init__(self, persist_dir: Path, collection_name: str = "personal_rag_chunks"):
        self._persist_dir = persist_dir.resolve()
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_dir.as_posix())
        self._collection = self._client.get_or_create_collection(name=collection_name)

    @property
    def persist_dir(self) -> Path:
        return self._persist_dir

    def upsert_chunks(self, chunks: Sequence[DocumentChunk], embeddings: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        if not chunks:
            return

        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas: List[Dict[str, object]] = []
        for chunk in chunks:
            metadata: Dict[str, object] = {
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "source_path": chunk.source_path.as_posix(),
                "file_name": chunk.file_name,
                "token_count": chunk.token_count,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            }
            metadata.update(chunk.metadata)
            metadatas.append(metadata)

        self._collection.upsert(
            ids=ids,
            embeddings=[list(vector) for vector in embeddings],
            documents=documents,
            metadatas=metadatas,
        )

    def query_similar(self, query_embedding: Sequence[float], n_results: int = 5) -> List[ChromaVectorRecord]:
        result = self._collection.query(
            query_embeddings=[list(query_embedding)],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        records: List[ChromaVectorRecord] = []
        for idx, chunk_id in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] is not None else {}
            records.append(
                ChromaVectorRecord(
                    chunk_id=chunk_id,
                    document_id=str(metadata.get("document_id", "")),
                    text=documents[idx] if idx < len(documents) else "",
                    metadata=dict(metadata),
                    distance=float(distances[idx]) if idx < len(distances) else 0.0,
                )
            )
        return records

    def get_by_ids(self, chunk_ids: Sequence[str]) -> List[ChromaVectorRecord]:
        if not chunk_ids:
            return []

        result = self._collection.get(
            ids=list(chunk_ids),
            include=["documents", "metadatas"],
        )
        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        records: List[ChromaVectorRecord] = []
        for idx, chunk_id in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] is not None else {}
            records.append(
                ChromaVectorRecord(
                    chunk_id=chunk_id,
                    document_id=str(metadata.get("document_id", "")),
                    text=documents[idx] if idx < len(documents) else "",
                    metadata=dict(metadata),
                )
            )
        return records

