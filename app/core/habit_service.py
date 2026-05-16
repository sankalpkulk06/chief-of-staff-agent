import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from app.storage.sqlite_registry import SQLiteRegistry


@dataclass
class Habit:
    id: str
    name: str
    reminder_time: str
    active: bool


@dataclass
class HabitLog:
    id: str
    habit_id: str
    logged_at: datetime
    status: str
    note: str


@dataclass
class HabitSummary:
    habit: Habit
    days_done: int       # out of last 7 days
    streak: int          # current consecutive 'done' streak
    logged_today: bool


def _parse_reminder_time(raw: str) -> str:
    """Convert human time like '9pm', '9:30am', '14:00' to 24h 'HH:MM' string."""
    raw = raw.strip().lower()
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw)
    if not match:
        return "21:00"
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


class HabitService:
    def __init__(self, registry: SQLiteRegistry):
        self._db = registry._connection

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _get_habit_by_name(self, name: str) -> Optional[Habit]:
        row = self._db.execute(
            "SELECT id, name, reminder_time, active FROM habits WHERE name = ? COLLATE NOCASE AND active = 1",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return Habit(id=row["id"], name=row["name"], reminder_time=row["reminder_time"], active=bool(row["active"]))

    def get_habit_by_id(self, habit_id: str) -> Optional[Habit]:
        row = self._db.execute(
            "SELECT id, name, reminder_time, active FROM habits WHERE id = ? AND active = 1",
            (habit_id,),
        ).fetchone()
        if row is None:
            return None
        return Habit(id=row["id"], name=row["name"], reminder_time=row["reminder_time"], active=bool(row["active"]))

    def _get_all_active(self) -> list[Habit]:
        rows = self._db.execute(
            "SELECT id, name, reminder_time, active FROM habits WHERE active = 1 ORDER BY created_at ASC"
        ).fetchall()
        return [Habit(id=r["id"], name=r["name"], reminder_time=r["reminder_time"], active=True) for r in rows]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_habit(self, name: str, reminder_time: str = "21:00") -> Habit:
        existing = self._get_habit_by_name(name)
        if existing:
            return existing
        habit_id = str(uuid.uuid4())
        rt = _parse_reminder_time(reminder_time) if reminder_time != "21:00" else reminder_time
        self._db.execute(
            "INSERT INTO habits (id, name, reminder_time) VALUES (?, ?, ?)",
            (habit_id, name, rt),
        )
        self._db.commit()
        return Habit(id=habit_id, name=name, reminder_time=rt, active=True)

    def log_habit(self, name: str, status: str = "done", note: str = "") -> HabitLog:
        habit = self._get_habit_by_name(name)
        if habit is None:
            raise ValueError(f"Habit '{name}' not found. Add it first with /habit add {name}")
        return self.log_habit_by_id(habit.id, status=status, note=note)

    def log_habit_by_id(self, habit_id: str, status: str = "done", note: str = "") -> HabitLog:
        habit = self.get_habit_by_id(habit_id)
        if habit is None:
            raise ValueError(f"Habit '{habit_id}' not found.")
        log_id = str(uuid.uuid4())
        now = datetime.now()
        self._db.execute(
            "INSERT INTO habit_logs (id, habit_id, logged_at, status, note) VALUES (?, ?, ?, ?, ?)",
            (log_id, habit_id, now.isoformat(), status, note),
        )
        self._db.commit()
        return HabitLog(id=log_id, habit_id=habit_id, logged_at=now, status=status, note=note)

    def unlog_habit(self, name: str) -> int:
        """Delete all log entries for today for the given habit. Returns rows deleted."""
        habit = self._get_habit_by_name(name)
        if habit is None:
            raise ValueError(f"Habit '{name}' not found.")
        today = date.today().isoformat()
        cursor = self._db.execute(
            "DELETE FROM habit_logs WHERE habit_id = ? AND DATE(logged_at) = ?",
            (habit.id, today),
        )
        self._db.commit()
        return cursor.rowcount

    def delete_habit(self, name: str) -> bool:
        habit = self._get_habit_by_name(name)
        if habit is None:
            return False
        self._db.execute("UPDATE habits SET active = 0 WHERE id = ?", (habit.id,))
        self._db.commit()
        return True

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_streak(self, habit_id: str) -> int:
        rows = self._db.execute(
            """
            SELECT DATE(logged_at) as day, status
            FROM habit_logs
            WHERE habit_id = ?
            ORDER BY logged_at DESC
            """,
            (habit_id,),
        ).fetchall()

        # Build a set of done-days (one per calendar day)
        done_days: set[date] = set()
        for row in rows:
            if row["status"] == "done":
                done_days.add(date.fromisoformat(row["day"]))

        streak = 0
        check = date.today()
        while check in done_days:
            streak += 1
            check -= timedelta(days=1)
        return streak

    def get_weekly_summary(self) -> list[HabitSummary]:
        habits = self._get_all_active()
        today = date.today()
        week_ago = today - timedelta(days=6)
        summaries: list[HabitSummary] = []

        for habit in habits:
            rows = self._db.execute(
                """
                SELECT DATE(logged_at) as day, status
                FROM habit_logs
                WHERE habit_id = ? AND DATE(logged_at) >= ?
                """,
                (habit.id, week_ago.isoformat()),
            ).fetchall()

            done_days: set[date] = set()
            logged_today = False
            for row in rows:
                d = date.fromisoformat(row["day"])
                if row["status"] == "done":
                    done_days.add(d)
                if d == today:
                    logged_today = True

            summaries.append(HabitSummary(
                habit=habit,
                days_done=len(done_days),
                streak=self.get_streak(habit.id),
                logged_today=logged_today,
            ))

        return summaries

    def get_unlogged_today(self) -> list[Habit]:
        today = date.today().isoformat()
        rows = self._db.execute(
            """
            SELECT h.id FROM habits h
            WHERE h.active = 1
              AND h.id NOT IN (
                SELECT habit_id FROM habit_logs WHERE DATE(logged_at) = ?
              )
            """,
            (today,),
        ).fetchall()
        ids = {r["id"] for r in rows}
        return [h for h in self._get_all_active() if h.id in ids]
