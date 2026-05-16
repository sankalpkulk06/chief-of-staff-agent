# Sage — Feature PRD
### Web Search · WhatsApp Integration · Habit Tracker & Proactive Reminders

**Status:** Planned  
**Author:** Sankalp  
**Last Updated:** April 2026

---

## Table of Contents

1. [Overview](#overview)
2. [Feature 1 — Web Search](#feature-1--web-search)
3. [Feature 2 — WhatsApp Integration](#feature-2--whatsapp-integration)
4. [Feature 3 — Habit Tracker & Proactive Reminders](#feature-3--habit-tracker--proactive-reminders)
5. [Cross-Feature Dependencies](#cross-feature-dependencies)
6. [Build Order](#build-order)
7. [Out of Scope](#out-of-scope)

---

## Overview

These three features evolve Sage from a local CLI chatbot into a **proactive personal agent** that:
- Knows what's happening in the world (web search)
- Lives in your WhatsApp — accessible from your phone via text or voice note
- Tracks your habits and nudges you when you fall behind

Each feature is designed to be **independently shippable** but they compound: WhatsApp + proactive reminders + web search makes Sage feel like a genuine AI life assistant, not just a RAG wrapper.

---

## Feature 1 — Web Search

### Goal

Allow Sage to answer any factual question about the world using live web results, not just ingested documents or RSS news.

### Problem

Currently Sage can only answer questions using:
- Locally ingested documents (RAG)
- Google News RSS (`/news` command)

This means questions like "What is LangGraph?", "Who won the election?", or "What's the best way to learn Rust?" fall back to the LLM's training data, which may be outdated or hallucinated.

### Solution

Add a `WebSearchService` that wraps the Tavily API (or DuckDuckGo as a free fallback). Register it as a tool alongside `fetch_news` so the LLM can invoke it automatically based on the user's intent.

### User Stories

- As a user, I can ask "What is LangGraph?" and get a current, cited answer from the web
- As a user, I can ask "latest news on AI regulation" and Sage decides whether to use `/news` or web search
- As a user, I can use `sage ask "..."` in single-question mode and get a web-grounded answer

### Technical Design

**New file:** `sage/services/web_search_service.py`

```python
class WebSearchService:
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]
    def format_for_context(self, results: list[SearchResult]) -> str
```

**SearchResult schema:**
```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    published_date: str | None
```

**Tool registration** (alongside existing `fetch_news` tool):
```python
{
    "name": "web_search",
    "description": "Search the web for current factual information, definitions, tutorials, or anything not in the user's documents",
    "parameters": {
        "query": { "type": "string" }
    }
}
```

**Routing logic:**
- `/news <topic>` → always uses `NewsService` (curated news feed)
- "What is X?" / "How does Y work?" → LLM chooses `web_search`
- "Latest news on X" → LLM may choose either; `web_search` preferred if no strong news signal

**Primary API:** Tavily (`tavily-python` package)
- Free tier: 1,000 searches/month — sufficient for personal use
- Returns clean LLM-ready snippets

**Fallback:** DuckDuckGo via `duckduckgo-search` package (no API key required)

### Citation Format

Web search results cited separately from documents and news:

```
According to [1], LangGraph is a framework for building stateful multi-agent workflows.

web sources:
- [1] LangGraph Docs — https://langchain-ai.github.io/langgraph/
- [2] LangGraph Tutorial — https://medium.com/...
```

### Environment Variables

```env
TAVILY_API_KEY=your_key_here          # Optional — falls back to DuckDuckGo if absent
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROVIDER=tavily            # "tavily" | "duckduckgo"
```

### Acceptance Criteria

- [ ] `WebSearchService.search()` returns results from Tavily or DuckDuckGo
- [ ] Web search registered as a callable tool in `ChatService`
- [ ] LLM correctly routes factual questions to web search vs. RAG vs. news
- [ ] Results cited with title + URL in response
- [ ] Works in both `sage chat` and `sage ask` modes
- [ ] Fallback to DuckDuckGo if Tavily key is not set
- [ ] `/search <query>` command as explicit shortcut

---

## Feature 2 — WhatsApp Integration

### Goal

Make Sage accessible from your phone via WhatsApp — supporting text messages and voice notes — so you can interact with your personal agent anywhere without opening a terminal.

### Problem

Sage is terminal-only. This limits usage to when you're at your laptop. The most valuable moments for a personal agent (on the go, quick reminders, voice memos) are exactly when the CLI is inaccessible.

### Solution

Build a webhook server that receives WhatsApp messages via Twilio, routes them through the existing `ChatService`, and sends replies back via Twilio's WhatsApp API. Voice notes are transcribed locally using Whisper before being passed to `ChatService`.

### User Stories

- As a user, I can send a WhatsApp message to Sage and get a reply just like in CLI chat
- As a user, I can send a voice note and Sage transcribes it and responds in text
- As a user, my WhatsApp session has persistent memory — Sage remembers what I told it earlier
- As a user, all existing commands (`/news`, `/todo`, `/facts`, etc.) work over WhatsApp
- As a user, Sage can send *me* messages proactively (reminders, briefings)

### Technical Design

**New file:** `sage/services/whatsapp_service.py`  
**New file:** `sage/webhook/server.py` (FastAPI app)

**Message flow:**
```
WhatsApp (your phone)
    ↓  message / voice note
Twilio
    ↓  POST /webhook
FastAPI webhook server
    ↓  WhatsAppService.handle_incoming()
    │   ├── Text message → ChatService.chat()
    │   └── Voice note   → WhisperService.transcribe() → ChatService.chat()
    ↓  response string
WhatsAppService.send_message()
    ↓  Twilio API
WhatsApp (your phone)
```

**Session mapping:**

Each WhatsApp number maps to a persistent Sage session:
```python
# In SQLiteRegistry — new table
whatsapp_sessions:
    phone_number  TEXT PRIMARY KEY
    session_id    TEXT  # maps to existing sessions table
    created_at    DATETIME
    last_active   DATETIME
```

**WhatsApp webhook server:**

```python
# sage/webhook/server.py
POST /webhook          # Twilio sends all incoming messages here
GET  /health           # Health check
```

Twilio sends a `application/x-www-form-urlencoded` POST with:
- `From` — sender's WhatsApp number (`whatsapp:+1415...`)
- `Body` — text content (empty for voice notes)
- `MediaUrl0` — URL to audio file (for voice notes)
- `MediaContentType0` — `audio/ogg` for WhatsApp voice notes

**Voice note pipeline:**

```python
class WhisperService:
    def transcribe(self, audio_url: str) -> str:
        # 1. Download audio from Twilio MediaUrl
        # 2. Convert ogg → wav (ffmpeg)
        # 3. Run whisper.transcribe(audio_path)
        # 4. Return transcript string
```

Uses `openai-whisper` package with the `base` model locally (~140MB). No API calls.

**Outbound messaging:**

```python
class WhatsAppService:
    def send_message(self, to: str, body: str) -> None
    def send_media(self, to: str, media_url: str, caption: str = "") -> None
```

Used by both the webhook response path AND the scheduler (for proactive reminders).

**Message length handling:**

WhatsApp has a 1600 character limit. Long responses are split at sentence boundaries and sent as sequential messages.

### Infrastructure

**Development:** ngrok tunnel
```bash
ngrok http 8000
# Set Twilio webhook to: https://abc123.ngrok.io/webhook
```

**Running alongside CLI:**
```bash
sage serve          # starts the webhook server on port 8000
sage chat           # still works independently
```

**Production (optional):** Any VPS with a public IP (Fly.io free tier, Railway, etc.)

### Twilio Setup (documented in README)

1. Create Twilio account → get a WhatsApp-enabled number (~$1/month)
2. Enable WhatsApp Sandbox for development (free)
3. Set webhook URL to your ngrok/VPS URL
4. Add credentials to `.env`

### Environment Variables

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886   # Your Twilio number
WEBHOOK_PORT=8000
WHISPER_MODEL=base                              # tiny | base | small
WHATSAPP_ENABLED=true
```

### Acceptance Criteria

- [ ] `sage serve` starts a FastAPI server that receives Twilio webhooks
- [ ] Text messages routed through `ChatService` with persistent session per phone number
- [ ] Voice notes transcribed via local Whisper and answered as text
- [ ] All existing commands work over WhatsApp (`/todo`, `/news`, `/facts`, `/remember-*`)
- [ ] Responses over 1600 chars split across multiple messages
- [ ] `WhatsAppService.send_message()` usable by scheduler for proactive messages
- [ ] Session persists across WhatsApp conversations (same session_id for same number)
- [ ] ngrok setup documented for local dev

---

## Feature 3 — Habit Tracker & Proactive Reminders

### Goal

Let Sage track personal habits, show streaks, and proactively message you via WhatsApp when you haven't logged a habit or have a todo due — turning Sage from a reactive chatbot into a proactive life assistant.

### Problem

Sage currently only responds when you talk to it. A real personal agent should notice when you're falling behind on your goals and reach out. There's also no way to track recurring behaviors like gym, reading, or sleep.

### Solution

Build a `HabitService` backed by SQLite with streak tracking, a `/habit` command set, and an `APScheduler`-powered background scheduler that sends WhatsApp nudges at configurable times.

### User Stories

- As a user, I can create habits with `/habit add gym`
- As a user, I can log a habit with `/habit log gym` and see my current streak
- As a user, I can view a weekly summary of all habits with `/habits`
- As a user, Sage messages me on WhatsApp at 9pm if I haven't logged a habit that day
- As a user, I can reply "done" or "skipped today" to log directly from the reminder
- As a user, I receive a morning WhatsApp briefing with today's habits, pending todos, and top news

### Technical Design

**New file:** `sage/services/habit_service.py`  
**New file:** `sage/scheduler/scheduler.py`

**Database schema (SQLite — extend existing registry.db):**

```sql
CREATE TABLE habits (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    reminder_time  TEXT DEFAULT '21:00',   -- 24h format
    active      BOOLEAN DEFAULT 1
);

CREATE TABLE habit_logs (
    id          TEXT PRIMARY KEY,
    habit_id    TEXT REFERENCES habits(id),
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    status      TEXT DEFAULT 'done',       -- 'done' | 'skipped'
    note        TEXT
);
```

**HabitService API:**

```python
class HabitService:
    def add_habit(self, name: str, reminder_time: str = "21:00") -> Habit
    def log_habit(self, name: str, status: str = "done", note: str = "") -> HabitLog
    def get_streak(self, habit_id: str) -> int
    def get_weekly_summary(self) -> list[HabitSummary]
    def get_unlogged_today(self) -> list[Habit]       # used by scheduler
    def delete_habit(self, name: str) -> bool
```

**Chat commands:**

| Command | Description |
|---------|-------------|
| `/habit add <name>` | Create a new habit |
| `/habit add <name> @9pm` | Create habit with custom reminder time |
| `/habit log <name>` | Log habit as done today |
| `/habit log <name> skipped` | Log habit as skipped |
| `/habit delete <name>` | Remove a habit |
| `/habits` | Show weekly summary with streaks |

**CLI output example:**

```
you: /habits

📊 Habit Summary — Week of Apr 28, 2026

  gym          ████████░░   5/7 days   🔥 5-day streak
  reading      ██████░░░░   3/7 days   🔥 2-day streak
  meditation   ████░░░░░░   2/7 days   ❌ streak broken

Total logged this week: 10/21
```

---

### Proactive Reminders (Scheduler)

**New file:** `sage/scheduler/scheduler.py`

Uses `APScheduler` (BSD licensed, lightweight, no additional infrastructure needed).

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

# Habit nudge — checks each habit at its configured reminder_time
scheduler.add_job(check_habits_and_nudge, 'cron', hour=21, minute=0)

# Morning briefing — sent every day at configured time
scheduler.add_job(send_morning_briefing, 'cron', hour=8, minute=0)

# Todo due reminders — checks every hour
scheduler.add_job(check_due_todos, 'interval', hours=1)
```

**Habit nudge logic:**

```python
def check_habits_and_nudge():
    unlogged = habit_service.get_unlogged_today()
    for habit in unlogged:
        msg = f"Hey — you haven't logged *{habit.name}* today. Still happening? 💪\n\nReply 'done' or 'skipped' to log it."
        whatsapp_service.send_message(YOUR_NUMBER, msg)
```

**Reply parsing (in webhook handler):**

When Sage sends a nudge, it stores the pending habit context. If the user replies with a short keyword, it's treated as a habit log:

```python
REPLY_MAP = {
    "done": "done",
    "yeah": "done",
    "yep": "done",
    "did it": "done",
    "skipped": "skipped",
    "nope": "skipped",
    "skip": "skipped",
    "no": "skipped"
}
```

If the reply doesn't match, it falls through to normal `ChatService` routing.

**Morning briefing format (WhatsApp message):**

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

**Todo due reminders:**

```python
def check_due_todos():
    # Query todos due in the next 60 minutes from SQLite
    # Send WhatsApp message for each
    # Mark as notified to avoid duplicate sends
```

Requires adding a `notified_at` column to the todos table.

### Environment Variables

```env
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00           # 24h format
YOUR_WHATSAPP_NUMBER=whatsapp:+1415...  # Your personal number for outbound
HABIT_DEFAULT_REMINDER_TIME=21:00
```

### Acceptance Criteria

- [ ] `/habit add`, `/habit log`, `/habit delete`, `/habits` commands work in CLI and WhatsApp
- [ ] Streak calculated correctly (consecutive days with `status=done`)
- [ ] Weekly summary shows progress bars and streak counts
- [ ] Scheduler starts automatically with `sage serve`
- [ ] Habit nudge sent via WhatsApp at configured time if not logged
- [ ] Short reply keywords ("done", "skipped", etc.) correctly log the habit
- [ ] Morning briefing sent at configured time with habits + news + todos
- [ ] Todo due reminders sent 60 minutes before due time
- [ ] Duplicate nudges prevented (one per habit per day)
- [ ] `SCHEDULER_ENABLED=false` disables all proactive messages without breaking CLI

---

## Cross-Feature Dependencies

| Feature | Depends On |
|---------|-----------|
| Web Search | Nothing — fully independent |
| WhatsApp text | Existing `ChatService`, `SQLiteRegistry` |
| WhatsApp voice | WhatsApp text + Whisper installed |
| Habit tracker (CLI) | `SQLiteRegistry` schema extension |
| Habit nudges | WhatsApp integration + Habit tracker + Scheduler |
| Morning briefing | WhatsApp + Habit tracker + `NewsService` + `TodoService` |
| Todo reminders | WhatsApp + Scheduler |

---

## Build Order

### Phase 1 — Web Search *(~3-4 days)*
No new infrastructure. Drop-in addition to existing tool system.
1. Add `WebSearchService` with Tavily + DuckDuckGo fallback
2. Register `web_search` as a tool in `ChatService`
3. Add `/search` command
4. Test routing: web search vs. RAG vs. news

### Phase 2 — WhatsApp Text *(~4-5 days)*
1. Set up Twilio sandbox + ngrok
2. Build FastAPI webhook server (`sage serve`)
3. Build `WhatsAppService` (send/receive)
4. Map phone numbers to sessions in SQLite
5. Route incoming messages through `ChatService`
6. Handle message length splitting

### Phase 3 — Habit Tracker CLI *(~3-4 days)*
1. Extend SQLite schema (habits + habit_logs tables)
2. Build `HabitService`
3. Add `/habit` commands to `ChatService`
4. Streak and weekly summary logic

### Phase 4 — Scheduler + Proactive Reminders *(~4-5 days)*
1. Add `APScheduler` to `sage serve`
2. Build habit nudge job
3. Build morning briefing job
4. Build todo due reminder job
5. Add reply parsing for nudge responses
6. Test end-to-end on real WhatsApp

### Phase 5 — WhatsApp Voice Notes *(~2-3 days)*
1. Install `openai-whisper` + `ffmpeg`
2. Build `WhisperService` (download → convert → transcribe)
3. Detect `MediaUrl0` in webhook payload and route to `WhisperService`
4. Test with real voice notes

**Total estimated effort:** 3-4 weeks of part-time building

---

## Out of Scope

The following are explicitly excluded from this PRD to keep scope manageable:

- Web UI or dashboard of any kind
- Multi-user support (Sage is personal-use only)
- WhatsApp group chat support
- Sending images or rich media back to WhatsApp (text-only responses)
- Hosting / deployment automation (manual VPS setup is fine)
- Calendar integration (separate future PRD)
- Auto fact extraction from conversation (separate future PRD)