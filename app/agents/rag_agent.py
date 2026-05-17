"""RAG agent — searches the user's personal documents and answers from them."""
import logging
from typing import Any, List, Optional


from app.agents.base import AgentResult
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a document assistant for a personal AI called Sage. \
The user has saved personal documents, notes, and articles. \
Answer the question using ONLY the document excerpts provided below. \
Cite sources naturally (e.g. "According to your note on X..."). \
If the excerpts do not contain the answer, say so clearly."""


class RAGAgent:
    """Retrieves relevant document chunks and generates a grounded answer."""

    def __init__(self, retriever: Retriever, chat_provider: OllamaChatProvider, top_k: int = 5):
        self._retriever = retriever
        self._provider = chat_provider
        self._top_k = top_k

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
            log.debug("RAG retrieve: task=%r top_k=%s user_id=%r chunks=%d", task[:80], self._top_k, user_id, len(retrieval.chunks))

            if not retrieval.chunks:
                return AgentResult(
                    agent="rag_agent",
                    task=task,
                    output=(
                        "I searched your saved documents but couldn't find anything relevant "
                        f"to '{task}'."
                    ),
                    success=True,
                    metadata={"chunks_found": 0},
                )

            # Build a cited context block.
            chunk_lines = []
            for i, chunk in enumerate(retrieval.chunks, 1):
                source = chunk.file_name or chunk.source_path or chunk.document_id or "unknown"
                chunk_lines.append(f"[{i}] {source}\n{chunk.text.strip()[:600]}")
            context = "\n\n".join(chunk_lines)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Task: {task}\n\nDocument excerpts:\n{context}\n\nAnswer:"},
            ]

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
                metadata={"chunks_found": len(retrieval.chunks)},
            )

        except Exception as exc:
            return AgentResult(
                agent="rag_agent",
                task=task,
                output="",
                success=False,
                error=f"Document search failed: {exc}",
            )
