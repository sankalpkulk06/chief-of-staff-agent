# Phase 3 — Habit Tracker CLI

**Est. effort:** 3–4 days  
**Dependencies:** `SQLiteRegistry` schema extension  
**Status:** Not started

---

## Goal

Add persistent habit tracking with streak calculation, weekly summaries, and a `/habit` command set that works in both `sage chat` and (later) WhatsApp.

---

## New Files

- `app/core/habit_service.py`
- `app/cli/commands_habit.py` — standalone `/habit` Typer commands (optional; commands also live inside `ChatService`)

## Modified Files

- `app/storage/sql_schema.sql` — add `habits` + `habit_logs` tables
- `app/core/chat_service.py` — inject `HabitService`; add `/habit` and `/habits` command handlers
- `app/config/settings.py` — add `HABIT_DEFAULT_REMINDER_TIME`
- `.env.example` — document new env vars

---

## Tasks

### 3.1 — SQLite schema

**File:** `app/storage/sql_schema.sql`

```sql
CREATE TABLE IF NOT EXISTS habits (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL COLLATE NOCASE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    reminder_time   TEXT DEFAULT '21:00',
    active          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id          TEXT PRIMARY KEY,
    habit_id    TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    status      TEXT DEFAULT 'done',   -- 'done' | 'skipped'
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_id ON habit_logs(habit_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_logged_at ON habit_logs(logged_at);
```

**AC:**
- [ ] Tables created on app start via `initialize_schema()`
- [ ] Foreign key cascade delete works (deleting a habit removes its logs)

---

### 3.2 — `HabitService`

**File:** `app/core/habit_service.py`

```python
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
    status: str   # 'done' | 'skipped'
    note: str

@dataclass
class HabitSummary:
    habit: Habit
    days_done: int        # out of last 7 days
    streak: int           # current consecutive 'done' streak
    logged_today: bool

class HabitService:
    def __init__(self, registry: SQLiteRegistry)
    
    def add_habit(self, name: str, reminder_time: str = "21:00") -> Habit
    def log_habit(self, name: str, status: str = "done", note: str = "") -> HabitLog
    def get_streak(self, habit_id: str) -> int
    def get_weekly_summary(self) -> list[HabitSummary]
    def get_unlogged_today(self) -> list[Habit]
    def delete_habit(self, name: str) -> bool
    def get_habit_by_name(self, name: str) -> Habit | None
```

**Streak logic:** Walk backwards from today. Count consecutive days that have at least one log with `status='done'`. Stop at the first day with no log or a `'skipped'` log.

**`get_unlogged_today`:** Returns habits where no log entry exists for today's date (used by scheduler in Phase 4).

**AC:**
- [ ] `add_habit("gym")` creates a row with `id=uuid4()`
- [ ] `log_habit("gym")` inserts a log for today; returns `HabitLog`
- [ ] `get_streak()` returns 0 if no logs, N for N consecutive done days
- [ ] `get_weekly_summary()` returns one `HabitSummary` per active habit
- [ ] `get_unlogged_today()` excludes habits already logged today
- [ ] `delete_habit("gym")` soft-deletes (sets `active=0`) — logs preserved

---

### 3.3 — Chat commands

**File:** `app/core/chat_service.py`

Inject `HabitService` as optional param: `habit_service: HabitService | None = None`

Add command dispatch in the message handler:

| Input | Action |
|-------|--------|
| `/habit add <name>` | `habit_service.add_habit(name)` |
| `/habit add <name> @<time>` | `add_habit(name, reminder_time=time)` |
| `/habit log <name>` | `log_habit(name, "done")` |
| `/habit log <name> skipped` | `log_habit(name, "skipped")` |
| `/habit delete <name>` | `delete_habit(name)` |
| `/habits` | `get_weekly_summary()` → formatted output |

**Parsing `@time`:** regex `@(\d{1,2}(?::\d{2})?(?:am|pm)?)` — convert to 24h string.

**AC:**
- [ ] Each command works in `sage chat` REPL
- [ ] Invalid habit name (doesn't exist) returns a friendly error, not an exception
- [ ] `/habits` when no habits returns "No habits tracked yet. Add one with `/habit add <name>`."

---

### 3.4 — `/habits` weekly summary output

Format:

```
📊 Habit Summary — Week of Apr 28, 2026

  gym          ████████░░   5/7 days   🔥 5-day streak
  reading      ██████░░░░   3/7 days   🔥 2-day streak
  meditation   ████░░░░░░   2/7 days   ❌ streak broken

Total logged this week: 10/21
```

Progress bar: 10 chars, filled with `█` for done days, `░` for missed. Scale: `filled = round(days_done / 7 * 10)`.

Streak status: `🔥 N-day streak` if streak > 0 and logged today; `❌ streak broken` if last log was not today.

**Helper function:** `format_weekly_summary(summaries: list[HabitSummary]) -> str` in the same file or a small formatting module.

**AC:**
- [ ] Output matches the format above
- [ ] Works cleanly when some habits have 0 logs

---

### 3.5 — Settings

**File:** `app/config/settings.py`

```python
HABIT_DEFAULT_REMINDER_TIME: str = "21:00"
```

**File:** `.env.example`

```env
HABIT_DEFAULT_REMINDER_TIME=21:00
```

---

## Acceptance Criteria (phase complete)

- [ ] `/habit add`, `/habit log`, `/habit delete`, `/habits` commands work in `sage chat`
- [ ] Streak calculated correctly (consecutive days with `status='done'`)
- [ ] Weekly summary shows progress bars and streak counts
- [ ] `get_unlogged_today()` returns correct habits for use by Phase 4 scheduler
- [ ] Deleting a habit sets `active=0`; it disappears from `/habits` but logs are retained
- [ ] All commands degrade gracefully when `HabitService` is `None` (disabled)
