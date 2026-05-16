from datetime import datetime
from pathlib import Path
from typing import Set

from app.core.qa_service import QAResult


def export_qa_to_markdown(result: QAResult, reports_dir: Path) -> Path:
    """Export Q&A result to Markdown file."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"qa_{timestamp}.md"
    filepath = reports_dir / filename

    content = _format_qa_markdown(result)
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _format_qa_markdown(result: QAResult) -> str:
    """Format QAResult as Markdown string."""
    lines = []

    # Header
    lines.append("# Q&A Export\n")

    # Metadata
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"**Generated:** {timestamp}\n")

    # Question
    lines.append("## Question\n")
    lines.append(f"{result.question}\n")

    # Answer
    lines.append("## Answer\n")
    lines.append(f"{result.answer}\n")

    # Sources
    if result.sources:
        lines.append("## Sources\n")
        seen: Set[str] = set()
        for i, chunk in enumerate(result.sources, 1):
            source_label = chunk.file_name or chunk.source_path or chunk.document_id
            if source_label in seen:
                continue
            seen.add(source_label)

            lines.append(f"### Source {i}: {source_label}\n")
            if chunk.chunk_index > 0:
                lines.append(f"**Chunk {chunk.chunk_index}** | Relevance: {chunk.score:.3f}\n")
            lines.append(f"{chunk.text}\n")

    return "\n".join(lines)
