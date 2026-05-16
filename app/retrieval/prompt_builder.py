import json
from typing import Any, List, Optional
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


def build_chat_messages(
    question: str,
    chunks: List[RetrievedChunk],
    history: List[dict[str, Any]],
    max_chunks: int = 5,
    assistant_name: str = "Sanky",
    learned_facts: List[dict[str, Any]] = None,
    news_articles: List[dict[str, Any]] = None,
    response_style: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Build a messages list for /api/chat endpoint with conversation history.

    Args:
        question: The current user question
        chunks: Retrieved context chunks
        history: Prior conversation turns, each a dict with "role" and "content"
        max_chunks: Max context chunks to include
        assistant_name: Name of the assistant for personality
        learned_facts: Optional list of learned facts to inject
        news_articles: Optional list of live news articles to inject

    Returns:
        A list of message dicts for the /api/chat endpoint
    """
    limited_chunks = chunks[:max_chunks]
    context_block = ""
    if limited_chunks:
        lines = []
        for idx, chunk in enumerate(limited_chunks, start=1):
            source = _format_chunk_source(chunk)
            lines.append(f"[{idx}] SOURCE={source}")
            lines.append("BEGIN_SNIPPET")
            lines.append(chunk.text.strip())
            lines.append("END_SNIPPET")
            lines.append("")
        context_block = "\n".join(lines).strip()

    system_message = (
        f"You are {assistant_name} — a wise, knowledgeable personal companion with a thoughtful tone.\n\n"
        "You have access to the user's personal documents and learned facts. When they ask about those, "
        "answer with precision, quoting sources where useful. When no documents are relevant, converse naturally "
        "like a trusted advisor.\n\n"
        "Rules:\n"
        "- Be thoughtful and direct. No padding. Short answers unless depth is needed.\n"
        "- Remember everything said in this session — you have full conversation history.\n"
        "- If given context snippets below, ground your answer in them and cite sources naturally.\n"
        "- If no context is provided, answer conversationally from the conversation history.\n"
        "- Never robotically refuse — just be honest if you don't know something.\n"
        "- Be warm and wise. You're a trusted companion, not a search engine."
    )

    if response_style:
        system_message += f"\n\nResponse style:\n{response_style}"

    if learned_facts:
        facts_block = "\n".join([f"- {fact['content']}" for fact in learned_facts])
        system_message += f"\n\nAbout the user (what I know):\n{facts_block}"

    if news_articles:
        news_block = "\n".join(
            [
                f"[{i}] {article['title']}\n    Source: {article['source']} | {article['published']}\n    URL: {article['url']}"
                for i, article in enumerate(news_articles, 1)
            ]
        )
        system_message += f"\n\nCurrent news (fetched live):\n{news_block}\n\nWhen citing these articles, reference them by number (e.g., '[1]')."

    if context_block:
        system_message += f"\n\nRelevant documents:\n{context_block}"

    messages: List[dict[str, Any]] = [{"role": "system", "content": system_message}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    return messages


def build_system_message_with_tools(
    assistant_name: str = "Sage",
    tools_schemas: Optional[List[dict[str, Any]]] = None,
    learned_facts: Optional[List[dict[str, Any]]] = None,
    response_style: Optional[str] = None,
) -> str:
    """Build a system message that instructs the model on tool use.

    Args:
        assistant_name: Name of the assistant
        tools_schemas: List of tool schemas from ToolRegistry.to_schemas()
        learned_facts: Optional list of learned facts to inject about the user

    Returns:
        System message string with tool definitions
    """
    message = (
        f"You are {assistant_name} — a wise, knowledgeable personal companion with a thoughtful tone.\n\n"
        "You have access to powerful tools. When the user's request would benefit from using a tool, "
        "call it using JSON format. Otherwise, respond naturally to the conversation.\n\n"
        "When using tools:\n"
        "- Output ONLY valid JSON like: {\"tool\": \"tool_name\", \"parameters\": {\"key\": \"value\"}}\n"
        "- Always use the exact tool name and parameters from the definitions below\n"
        "- For natural language dates/times, describe them as the user said (e.g., 'tomorrow', 'next Tuesday')\n\n"
        "- Use add_todo for normal reminders/todos, including 'remind me...' requests\n"
        "- Use add_apple_reminder only when the user explicitly says Apple Reminders\n\n"
    )

    if learned_facts:
        facts_block = "\n".join([f"- {fact['content']}" for fact in learned_facts])
        message += f"About the user (what you know about them):\n{facts_block}\n\n"

    if response_style:
        message += f"Response style:\n{response_style}\n\n"

    if tools_schemas:
        message += "Available tools:\n"
        for tool in tools_schemas:
            message += f"\n- {tool['name']}: {tool['description']}\n"
            if tool.get("parameters"):
                params = tool["parameters"].get("properties", {})
                for param_name, param_def in params.items():
                    req = " (required)" if param_name in tool["parameters"].get("required", []) else " (optional)"
                    message += f"  • {param_name}: {param_def.get('description', '')}{req}\n"

    message += (
        "\nRules:\n"
        "- Use tools when they're relevant to the user's intent\n"
        "- If no tool is needed, respond conversationally\n"
        "- Be warm, thoughtful, and helpful\n"
        "- Remember the conversation context for natural follow-ups\n"
    )

    return message
