from pathlib import Path

from app.core.qa_service import QAService
from app.ingestion.chunker import Chunker, ChunkingConfig
from app.ingestion.ids import build_document_id
from app.parsers.router import ParserRouter
from app.retrieval.prompt_builder import build_grounded_prompt, build_system_message_with_tools
from app.retrieval.retriever import Retriever
from app.storage.chroma_store import ChromaStore
from app.storage.sqlite_registry import SQLiteRegistry


class _StubEmbeddingsProvider:
    def __init__(self, vector):
        self._vector = vector
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return list(self._vector)


class _StubChatProvider:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return "stubbed grounded answer"


def _prepare_storage_with_chunks(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("alpha beta gamma delta epsilon zeta eta theta", encoding="utf-8")

    parsed = ParserRouter().parse(file_path)
    document_id = build_document_id(parsed.source_path, parsed.checksum_sha256)
    chunks = Chunker(ChunkingConfig(chunk_size=20, chunk_overlap=5)).chunk_document(parsed, document_id=document_id)

    sqlite_registry = SQLiteRegistry(tmp_path / "registry.db")
    sqlite_registry.upsert_document(document_id=document_id, document=parsed)
    for chunk in chunks:
        sqlite_registry.upsert_chunk(chunk)

    chroma_store = ChromaStore(tmp_path / "chroma", collection_name="retriever_test")
    embeddings = []
    for idx, _ in enumerate(chunks):
        if idx == 0:
            embeddings.append([1.0, 0.0, 0.0])
        else:
            embeddings.append([0.0, 1.0, 0.0])
    chroma_store.upsert_chunks(chunks=chunks, embeddings=embeddings)
    return sqlite_registry, chroma_store


def test_retriever_returns_ranked_chunks_with_source_metadata(tmp_path):
    sqlite_registry, chroma_store = _prepare_storage_with_chunks(tmp_path)
    try:
        retriever = Retriever(
            embeddings_provider=_StubEmbeddingsProvider([1.0, 0.0, 0.0]),
            vector_store=chroma_store,
            metadata_registry=sqlite_registry,
            default_top_k=2,
        )

        result = retriever.retrieve("What is in my notes?")

        assert result.is_empty is False
        assert result.top_k == 2
        assert len(result.chunks) == 2
        assert result.chunks[0].file_name == "notes.txt"
        assert result.chunks[0].source_path.endswith("notes.txt")
    finally:
        sqlite_registry.close()


def test_retriever_empty_result_handling(tmp_path):
    chroma_store = ChromaStore(tmp_path / "chroma", collection_name="empty_retriever")
    retriever = Retriever(
        embeddings_provider=_StubEmbeddingsProvider([1.0, 0.0, 0.0]),
        vector_store=chroma_store,
        metadata_registry=None,
        default_top_k=3,
    )

    result = retriever.retrieve("No data question")

    assert result.is_empty is True
    assert result.chunks == []
    assert result.top_k == 3


def test_prompt_builder_output_shape():
    prompt = build_grounded_prompt(
        question="What is RAG?",
        chunks=[],
        max_chunks=3,
    )

    assert "Question:" in prompt
    assert "Context:" in prompt
    assert "I don't know based on the provided documents." in prompt
    assert "If the user asks about a specific file" in prompt


def test_tool_prompt_includes_response_style():
    prompt = build_system_message_with_tools(
        response_style="Use friendly emojis and compact WhatsApp formatting.",
    )

    assert "Response style:" in prompt
    assert "friendly emojis" in prompt


def test_qa_service_orchestration(tmp_path):
    sqlite_registry, chroma_store = _prepare_storage_with_chunks(tmp_path)
    try:
        retriever = Retriever(
            embeddings_provider=_StubEmbeddingsProvider([1.0, 0.0, 0.0]),
            vector_store=chroma_store,
            metadata_registry=sqlite_registry,
            default_top_k=1,
        )
        chat = _StubChatProvider()
        service = QAService(retriever=retriever, chat_provider=chat, max_prompt_chunks=1)

        result = service.answer_question("Summarize the notes")

        assert result.answer == "stubbed grounded answer"
        assert len(result.sources) == 1
        assert "Summarize the notes" in result.prompt
        assert len(chat.prompts) == 1
    finally:
        sqlite_registry.close()
