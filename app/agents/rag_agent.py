"""RAG agent — searches the user's personal documents and answers from them."""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.agents.base import AgentResult
from app.agents.prompts import load
from app.providers.ollama_chat import OllamaChatProvider
from app.retrieval.retriever import Retriever

log = logging.getLogger(__name__)

_SYSTEM = load("rag")

_FILTER_EXTRACT_SYSTEM = """\
You extract metadata filters from a document search task.

Given the task and recent conversation, identify:
- "file_name": exact filename if the user refers to a specific file (e.g. "README.md", "notes.txt"). Use null if not mentioned or unclear.
- "query": the cleaned semantic search query — what the user actually wants to know, without filler like "search_documents:" or filename references.

Respond with ONLY valid JSON:
{"query": "...", "file_name": "..." or null}

Examples:
task: "search_documents: summarize README.md"
→ {"query": "summarize README.md", "file_name": "README.md"}

task: "search_documents: give me a summary of the doc (README.md was uploaded)"
→ {"query": "summary of the document", "file_name": "README.md"}

task: "search_documents: key points in the document on Sage Personal AI Assistant"
→ {"query": "key points Sage Personal AI Assistant", "file_name": null}

task: "search_documents: what did I save about machine learning"
→ {"query": "machine learning notes", "file_name": null}

task: "search_documents: title of the README file"
→ {"query": "title of README", "file_name": "README.md"}
"""


def _extract_filters(task: str, provider: OllamaChatProvider) -> Dict[str, Any]:
    """Ask the LLM to pull a semantic query and optional file_name filter from the task."""
    try:
        response = provider.chat([
            {"role": "system", "content": _FILTER_EXTRACT_SYSTEM},
            {"role": "user", "content": f"task: \"{task}\""},
        ])
        cleaned = re.sub(r"```(?:json)?", "", response).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return {
                "query": data.get("query") or task,
                "file_name": data.get("file_name") or None,
            }
    except Exception as exc:
        log.debug("Filter extraction failed, falling back to raw task: %s", exc)
    return {"query": task, "file_name": None}


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
            # Let the LLM identify the semantic query and any metadata filters
            # (e.g. file_name) so the vector store can apply hard WHERE clauses
            # rather than letting filename noise pollute the embedding search.
            filters = _extract_filters(task, self._provider)
            query = filters["query"]
            file_name = filters.get("file_name")

            log.debug(
                "RAG retrieve: query=%r file_name=%r user_id=%r top_k=%s",
                query[:80], file_name, user_id, self._top_k,
            )

            retrieval = self._retriever.retrieve(
                question=query,
                top_k=self._top_k,
                user_id=user_id,
                file_name=file_name,
            )

            if not retrieval.chunks:
                # If a file_name filter was applied and returned nothing, retry
                # without the filter — the filename may have been guessed wrong.
                if file_name:
                    log.debug("RAG: no chunks with file_name=%r, retrying without filter", file_name)
                    retrieval = self._retriever.retrieve(
                        question=query,
                        top_k=self._top_k,
                        user_id=user_id,
                    )

            if not retrieval.chunks:
                return AgentResult(
                    agent="rag_agent",
                    task=task,
                    output=f"Nothing in your saved documents covers '{original_question}'. Want me to search the web instead?",
                    success=True,
                    metadata={"chunks_found": 0, "top_score": 1.0},
                )

            document_chunks = _format_chunks(retrieval.chunks)
            system = (
                _SYSTEM
                .replace("{assistant_name}", self._assistant_name)
                .replace("{document_chunks}", document_chunks)
                .replace("{user_query}", original_question)
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
                metadata={
                    "chunks_found": len(retrieval.chunks),
                    "top_score": retrieval.chunks[0].score,
                    "retrieved_contexts": [c.text for c in retrieval.chunks],
                    "source_ids": [c.chunk_id for c in retrieval.chunks],
                },
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
