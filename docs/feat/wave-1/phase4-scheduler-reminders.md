# Phase 4 — Scheduler & Proactive Reminders

**Est. effort:** 4–5 days  
**Dependencies:** Phase 2 (WhatsApp) + Phase 3 (Habit Tracker)  
**Status:** Not started

---

## Goal

APScheduler runs three background jobs inside `sage serve`: habit nudges at 9pm, a morning briefing at 8am, and hourly todo due reminders. Short reply keywords ("done", "skipped") from nudges log the habit without going through the LLM.

---

## New Files

- `app/scheduler/__init__.py`
- `app/scheduler/scheduler.py`

## Modified Files

- `app/webhook/server.py` — start scheduler on app startup; add nudge reply parsing
- `app/storage/sql_schema.sql` — add `notified_at` column to todos table; add `nudge_context` table
- `app/storage/sqlite_registry.py` — add `get_todos_due_soon()`, `mark_todo_notified()`, nudge context CRUD
- `app/config/settings.py` — add scheduler env vars
- `.env.example` — document new env vars
- `setup.py` / `pyproject.toml` — add `APScheduler`

---

## Tasks

### 4.1 — SQLite schema additions

**File:** `app/storage/sql_schema.sql`

Add `notified_at` to todos (prevents duplicate reminders):
```sql
ALTER TABLE todos ADD COLUMN notified_at DATETIME;
```
> Use `CREATE TABLE IF NOT EXISTS` pattern to avoid errors on re-run — wrap ALTER in a migration check.

Add nudge context (tracks which habit a user was nudged about):
```sql
CREATE TABLE IF NOT EXISTS nudge_context (
    phone_number  TEXT PRIMARY KEY,
    habit_id      TEXT REFERENCES habits(id),
    sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**AC:**
- [ ] `notified_at` column exists after schema init
- [ ] `nudge_context` table created automatically

---

### 4.2 — `SQLiteRegistry` additions

**File:** `app/storage/sqlite_registry.py`

```python
def get_todos_due_soon(self, minutes_ahead: int = 60) -> list[dict]:
    # Returns todos where due_at is within next `minutes_ahead` minutes
    # AND notified_at IS NULL

def mark_todo_notified(self, todo_id: str) -> None

def set_nudge_context(self, phone_number: str, habit_id: str) -> None

def get_nudge_context(self, phone_number: str) -> str | None:
    # Returns habit_id if a nudge was sent in the last 24h, else None

def clear_nudge_context(self, phone_number: str) -> None
```

**AC:**
- [ ] `get_todos_due_soon()` excludes already-notified todos
- [ ] `nudge_context` persists across server restarts

---

### 4.3 — Scheduler

**File:** `app/scheduler/scheduler.py`

```python
from apscheduler.schedulers.background import BackgroundScheduler

def build_scheduler(
    habit_service: HabitService,
    whatsapp_service: WhatsAppService,
    news_service: NewsService,
    registry: SQLiteRegistry,
    your_number: str,
    morning_briefing_time: str = "08:00",    # "HH:MM"
    habit_nudge_time: str = "21:00",         # "HH:MM"
) -> BackgroundScheduler:

    scheduler = BackgroundScheduler()
    
    h, m = map(int, habit_nudge_time.split(":"))
    scheduler.add_job(
        check_habits_and_nudge,
        "cron", hour=h, minute=m,
        args=[habit_service, whatsapp_service, registry, your_number]
    )
    
    h, m = map(int, morning_briefing_time.split(":"))
    scheduler.add_job(
        send_morning_briefing,
        "cron", hour=h, minute=m,
        args=[habit_service, whatsapp_service, news_service, registry, your_number]
    )
    
    scheduler.add_job(
        check_due_todos,
        "interval", hours=1,
        args=[whatsapp_service, registry, your_number]
    )
    
    return scheduler
```

**Job functions (in the same file):**

```python
def check_habits_and_nudge(habit_service, whatsapp_service, registry, your_number):
    unlogged = habit_service.get_unlogged_today()
    for habit in unlogged:
        registry.set_nudge_context(your_number, habit.id)
        msg = (
            f"Hey — you haven't logged *{habit.name}* today. Still happening? 💪\n\n"
            "Reply 'done' or 'skipped' to log it."
        )
        whatsapp_service.send_message(your_number, msg)

def send_morning_briefing(habit_service, whatsapp_service, news_service, registry, your_number):
    # Build the briefing string (habits + news + due todos)
    # See format spec below
    whatsapp_service.send_message(your_number, briefing)

def check_due_todos(whatsapp_service, registry, your_number):
    due = registry.get_todos_due_soon(minutes_ahead=60)
    for todo in due:
        msg = f"⏰ Reminder: *{todo['title']}* is due soon."
        whatsapp_service.send_message(your_number, msg)
        registry.mark_todo_notified(todo["id"])
```

**AC:**
- [ ] `build_scheduler()` returns a configured `BackgroundScheduler` (not yet started)
- [ ] Each job function is independently callable (no global state)
- [ ] Habit nudge sends one message per unlogged habit

---

### 4.4 — Start scheduler in `sage serve`

**File:** `app/webhook/server.py`

```python
@app.on_event("startup")
def startup():
    if settings.SCHEDULER_ENABLED and settings.YOUR_WHATSAPP_NUMBER:
        scheduler = build_scheduler(...)
        scheduler.start()
        app.state.scheduler = scheduler

@app.on_event("shutdown")
def shutdown():
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown(wait=False)
```

**AC:**
- [ ] Scheduler starts when `sage serve` starts (if `SCHEDULER_ENABLED=true`)
- [ ] Scheduler shuts down cleanly on Ctrl-C
- [ ] `SCHEDULER_ENABLED=false` skips scheduler entirely

---

### 4.5 — Nudge reply parsing

**File:** `app/webhook/server.py`

In the `POST /webhook` handler, before routing to `ChatService`:

```python
REPLY_MAP = {
    "done": "done", "yeah": "done", "yep": "done", "did it": "done",
    "skipped": "skipped", "nope": "skipped", "skip": "skipped", "no": "skipped"
}

body_lower = Body.strip().lower()
pending_habit_id = registry.get_nudge_context(phone)

if pending_habit_id and body_lower in REPLY_MAP:
    status = REPLY_MAP[body_lower]
    habit = habit_service.get_habit_by_id(pending_habit_id)
    habit_service.log_habit_by_id(pending_habit_id, status)
    registry.clear_nudge_context(phone)
    reply = f"✅ Logged *{habit.name}* as {status} for today!"
    whatsapp_service.send_message(to=phone, body=reply)
    return Response(content="", media_type="application/xml")

# Fall through to normal ChatService routing
```

**AC:**
- [ ] Reply "done" after a nudge logs the correct habit and clears the context
- [ ] Reply "skipped" logs as skipped
- [ ] Unrecognized reply falls through to normal LLM chat
- [ ] Nudge context expires after 24h (check `sent_at` in `get_nudge_context`)

---

### 4.6 — Morning briefing format

```
☀️ Good morning, Sankalp!

📋 Today's Habits
  • gym (🔥 4-day streak — keep it going!)
  • reading
  • meditation

📰 Top News
  • [1] OpenAI releases GPT-5 — The Verge
  • [2] Fed holds rates steady — Reuters

✅ Due Today
  • Email tax documents (3:00 PM)
  • Call dentist

Have a great day!
```

Build the string from live data: `habit_service.get_weekly_summary()` for habits, `news_service.get_news()` for top 3 headlines, `registry.get_todos_due_soon(minutes_ahead=1440)` for today's todos.

---

### 4.7 — Settings + env vars

**File:** `app/config/settings.py`

```python
SCHEDULER_ENABLED: bool = True
MORNING_BRIEFING_TIME: str = "08:00"
YOUR_WHATSAPP_NUMBER: str = ""    # "whatsapp:+1415..."
HABIT_NUDGE_TIME: str = "21:00"
```

**File:** `.env.example`

```env
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234
HABIT_NUDGE_TIME=21:00
```

---

### 4.8 — Dependencies

```
APScheduler>=3.10
```

---

## Acceptance Criteria (phase complete)

- [ ] Scheduler starts automatically with `sage serve` when `SCHEDULER_ENABLED=true`
- [ ] Habit nudge sent via WhatsApp at configured time if habit not logged
- [ ] Each habit gets at most one nudge per day (no duplicates)
- [ ] Short reply keywords ("done", "skipped", etc.) log the correct habit
- [ ] Morning briefing sent at configured time with habits + news + todos
- [ ] Todo due reminders sent ~60 minutes before due time
- [ ] Duplicate todo notifications prevented via `notified_at` column
- [ ] `SCHEDULER_ENABLED=false` disables all proactive messages without breaking CLI or `/health`
- [ ] Scheduler shuts down cleanly on server stop
