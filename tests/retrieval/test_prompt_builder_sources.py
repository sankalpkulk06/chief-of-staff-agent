from app.retrieval.prompt_builder import build_grounded_prompt
from app.retrieval.retriever import RetrievedChunk


def test_grounded_prompt_formats_url_and_local_sources():
    chunks = [
        RetrievedChunk(
            chunk_id="url-chunk",
            document_id="doc-url",
            text="url text",
            score=0.1,
            file_name="Article",
            source_type="url",
            source_url="https://example.com/a",
            document_metadata={"ingested_at": "2026-04-30 12:00:00"},
        ),
        RetrievedChunk(
            chunk_id="local-chunk",
            document_id="doc-local",
            text="local text",
            score=0.2,
            file_name="notes.txt",
        ),
    ]

    prompt = build_grounded_prompt("question", chunks)

    assert "Article — example.com 🌐 (saved 2026-04-30)" in prompt
    assert "notes.txt 📄 (local)" in prompt
