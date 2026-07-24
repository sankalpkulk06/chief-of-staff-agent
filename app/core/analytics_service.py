import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

# Per-process TTL cache for the (costly) LLM insights digest: {(user_id, window): (ts, text)}
_INSIGHTS_CACHE: Dict[tuple, tuple] = {}
_INSIGHTS_TTL = 6 * 3600  # 6 hours

_TOPIC_STOPWORDS = {
    "the","a","an","is","it","what","how","can","do","i","my","me","you",
    "to","of","in","on","for","and","or","with","this","that","was","are",
    "be","have","has","had","will","would","could","should","about","from",
    "at","by","not","no","but","we","they","their","there","when","where",
    "which","who","why","please","just","some","any","all","get","its",
    "if","so","as","up","out","into","also","tell","show","need","want",
    "use","using","used","make","like","know","go","more","then","than",
    "your","our","his","her","them","been","did","does","let","now","new",
    "see","give","check","find","look","help","sage","hi","hey","ok","yes",
}


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

    def __init__(self, registry: Any):
        self._registry = registry

    def get_analytics(self, user_id: str) -> AnalyticsStats:
        """Generate comprehensive analytics about usage patterns."""
        sessions = self._registry.list_sessions(limit=10000, user_id=user_id)
        all_turns = self._get_all_turns(sessions)
        facts = self._registry.list_facts(user_id=user_id)

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

    # ------------------------------------------------------------------
    # Dashboard (windowed, multi-section) — the single source for web + CLI
    # ------------------------------------------------------------------

    def get_dashboard(self, user_id: str, window_days: int = 30) -> Dict[str, Any]:
        """Compute the full analytics payload over a rolling window (7/30/90 days).

        The window is anchored to UTC because the high-volume event tables
        (chat_turns, chat_sessions, learned_facts, agent_invocations) stamp
        created_at with SQLite CURRENT_TIMESTAMP / Postgres NOW() (UTC).
        """
        today = datetime.utcnow().date()
        win = max(1, int(window_days or 30))
        since = today - timedelta(days=win - 1)
        since_iso = since.isoformat()
        days = [since + timedelta(days=i) for i in range(win)]
        idx = {d: i for i, d in enumerate(days)}

        sessions = self._registry.list_sessions(limit=10000, user_id=user_id)
        all_turns = self._get_all_turns(sessions)

        # Usage: heatmap + daily volume + peak hour + active days (windowed)
        heatmap = [[0] * 24 for _ in range(7)]
        daily_vol = [0] * win
        hour_counts: Dict[int, int] = defaultdict(int)
        active_dates: set = set()
        user_turns: List[Dict] = []
        for t in all_turns:
            dt = self._parse_datetime(t.get("created_at", ""))
            if not dt or not (since <= dt.date() <= today):
                continue
            heatmap[dt.weekday()][dt.hour] += 1
            hour_counts[dt.hour] += 1
            active_dates.add(dt.date())
            if dt.date() in idx:
                daily_vol[idx[dt.date()]] += 1
            if t.get("role") == "user":
                user_turns.append(t)
        peak_hour = max(hour_counts.items(), key=lambda x: x[1])[0] if hour_counts else None
        source = self._registry.get_chat_source_counts(user_id, since_iso)

        # Sessions in window + spark
        sess_daily = [0] * win
        sess_count = 0
        for s in sessions:
            dt = self._parse_datetime(s.get("created_at"))
            if dt and dt.date() in idx:
                sess_count += 1
                sess_daily[idx[dt.date()]] += 1

        habits = self._habits_section(user_id, win)
        todos = self._todos_section(user_id, idx)

        agent_counts = self._registry.get_agent_usage(user_id, since_iso)
        total_ag = sum(agent_counts.values()) or 1
        agents = [
            {"name": k, "count": v, "pct": round(v / total_ag * 100)}
            for k, v in sorted(agent_counts.items(), key=lambda x: -x[1])
        ]

        facts = self._registry.list_facts(user_id=user_id)
        cats = self._count_fact_categories(facts)
        facts_daily = [0] * win
        for f in facts:
            dt = self._parse_datetime(f.get("created_at"))
            if dt and dt.date() in idx:
                facts_daily[idx[dt.date()]] += 1

        top_streak = max(habits, key=lambda h: h["streak"], default=None)
        kpis = {
            "sessions": sess_count,
            "sessions_spark": sess_daily,
            "days_active": len(active_dates),
            "peak_hour": peak_hour,
            "top_streak": ({"name": top_streak["name"], "days": top_streak["streak"]}
                           if top_streak and top_streak["streak"] > 0 else None),
            "todos_pct": todos["pct"],
            "todos_done": todos["done"],
            "todos_total": todos["total"],
            "overdue": todos["overdue"],
            "facts_total": len(facts),
            "facts_personal": cats.get("personal", 0),
            "facts_work": cats.get("work", 0),
            "facts_spark": facts_daily,
        }
        return {
            "window_days": win,
            "kpis": kpis,
            "habits": habits,
            "todos": todos,
            "usage": {"heatmap": heatmap, "peak_hour": peak_hour, "source": source, "daily": daily_vol},
            "agents": agents,
            "topics": self._top_topics(user_turns),
        }

    # ------------------------------------------------------------------
    # Insights digest (LLM narrative over the computed stats; cached)
    # ------------------------------------------------------------------

    def insights(self, user_id: str, window_days: int = 30) -> str:
        key = (user_id, int(window_days or 30))
        hit = _INSIGHTS_CACHE.get(key)
        if hit and (time.time() - hit[0]) < _INSIGHTS_TTL:
            return hit[1]
        data = self.get_dashboard(user_id, window_days)
        text = self._generate_insight(data)
        _INSIGHTS_CACHE[key] = (time.time(), text)
        return text

    def _generate_insight(self, data: Dict[str, Any]) -> str:
        summary = self._facts_for_prompt(data)
        if not summary.strip():
            return "Not enough activity yet — check back once you've logged habits, todos, or a few chats."

        prompt = (
            "You are Sage, a personal chief-of-staff. Below are this user's own usage stats for the "
            f"last {data.get('window_days', 30)} days. Write 2-4 short sentences of plain-English insight: "
            "what's going well, what's slipping, and one concrete, kind suggestion. Refer to specifics "
            "(habit names, numbers). No preamble, no bullet points, no markdown headers.\n\n"
            f"{summary}"
        )
        try:
            from app.config.settings import get_settings
            from app.providers.factory import create_default_chat_provider
            out = create_default_chat_provider(get_settings()).generate(prompt)
            out = (out or "").strip()
            if out:
                return out
        except Exception:
            pass
        return self._fallback_insight(data)

    @staticmethod
    def _facts_for_prompt(data: Dict[str, Any]) -> str:
        k = data.get("kpis", {})
        lines = []
        if data.get("habits"):
            hb = ", ".join(f"{h['name']} {h['pct']}% (streak {h['streak']})" for h in data["habits"])
            lines.append(f"Habits: {hb}")
        t = data.get("todos", {})
        if t.get("total"):
            lines.append(f"Todos: {t['done']}/{t['total']} done ({t['pct']}%), {t['overdue']} overdue"
                         + (f", avg {t['avg_days']}d to finish" if t.get("avg_days") is not None else ""))
        if data.get("agents"):
            ag = ", ".join(f"{a['name'].replace('_agent','')} {a['pct']}%" for a in data["agents"][:5])
            lines.append(f"Feature usage: {ag}")
        if k.get("peak_hour") is not None:
            lines.append(f"Most active hour: {k['peak_hour']}:00 UTC; {k.get('days_active', 0)} active days; "
                         f"{k.get('sessions', 0)} sessions")
        if k.get("facts_total"):
            lines.append(f"Memory: {k['facts_total']} facts")
        return "\n".join(lines)

    @staticmethod
    def _fallback_insight(data: Dict[str, Any]) -> str:
        """Deterministic summary when the LLM is unavailable."""
        parts = []
        habits = data.get("habits") or []
        best = max(habits, key=lambda h: h["streak"], default=None)
        if best and best["streak"] > 0:
            parts.append(f"Your strongest habit is {best['name']} ({best['streak']}-day streak).")
        t = data.get("todos", {})
        if t.get("total"):
            parts.append(f"You've completed {t['done']} of {t['total']} todos ({t['pct']}%)"
                         + (f", with {t['overdue']} overdue." if t.get("overdue") else "."))
        ag = (data.get("agents") or [])[:1]
        if ag:
            parts.append(f"You lean most on {ag[0]['name'].replace('_agent','')} ({ag[0]['pct']}%).")
        return " ".join(parts) or "Not enough activity yet to summarize."

    def _habits_section(self, user_id: str, win: int) -> List[Dict]:
        from app.core.habit_service import HabitService
        # Habits are stored/streaked in LOCAL time (habit_service uses datetime.now()
        # + date.today()), so anchor the habit window locally so "logged today" lines up.
        today = date.today()
        since = today - timedelta(days=win - 1)
        days = [since + timedelta(days=i) for i in range(win)]
        hs = HabitService(self._registry, user_id)
        try:
            habits = hs._get_all_active()
        except Exception:
            return []
        day_set = set(days)
        out = []
        for h in habits:
            try:
                logs = hs.get_logs_since(h.id, since)
                done = {self._parse_date(l["day"]) for l in logs if l.get("status") == "done"}
                streak = hs.get_streak(h.id)
            except Exception:
                done, streak = set(), 0
            series = [1 if d in done else 0 for d in days]
            pct = round(len(done & day_set) / win * 100) if win else 0
            out.append({"name": h.name, "pct": pct, "streak": streak, "series": series})
        return out

    def _todos_section(self, user_id: str, idx: Dict[date, int]) -> Dict[str, Any]:
        rows = self._registry.list_all_todos(user_id)
        win = len(idx)
        created = [0] * win
        completed = [0] * win
        done_in = 0
        open_now = 0
        overdue = 0
        times: List[float] = []
        now = datetime.now()
        for r in rows:
            c = self._parse_datetime(r.get("created_at"))
            comp = self._parse_datetime(r.get("completed_at"))
            due = self._parse_datetime(r.get("due_at"))
            if comp is None:
                open_now += 1
                if due and due < now:
                    overdue += 1
            if c and c.date() in idx:
                created[idx[c.date()]] += 1
            if comp and comp.date() in idx:
                completed[idx[comp.date()]] += 1
                done_in += 1
                if c and comp >= c:  # drop noise (created_at/completed_at tz skew)
                    times.append((comp - c).total_seconds() / 86400)
        total = done_in + open_now
        pct = round(done_in / total * 100) if total else 0
        return {
            "pct": pct,
            "done": done_in,
            "total": total,
            "overdue": overdue,
            "avg_days": round(mean(times), 1) if times else None,
            "throughput_created": created,
            "throughput_completed": completed,
        }

    @staticmethod
    def _top_topics(user_turns: List[Dict], limit: int = 6) -> List[Dict]:
        counts: Dict[str, int] = {}
        for r in user_turns[-500:]:
            for word in (r.get("content") or "").lower().split():
                w = word.strip(".,!?;:\"'()[]{}")
                if len(w) > 3 and w not in _TOPIC_STOPWORDS and w.isalpha():
                    counts[w] = counts.get(w, 0) + 1
        top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        mx = top[0][1] if top else 1
        return [{"label": w, "count": c, "pct": round(c / mx * 100)} for w, c in top]

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

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
            dt = self._parse_datetime(session["created_at"])
            if dt is not None:
                day_name = days[dt.weekday()]
                day_counts[day_name] += 1

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
            dt = self._parse_datetime(turn.get("created_at", ""))
            if dt is not None:
                hour_counts[dt.hour] += 1

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
            dt = self._parse_datetime(session["created_at"])
            if dt is not None:
                dates.append(dt)

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
            dt = self._parse_datetime(session["updated_at"])
            if dt is not None:
                dates.append(dt)

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
            dt = self._parse_datetime(session["created_at"])
            if dt is not None:
                active_dates.add(dt.date())

        return len(active_dates)

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None
