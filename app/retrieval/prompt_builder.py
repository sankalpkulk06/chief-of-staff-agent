from typing import List
from urllib.parse import urlparse

from app.retrieval.retriever import RetrievedChunk


def _format_chunk_source(chunk: RetrievedChunk) -> str:
    if chunk.source_type == "url" and chunk.source_url:
        domain = urlparse(chunk.source_url).netloc
        ingested = str(
            chunk.document_metadata.get("ingested_at")
            or chunk.document_metadata.get("saved_at")
            or ""
        )[:10]
        saved = f" (saved {ingested})" if ingested else ""
        return f"{chunk.file_name or 'untitled'} — {domain} 🌐{saved}"
    title = chunk.file_name or chunk.source_path or chunk.document_id
    return f"{title} 📄 (local)"


def build_grounded_prompt(question: str, chunks: List[RetrievedChunk], max_chunks: int = 5) -> str:
    limited_chunks = chunks[:max_chunks]
    if not limited_chunks:
        context_block = "No supporting context was retrieved."
    else:
        lines = []
        for idx, chunk in enumerate(limited_chunks, start=1):
            source = _format_chunk_source(chunk)
            lines.append(f"[{idx}] SOURCE={source}")
            lines.append("BEGIN_SNIPPET")
            lines.append(chunk.text.strip())
            lines.append("END_SNIPPET")
            lines.append("")
        context_block = "\n".join(lines).strip()

    return (
        "You are a local-first study assistant.\n"
        "Use only the provided context snippets.\n"
        "If the user asks about a specific file (for example, sample.md), prioritize snippets where SOURCE matches.\n"
        "If an answer is present in context, answer directly and quote short exact phrases from snippets.\n"
        "Only if the answer is truly absent, reply exactly: "
        "\"I don't know based on the provided documents.\"\n\n"
        f"Question:\n{question}\n\n"
        f"Context:\n{context_block}\n\n"
        "Answer:"
    )
