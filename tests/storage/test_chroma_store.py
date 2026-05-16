from pathlib import Path

from app.schemas.chunk import DocumentChunk
from app.storage.chroma_store import ChromaStore


def _chunk(source_path: Path, chunk_id: str, document_id: str, text: str, index: int) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        document_checksum_sha256="b" * 64,
        source_path=source_path.resolve(),
        file_name=source_path.name,
        chunk_index=index,
        text=text,
        token_count=len(text.split()),
        char_start=0,
        char_end=len(text),
        metadata={"kind": "fixture"},
    )


def test_chroma_store_initializes_collection(tmp_path):
    store = ChromaStore(persist_dir=tmp_path / "chroma", collection_name="test_collection")
    assert store.persist_dir == (tmp_path / "chroma").resolve()


def test_chroma_store_upsert_query_and_get_round_trip(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("sample", encoding="utf-8")

    store = ChromaStore(persist_dir=tmp_path / "chroma", collection_name="round_trip")
    chunks = [
        _chunk(file_path, "chk_1", "doc_1", "alpha beta gamma", 0),
        _chunk(file_path, "chk_2", "doc_1", "delta epsilon zeta", 1),
    ]
    embeddings = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]

    store.upsert_chunks(chunks=chunks, embeddings=embeddings)

    query_results = store.query_similar(query_embedding=[1.0, 0.0, 0.0], n_results=1)
    assert len(query_results) == 1
    assert query_results[0].chunk_id == "chk_1"
    assert query_results[0].document_id == "doc_1"

    fetched = store.get_by_ids(["chk_1", "chk_2"])
    fetched_ids = sorted([record.chunk_id for record in fetched])
    assert fetched_ids == ["chk_1", "chk_2"]

