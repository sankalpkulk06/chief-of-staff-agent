import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator

from app.storage.sqlite_registry import SQLiteRegistry


class Fact(BaseModel):
    """Represents a learned fact."""
    fact_id: str
    content: str
    category: str
    source: str
    confidence_score: float
    created_at: str
    usage_count: int

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

    def remember(self, content: str, category: str = "general", source: str = "user", confidence_score: float = 1.0) -> Fact:
        fact_id = str(uuid.uuid4())
        self._registry.insert_fact(
            fact_id=fact_id, content=content, category=category,
            source=source, confidence_score=confidence_score, user_id=self._user_id,
        )
        fact_data = self._registry.get_fact(fact_id)
        return Fact(**fact_data)

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
