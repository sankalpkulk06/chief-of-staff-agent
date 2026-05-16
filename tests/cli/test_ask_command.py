from typing import List

from typer.testing import CliRunner

from app.cli.app import cli
from app.core.qa_service import QAResult
from app.retrieval.retriever import RetrievalResult, RetrievedChunk


runner = CliRunner()


class _StubQAService:
    def __init__(self, result: QAResult):
        self._result = result

    def answer_question(self, question: str, top_k=None) -> QAResult:
        return self._result


def _qa_result(answer: str, sources: List[RetrievedChunk]) -> QAResult:
    retrieval = RetrievalResult(question="q", chunks=sources, top_k=5)
    return QAResult(
        question="q",
        answer=answer,
        sources=sources,
        retrieval=retrieval,
        prompt="prompt",
    )


def test_ask_command_happy_path(monkeypatch):
    sources = [
        RetrievedChunk(
            chunk_id="chk_1",
            document_id="doc_1",
            text="chunk text",
            score=0.1,
            source_path="/tmp/sample.txt",
            file_name="sample.txt",
            chunk_index=0,
            token_count=2,
            document_metadata={},
        )
    ]
    monkeypatch.setattr(
        "app.cli.commands_ask.create_qa_service",
        lambda: _StubQAService(_qa_result("This is an answer.", sources)),
    )

    result = runner.invoke(cli, ["ask", "What is this?"])

    assert result.exit_code == 0
    assert "Answer:" in result.stdout
    assert "This is an answer." in result.stdout
    assert "Sources:" in result.stdout
    assert "sample.txt" in result.stdout


def test_ask_command_no_results(monkeypatch):
    monkeypatch.setattr(
        "app.cli.commands_ask.create_qa_service",
        lambda: _StubQAService(_qa_result("I don't know based on the provided documents.", [])),
    )

    result = runner.invoke(cli, ["ask", "Unknown question"])

    assert result.exit_code == 0
    assert "No relevant sources found in indexed documents." in result.stdout
    assert "I don't know based on the provided documents." in result.stdout

