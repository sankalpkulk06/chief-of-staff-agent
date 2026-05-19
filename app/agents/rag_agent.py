"""RAG agent — searches the user's personal documents and answers from them."""
import logging
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.agents.prompts import load
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever

log = logging.getLogger(__name__)

_SYSTEM = load("rag")


def _format_chunks(chunks: list) -> str:
    if not chunks:
        return "No relevant documents found."
    parts = []
    for i, chunk in enumerate(chunks, 1):
        source = chunk.file_name or chunk.source_path or chunk.document_id or "unknown"
        score = getattr(chunk, "score", 0.0)
        parts.append(
            f"[{i}] Source: {source}\n"
            f"    Relevance: {score:.2f}\n"
            f"    Content: {chunk.text.strip()[:600]}"
        )
    return "\n\n".join(parts)


class RAGAgent:
    """Retrieves relevant document chunks and generates a grounded answer."""

    def __init__(
        self,
        retriever: Retriever,
        chat_provider: OllamaChatProvider,
        top_k: int = 5,
        assistant_name: str = "Sage",
    ):
        self._retriever = retriever
        self._provider = chat_provider
        self._top_k = top_k
        self._assistant_name = assistant_name

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict[str, Any]],
        previous_results: Optional[List[AgentResult]] = None,
        user_id: Optional[str] = None,
    ) -> AgentResult:
        try:
            retrieval = self._retriever.retrieve(question=task, top_k=self._top_k, user_id=user_id)
            log.debug(
                "RAG retrieve: task=%r top_k=%s user_id=%r chunks=%d",
                task[:80], self._top_k, user_id, len(retrieval.chunks),
            )

            if not retrieval.chunks:
                return AgentResult(
                    agent="rag_agent",
                    task=task,
                    output=f"Nothing in your saved documents covers '{task}'. Want me to search the web instead?",
                    success=True,
                    metadata={"chunks_found": 0, "top_score": 1.0},
                )

            document_chunks = _format_chunks(retrieval.chunks)
            system = (
                _SYSTEM
                .replace("{assistant_name}", self._assistant_name)
                .replace("{document_chunks}", document_chunks)
                .replace("{user_query}", task)
            )

            messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
            answer = self._provider.chat(messages=messages)

            citations = [
                {
                    "index": i,
                    "source": c.file_name or c.source_path or c.document_id,
                    "snippet": c.text[:120],
                }
                for i, c in enumerate(retrieval.chunks, 1)
            ]

            return AgentResult(
                agent="rag_agent",
                task=task,
                output=answer,
                success=True,
                citations=citations,
                metadata={"chunks_found": len(retrieval.chunks), "top_score": retrieval.chunks[0].score},
            )

        except Exception as exc:
            log.error("RAGAgent: retrieval failed: %s", exc, exc_info=True)
            return AgentResult(
                agent="rag_agent",
                task=task,
                output="",
                success=False,
                error=f"Document search failed: {exc}",
            )
