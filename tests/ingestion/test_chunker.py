from pathlib import Path

from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ids import build_chunk_id, build_document_id
from app.schemas.document import ParsedDocument


def _parsed_document(text: str) -> ParsedDocument:
    return ParsedDocument(
        source_path=Path("tests/fixtures/docs/synthetic.txt").resolve(),
        filename="synthetic.txt",
        extension=".txt",
        checksum_sha256="abc123" * 10 + "ab",
        parser_name="txt",
        content=text,
        char_count=len(text),
        metadata={},
    )


def test_chunker_is_deterministic_for_same_input():
    document = _parsed_document("abcdefghijklmnopqrstuvwxyz")
    chunker = Chunker(ChunkingConfig(chunk_size=10, chunk_overlap=2))

    first = chunker.chunk_document(document)
    second = chunker.chunk_document(document)

    assert [(c.chunk_id, c.chunk_index, c.char_start, c.char_end, c.text) for c in first] == [
        (c.chunk_id, c.chunk_index, c.char_start, c.char_end, c.text) for c in second
    ]


def test_chunker_overlap_and_order():
    document = _parsed_document("abcdefghijklmnopqrstuvwxyz")
    chunker = Chunker(ChunkingConfig(chunk_size=10, chunk_overlap=2))

    chunks = chunker.chunk_document(document)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert [chunk.char_start for chunk in chunks] == [0, 8, 16]
    assert [chunk.char_end for chunk in chunks] == [10, 18, 26]
    assert [chunk.text for chunk in chunks] == ["abcdefghij", "ijklmnopqr", "qrstuvwxyz"]


def test_chunker_handles_empty_content():
    document = _parsed_document("")
    chunker = Chunker(ChunkingConfig(chunk_size=10, chunk_overlap=2))

    chunks = chunker.chunk_document(document)

    assert chunks == []


def test_stable_id_generation():
    source_path = Path("tests/fixtures/docs/sample.txt").resolve()
    checksum = "f" * 64

    doc_id_1 = build_document_id(source_path, checksum)
    doc_id_2 = build_document_id(source_path, checksum)
    doc_id_3 = build_document_id(source_path, "e" * 64)

    assert doc_id_1 == doc_id_2
    assert doc_id_1 != doc_id_3
    assert doc_id_1.startswith("doc_")

    chunk_id_1 = build_chunk_id(doc_id_1, 0, 0, 10, "abcdefghij")
    chunk_id_2 = build_chunk_id(doc_id_1, 0, 0, 10, "abcdefghij")
    chunk_id_3 = build_chunk_id(doc_id_1, 1, 8, 18, "ijklmnopqr")

    assert chunk_id_1 == chunk_id_2
    assert chunk_id_1 != chunk_id_3
    assert chunk_id_1.startswith("chk_")

