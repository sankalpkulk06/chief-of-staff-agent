"""Shared types + helpers for native LLM tool-calling (function-calling).

Providers speak different wire formats (Groq = OpenAI, Gemini = functionDeclarations), so
callers work with these normalized types and each provider translates at its own boundary.
A provider is "tool-capable" iff it exposes a ``chat_tools`` method (Groq, Gemini, and the
Fallback wrapper do; Ollama does not — it keeps the prompt-for-JSON path).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


@dataclass
class Tool:
    """A function the model may call. ``parameters`` is a JSON-Schema object restricted to the
    subset Gemini + Groq both accept (type/properties/required/enum/items — no $ref/oneOf)."""
    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    id: str = ""


@dataclass
class ToolChatResult:
    """Normalized tool-calling response. ``content`` is any free text (may be None when the
    model chose to call tools); ``tool_calls`` is the list of requested calls."""
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)


def supports_tools(provider: Any) -> bool:
    """True if the provider exposes native tool-calling."""
    return callable(getattr(provider, "chat_tools", None))


def extract_one(provider: Any, messages: List[dict], tool: Tool,
                fallback_fn: Callable[[], dict], enabled: bool = True) -> dict:
    """Single structured extraction: return the first tool call's arguments via native
    tool-calling, else fall back to ``fallback_fn()`` (the legacy prompt-for-JSON path)."""
    if enabled and supports_tools(provider):
        try:
            result = provider.chat_tools(messages, [tool], tool_choice="required")
            if result.tool_calls:
                return dict(result.tool_calls[0].arguments or {})
        except Exception:
            pass
    return fallback_fn()
