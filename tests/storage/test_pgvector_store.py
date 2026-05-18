import pytest

from app.storage.pgvector_store import PgVectorStore


def test_pgvector_store_rejects_wrong_embedding_dimension():
    store = PgVectorStore.__new__(PgVectorStore)
    store._embedding_dimension = 384

    with pytest.raises(ValueError) as exc_info:
        store._validate_embedding([0.1] * 768)

    assert "configured EMBEDDING_DIMENSION=384" in str(exc_info.value)
    assert "provider returned 768" in str(exc_info.value)


def test_pgvector_store_accepts_expected_embedding_dimension():
    store = PgVectorStore.__new__(PgVectorStore)
    store._embedding_dimension = 384

    store._validate_embedding([0.1] * 384)
