import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.storage.sqlite_registry import SQLiteRegistry


class AnalyticsStats(BaseModel):
    """Analytics statistics about usage patterns."""

    # Session metrics
    total_sessions: int
    total_turns: int
    average_turns_per_session: float
    longest_session_turns: int

    # Activity metrics
    most_active_day: Optional[str]
    most_active_hour: Optional[int]
    sessions_per_day_avg: float

    # Conversation patterns
    top_question_words: List[tuple[str, int]]
    top_commands: List[tuple[str, int]]
    fact_categories_count: Dict[str, int]

    # Time metrics
    first_session: Optional[str]
    last_session: Optional[str]
    days_active: int


class AnalyticsService:
    """Service for analyzing conversation patterns and usage statistics."""

    def __init__(self, registry: SQLiteRegistry):
        self._registry = registry

    def get_analytics(self) -> AnalyticsStats:
        """Generate comprehensive analytics about usage patterns.

        Returns:
            AnalyticsStats with all computed metrics
        """
        sessions = self._registry.list_sessions(limit=10000)
        all_turns = self._get_all_turns(sessions)
        facts = self._registry.list_facts()

        return AnalyticsStats(
            total_sessions=len(sessions),
            total_turns=len(all_turns),
            average_turns_per_session=self._calc_avg_turns(sessions, all_turns),
            longest_session_turns=self._get_longest_session(sessions),
            most_active_day=self._get_most_active_day(sessions),
            most_active_hour=self._get_most_active_hour(all_turns),
            sessions_per_day_avg=self._calc_sessions_per_day(sessions),
            top_question_words=self._get_top_question_words(all_turns),
            top_commands=self._get_top_commands(all_turns),
            fact_categories_count=self._count_fact_categories(facts),
            first_session=self._get_first_session_date(sessions),
            last_session=self._get_last_session_date(sessions),
            days_active=self._calc_days_active(sessions),
        )

    def _get_all_turns(self, sessions: List[Dict]) -> List[Dict]:
        """Get all turns from all sessions."""
        all_turns = []
        for session in sessions:
            turns = self._registry.get_session_turns(session["session_id"])
            all_turns.extend(turns)
        return all_turns

    def _calc_avg_turns(self, sessions: List[Dict], all_turns: List[Dict]) -> float:
        """Calculate average turns per session."""
        if not sessions:
            return 0.0
        return len(all_turns) / len(sessions)

    def _get_longest_session(self, sessions: List[Dict]) -> int:
        """Get the longest session by turn count."""
        max_turns = 0
        for session in sessions:
            turns = self._registry.get_session_turns(session["session_id"])
            max_turns = max(max_turns, len(turns))
        return max_turns

    def _get_most_active_day(self, sessions: List[Dict]) -> Optional[str]:
        """Get the day of week with most sessions."""
        if not sessions:
            return None

        day_counts = defaultdict(int)
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for session in sessions:
            created_str = session["created_at"]
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                day_name = days[dt.weekday()]
                day_counts[day_name] += 1
            except (ValueError, AttributeError):
                pass

        if day_counts:
            most_common = max(day_counts.items(), key=lambda x: x[1])
            return f"{most_common[0]} ({most_common[1]} sessions)"
        return None

    def _get_most_active_hour(self, all_turns: List[Dict]) -> Optional[int]:
        """Get the hour of day with most turns."""
        if not all_turns:
            return None

        hour_counts = defaultdict(int)
        for turn in all_turns:
            created_str = turn.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                hour_counts[dt.hour] += 1
            except (ValueError, AttributeError):
                pass

        if hour_counts:
            most_active = max(hour_counts.items(), key=lambda x: x[1])
            return most_active[0]
        return None

    def _calc_sessions_per_day(self, sessions: List[Dict]) -> float:
        """Calculate average sessions per day of activity."""
        if not sessions:
            return 0.0

        days_active = self._calc_days_active(sessions)
        if days_active == 0:
            return 0.0

        return len(sessions) / days_active

    def _get_top_question_words(self, all_turns: List[Dict], limit: int = 5) -> List[tuple[str, int]]:
        """Get most common starting words in user questions."""
        if not all_turns:
            return []

        question_words = []
        for turn in all_turns:
            if turn.get("role") == "user":
                content = turn.get("content", "").lower().strip()
                if content:
                    # Get first word or first 2 words if it's a command
                    if content.startswith("/"):
                        first_word = content.split()[0]  # e.g., "/news", "/todo"
                    else:
                        first_word = content.split()[0]  # e.g., "what", "how", "why"

                    question_words.append(first_word)

        word_counts = Counter(question_words)
        return word_counts.most_common(limit)

    def _get_top_commands(self, all_turns: List[Dict], limit: int = 5) -> List[tuple[str, int]]:
        """Get most frequently used commands."""
        if not all_turns:
            return []

        commands = []
        for turn in all_turns:
            if turn.get("role") == "user":
                content = turn.get("content", "").lower().strip()
                if content.startswith("/"):
                    cmd = content.split()[0]  # e.g., "/news", "/todo"
                    commands.append(cmd)

        cmd_counts = Counter(commands)
        return cmd_counts.most_common(limit)

    def _count_fact_categories(self, facts: List[Dict]) -> Dict[str, int]:
        """Count facts by category."""
        categories = defaultdict(int)
        for fact in facts:
            category = fact.get("category", "general")
            categories[category] += 1
        return dict(categories)

    def _get_first_session_date(self, sessions: List[Dict]) -> Optional[str]:
        """Get the date of the first session."""
        if not sessions:
            return None

        dates = []
        for session in sessions:
            created_str = session["created_at"]
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                dates.append(dt)
            except (ValueError, AttributeError):
                pass

        if dates:
            first = min(dates)
            return first.strftime("%Y-%m-%d")
        return None

    def _get_last_session_date(self, sessions: List[Dict]) -> Optional[str]:
        """Get the date of the last session."""
        if not sessions:
            return None

        dates = []
        for session in sessions:
            updated_str = session["updated_at"]
            try:
                dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                dates.append(dt)
            except (ValueError, AttributeError):
                pass

        if dates:
            last = max(dates)
            return last.strftime("%Y-%m-%d")
        return None

    def _calc_days_active(self, sessions: List[Dict]) -> int:
        """Calculate number of unique days with activity."""
        if not sessions:
            return 0

        active_dates = set()
        for session in sessions:
            created_str = session["created_at"]
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                active_dates.add(dt.date())
            except (ValueError, AttributeError):
                pass

        return len(active_dates)
