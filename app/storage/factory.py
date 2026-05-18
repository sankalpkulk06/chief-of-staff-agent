"""Factory functions that return the right storage backend based on DATABASE_URL."""
from pathlib import Path
from typing import Union

from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


def create_registry(database_url: str, sqlite_path: Path) -> Union[SQLiteRegistry, "PostgresRegistry"]:
    if database_url:
        from app.storage.pg_registry import PostgresRegistry
        return PostgresRegistry(database_url)
    return SQLiteRegistry(sqlite_path)


def create_vector_store(database_url: str, chroma_dir: Path, embedding_dimension: int = 768) -> Union[ChromaStore, "PgVectorStore"]:
    if database_url:
        from app.storage.pgvector_store import PgVectorStore
        return PgVectorStore(database_url, embedding_dimension=embedding_dimension)
    return ChromaStore(chroma_dir)
