import re
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.storage.sqlite_registry import SQLiteRegistry

# Stopwords stripped when deriving a fact's dedup key, so "my father is Naveen" and
# "father: Naveen" collapse to the same key and don't pile up as separate facts.
_KEY_STOP = {"my", "is", "the", "a", "an", "of", "to", "i", "am", "was", "are", "s", "his", "her"}


def content_key(content: str) -> str:
    """Normalized dedup/supersede key: lowercased, stopworded, sorted tokens."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", (content or "").lower()) if t not in _KEY_STOP]
    return " ".join(sorted(tokens))


class Fact(BaseModel):
    """Represents a learned fact."""
    fact_id: str
    content: str
    category: str
    source: str
    confidence_score: float
    created_at: str
    usage_count: int
    trust: str = "high"          # "high" = user-stated, "low" = external (email/doc-derived)
    status: str = "confirmed"    # "confirmed" | "tentative" | "superseded"

    @field_validator("created_at", mode="before")
    @classmethod
    def _stringify_created_at(cls, value) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


class FactService:
    """Service for managing learned facts about the user."""

    def __init__(self, registry: SQLiteRegistry, user_id: str = ""):
        self._registry = registry
        self._user_id = user_id

    def remember(self, content: str, category: str = "general", source: str = "user",
                 confidence_score: float = 1.0, trust: str = "high", status: str = "confirmed",
                 key: Optional[str] = None) -> Fact:
        fact_id = str(uuid.uuid4())
        self._registry.insert_fact(
            fact_id=fact_id, content=content, category=category,
            source=source, confidence_score=confidence_score, user_id=self._user_id,
            trust=trust, status=status, content_key=key or content_key(content),
        )
        fact_data = self._registry.get_fact(fact_id)
        return Fact(**fact_data)

    def find_by_key(self, key: str) -> List[dict]:
        """Active facts sharing a dedup key — used to skip duplicates / supersede contradictions."""
        return self._registry.find_facts_by_content_key(key, user_id=self._user_id)

    def supersede(self, old_fact_id: str, new_fact_id: str) -> None:
        self._registry.supersede_fact(old_fact_id, new_fact_id, user_id=self._user_id)

    def list_facts(self, category: Optional[str] = None) -> List[Fact]:
        rows = self._registry.list_facts(category=category, user_id=self._user_id)
        return [Fact(**row) for row in rows]

    def forget(self, fact_id: str) -> None:
        self._registry.delete_fact(fact_id, user_id=self._user_id)

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        row = self._registry.get_fact(fact_id)
        return Fact(**row) if row else None

    def mark_used(self, fact_id: str) -> None:
        self._registry.increment_fact_usage(fact_id)

    def get_relevant_facts(self, category: str, limit: int = 5) -> List[Fact]:
        return self.list_facts(category=category)[:limit]
