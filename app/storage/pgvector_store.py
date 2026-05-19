import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import psycopg2
import psycopg2.extras
from psycopg2.extras import RealDictCursor

from app.schemas.chunk import DocumentChunk
from app.storage.chroma_store import ChromaVectorRecord


class PgVectorStore:
    """Drop-in pgvector replacement for ChromaStore. Uses chunk_embeddings table."""

    def __init__(self, database_url: str, embedding_dimension: int = 768):
        self._database_url = database_url
        self._embedding_dimension = embedding_dimension
        self._conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        self._conn.autocommit = False

    def _cursor(self):
        try:
            self._conn.isolation_level
        except psycopg2.InterfaceError:
            self._conn = psycopg2.connect(self._database_url, cursor_factory=RealDictCursor)
            self._conn.autocommit = False
        return self._conn.cursor()

    @property
    def persist_dir(self) -> Path:
        return Path("/tmp/pgvector-placeholder")

    @property
    def embedding_dimension(self) -> int:
        return self._embedding_dimension

    def get_database_embedding_dimension(self) -> Optional[int]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod) AS formatted_type
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = 'chunk_embeddings'
                  AND a.attname = 'embedding'
                  AND NOT a.attisdropped
                ORDER BY CASE WHEN n.nspname = 'public' THEN 0 ELSE 1 END
                LIMIT 1
                """
            )
            row = cur.fetchone()
        if not row:
            return None
        match = re.search(r"vector\((\d+)\)", str(row["formatted_type"]))
        return int(match.group(1)) if match else None

    def validate_database_dimension(self) -> None:
        actual = self.get_database_embedding_dimension()
        if actual != self._embedding_dimension:
            raise ValueError(
                "Supabase pgvector dimension mismatch: "
                f"configured EMBEDDING_DIMENSION={self._embedding_dimension}, "
                f"database chunk_embeddings.embedding={actual or 'missing/untyped'}. "
                "Run the pgvector dimension migration, then re-ingest documents before starting Sage."
            )

    def upsert_chunks(self, chunks: Sequence[DocumentChunk], embeddings: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        if not chunks:
            return
        for embedding in embeddings:
            self._validate_embedding(embedding)

        with self._cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings):
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

                # Upsert the chunk row first (in case it doesn't exist yet)
                cur.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, chunk_index, text, token_count,
                        char_start, char_end, source_path, file_name,
                        document_checksum_sha256, metadata_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        text       = EXCLUDED.text,
                        updated_at = NOW()
                    """,
                    (
                        chunk.chunk_id, chunk.document_id, chunk.chunk_index, chunk.text,
                        chunk.token_count, chunk.char_start, chunk.char_end,
                        chunk.source_path.as_posix(), chunk.file_name,
                        chunk.document_checksum_sha256,
                        json.dumps(chunk.metadata, sort_keys=True),
                    ),
                )

                # Upsert the embedding
                vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
                cur.execute(
                    """
                    INSERT INTO chunk_embeddings (chunk_id, embedding)
                    VALUES (%s, %s::extensions.vector)
                    ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding
                    """,
                    (chunk.chunk_id, vector_str),
                )
        self._conn.commit()

    def query_similar(
        self,
        query_embedding: Sequence[float],
        n_results: int = 5,
        where: Optional[Dict] = None,
    ) -> List[ChromaVectorRecord]:
        self._validate_embedding(query_embedding)
        vector_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        filter_clauses: List[str] = []
        filter_params: list = []

        def add_filter(key: str, value: object) -> None:
            if value in (None, ""):
                return
            if key == "document_id":
                filter_clauses.append("c.document_id = %s")
                filter_params.append(value)
            elif key == "user_id":
                filter_clauses.append("d.user_id = %s")
                filter_params.append(value)
            elif key == "source_url":
                filter_clauses.append("d.source_url = %s")
                filter_params.append(value)
            elif key == "source_type":
                filter_clauses.append("d.source_type = %s")
                filter_params.append(value)
            elif key == "file_name":
                filter_clauses.append("c.file_name = %s")
                filter_params.append(value)

        if where:
            if "$and" in where and isinstance(where["$and"], list):
                for condition in where["$and"]:
                    if isinstance(condition, dict):
                        for key, value in condition.items():
                            add_filter(key, value)
            else:
                for key, value in where.items():
                    add_filter(key, value)

        extra_where = ""
        if filter_clauses:
            extra_where = "AND " + " AND ".join(filter_clauses)
        params: list = [vector_str, *filter_params, vector_str, n_results]

        sql = f"""
            SELECT
                c.chunk_id,
                c.document_id,
                c.text,
                c.metadata_json,
                c.source_path,
                c.file_name,
                c.chunk_index,
                c.token_count,
                (ce.embedding <=> %s::extensions.vector) AS distance
            FROM chunk_embeddings ce
            JOIN chunks c ON c.chunk_id = ce.chunk_id
            JOIN documents d ON d.document_id = c.document_id
            WHERE ce.embedding IS NOT NULL {extra_where}
            ORDER BY ce.embedding <=> %s::extensions.vector
            LIMIT %s
        """

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        records: List[ChromaVectorRecord] = []
        for row in rows:
            metadata = row["metadata_json"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            metadata.setdefault("document_id", row["document_id"])
            metadata.setdefault("chunk_index", row["chunk_index"])
            metadata.setdefault("source_path", row["source_path"])
            metadata.setdefault("file_name", row["file_name"])
            metadata.setdefault("token_count", row["token_count"])
            records.append(
                ChromaVectorRecord(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    text=row["text"],
                    metadata=metadata,
                    distance=float(row["distance"]),
                )
            )
        return records

    def get_by_ids(self, chunk_ids: Sequence[str]) -> List[ChromaVectorRecord]:
        if not chunk_ids:
            return []
        with self._cursor() as cur:
            cur.execute(
                "SELECT chunk_id, document_id, text, metadata_json, source_path, file_name, chunk_index, token_count FROM chunks WHERE chunk_id = ANY(%s)",
                (list(chunk_ids),),
            )
            rows = cur.fetchall()

        records: List[ChromaVectorRecord] = []
        for row in rows:
            metadata = row["metadata_json"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            records.append(
                ChromaVectorRecord(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    text=row["text"],
                    metadata=metadata,
                )
            )
        return records

    def delete_user_records(self, user_id: str, document_ids: Optional[Sequence[str]] = None) -> None:
        """Delete embeddings for documents owned by a user."""
        with self._cursor() as cur:
            cur.execute(
                """
                DELETE FROM chunk_embeddings
                WHERE chunk_id IN (
                    SELECT c.chunk_id
                    FROM chunks c
                    JOIN documents d ON d.document_id = c.document_id
                    WHERE d.user_id = %s
                )
                """,
                (user_id,),
            )
            if document_ids:
                cur.execute(
                    """
                    DELETE FROM chunk_embeddings
                    WHERE chunk_id IN (
                        SELECT chunk_id FROM chunks WHERE document_id = ANY(%s)
                    )
                    """,
                    (list(document_ids),),
                )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _validate_embedding(self, embedding: Sequence[float]) -> None:
        actual = len(embedding)
        if actual != self._embedding_dimension:
            raise ValueError(
                "Embedding dimension mismatch: "
                f"configured EMBEDDING_DIMENSION={self._embedding_dimension}, "
                f"provider returned {actual}. Check EMBEDDINGS_PROVIDER/model configuration and re-ingest documents."
            )
