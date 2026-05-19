"""Prompt loader for agent system prompts."""
from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    """Load a prompt by filename (without .txt extension)."""
    return (_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()
