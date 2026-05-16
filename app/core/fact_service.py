import uuid
from typing import List, Optional

from pydantic import BaseModel

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


class FactService:
    """Service for managing learned facts about the user."""

    def __init__(self, registry: SQLiteRegistry):
        self._registry = registry

    def remember(
        self,
        content: str,
        category: str = "general",
        source: str = "user",
        confidence_score: float = 1.0,
    ) -> Fact:
        """Store a new fact.

        Args:
            content: The fact to remember
            category: Category for organizing facts (e.g., 'personal', 'work')
            source: Where the fact came from ('user', 'inferred', etc.)
            confidence_score: Confidence in this fact (0-1)

        Returns:
            The created Fact
        """
        fact_id = str(uuid.uuid4())
        self._registry.insert_fact(
            fact_id=fact_id,
            content=content,
            category=category,
            source=source,
            confidence_score=confidence_score,
        )

        fact_data = self._registry.get_fact(fact_id)
        return Fact(**fact_data)

    def list_facts(self, category: Optional[str] = None) -> List[Fact]:
        """List all facts, optionally filtered by category.

        Args:
            category: Optional category filter

        Returns:
            List of Fact objects
        """
        rows = self._registry.list_facts(category=category)
        return [Fact(**row) for row in rows]

    def forget(self, fact_id: str) -> None:
        """Delete a fact.

        Args:
            fact_id: ID of the fact to delete
        """
        self._registry.delete_fact(fact_id)

    def get_fact(self, fact_id: str) -> Optional[Fact]:
        """Get a specific fact.

        Args:
            fact_id: ID of the fact

        Returns:
            Fact if found, None otherwise
        """
        row = self._registry.get_fact(fact_id)
        return Fact(**row) if row else None

    def mark_used(self, fact_id: str) -> None:
        """Mark a fact as used (for tracking).

        Args:
            fact_id: ID of the fact
        """
        self._registry.increment_fact_usage(fact_id)

    def get_relevant_facts(self, category: str, limit: int = 5) -> List[Fact]:
        """Get facts from a specific category.

        Args:
            category: Category to retrieve
            limit: Maximum number of facts

        Returns:
            List of Fact objects
        """
        facts = self.list_facts(category=category)
        return facts[:limit]
