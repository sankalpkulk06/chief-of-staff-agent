# Phase 3 — Weekly Review

**Est. effort:** 3–4 days
**Dependencies:** All existing data stores (habits, todos, facts, sessions); richer with Phase 1 (URL ingestion) and Phase 2 (followed topics) data
**Status:** Not started

---

## Goal

When the user asks for a review of their week, Sage queries all its data stores for the past 7 days, passes a structured snapshot to the LLM, and returns a coherent narrative — delivered via WhatsApp (3 messages) or CLI (richer formatting). Section-specific queries (`"how did my habits go?"`) return only that section.

On-demand only — no scheduled push.

---

## New Files

- `app/services/review_service.py` — data collection + LLM synthesis
- `app/core/review_intent.py` — trigger phrase detection (or inline in `chat_service.py`)
- `tests/services/test_review_service.py` — tests for each data collector + empty-state handling

## Modified Files

- `app/storage/sqlite_registry.py` — add `since`-filtered queries for habits, todos, facts, sessions
- `app/core/chat_service.py` — review intent detection, section routing, output channel detection
- `app/webhook/server.py` — multi-message WhatsApp delivery for the review
- `app/cli/commands_chat.py` — rich formatting for CLI review output
- `app/config/settings.py` — `REVIEW_DEFAULT_DAYS`, `REVIEW_WHATSAPP_MAX_WORDS`, `REVIEW_INCLUDE_TOP_TOPICS`
- `.env.example` — document new env vars

---

## Tasks

### 3.1 — Data collection methods on `SQLiteRegistry`

**File:** `app/storage/sqlite_registry.py`

Add `since`-filtered queries (some may already exist — reuse if so):

```python
def get_habit_logs_since(self, since: datetime) -> list[dict]: ...
# Each row: {habit_id, habit_name, logged_at, ...}

def get_todos_since(self, since: datetime) -> dict:
    return {
        "completed": [...],   # completed_at >= since
        "open": [...],         # completed_at IS NULL
        "overdue": [...],      # open AND due_date < now
    }

def get_facts_since(self, since: datetime) -> list[dict]: ...
def get_sessions_since(self, since: datetime) -> list[dict]: ...
def get_total_turns_since(self, since: datetime) -> int: ...
def get_url_documents_since(self, since: datetime) -> list[dict]: ...   # source_type='url' AND ingested_at >= since
def get_topics_followed_since(self, since: datetime) -> list[dict]: ... # from followed_topics
```

**AC:**
- [ ] Each query returns `[]` (or `0` for counts) when no data exists in the window
- [ ] Queries respect timezone — `since` is UTC, comparisons are UTC

---

### 3.2 — `ReviewService` data layer

**File:** `app/services/review_service.py`

```python
@dataclass
class HabitSummary:
    name: str
    days_logged: int          # in window
    window_days: int          # usually 7
    streak: int               # current streak as of period_end
    note: str | None          # qualitative note ("best this month", "missed mid-week")

@dataclass
class WeeklyReviewData:
    period_start: datetime
    period_end: datetime
    habits: list[HabitSummary]
    todos_completed: list[dict]
    todos_open: list[dict]
    todos_overdue: list[dict]
    facts_saved: list[dict]
    topics_followed: list[str]
    sessions_count: int
    total_turns: int
    ingested_urls: list[dict]
    top_topics: list[str]      # LLM-extracted, optional

class ReviewService:
    def __init__(self, registry, chat_provider, settings): ...

    def generate(self, days: int = 7, section: str | None = None) -> str:
        data = self._collect(days)
        if section:
            return self._render_section(section, data)
        return self.synthesize(data)

    def _collect(self, days: int) -> WeeklyReviewData: ...
    def _build_habit_summaries(self, logs: list[dict]) -> list[HabitSummary]: ...
    def _extract_top_topics(self, sessions: list[dict]) -> list[str]: ...   # optional LLM call
    def synthesize(self, data: WeeklyReviewData) -> str: ...                 # LLM call
    def _render_section(self, section: str, data: WeeklyReviewData) -> str: ...
```

**AC:**
- [ ] `_collect()` runs each registry query independently — one failing returns empty for that field, never breaks the whole review
- [ ] `_build_habit_summaries()` computes streak across the window correctly
- [ ] `_extract_top_topics()` returns `[]` if `REVIEW_INCLUDE_TOP_TOPICS=false` or no sessions

---

### 3.3 — Synthesis prompt + LLM call

**`REVIEW_PROMPT`:**

```
You are Sage, a personal AI assistant generating a weekly review for your user.

Here is the data from their past {days} days:
{structured_data_as_json}

Write a warm, concise weekly review that:
- Celebrates wins (streaks, completed todos)
- Honestly notes areas for improvement (missed habits, overdue todos)
- Summarizes what they learned and explored
- Ends with one concrete, actionable focus for next week

Tone: like a thoughtful coach, not a robot reading a spreadsheet.
Use these section headers exactly:
🏋️ Habits
✅ Todos
🧠 What You Learned
💬 Topics You Explored
📌 Facts You Saved
🔮 One Thing to Focus on Next Week

Omit any section where there is no data — do NOT print empty headers.
Keep total length under {max_words} words.
```

`{structured_data_as_json}` is a JSON-serialized `WeeklyReviewData` (with datetime → ISO strings).

**AC:**
- [ ] LLM output starts with `📊 Your Week — {start} to {end}` header (added by `synthesize`, not the LLM)
- [ ] Empty sections omitted (verified with a test where `ingested_urls=[]`)
- [ ] Output stays under `REVIEW_WHATSAPP_MAX_WORDS` words (post-validate; truncate if over)

---

### 3.4 — Intent detection in `ChatService`

**File:** `app/core/chat_service.py`

```python
REVIEW_TRIGGERS = [
    r"review of my week", r"weekly review", r"how was my week",
    r"what did i do this week", r"week in review", r"\bmy week\b",
]

SECTION_KEYWORDS = {
    "habits": r"habits?",
    "todos": r"to-?dos?|tasks?",
    "sources": r"saved|sources|articles|urls?",
    "facts": r"facts?",
    "topics": r"topics?",
}

def _try_review_intent(self, message: str) -> str | None:
    if not self.review_service:
        return None
    if not any(re.search(p, message, re.IGNORECASE) for p in REVIEW_TRIGGERS) and \
       not re.search(r"how (did|are) my (habits|todos|tasks|sources)", message, re.IGNORECASE):
        return None
    section = None
    for key, pat in SECTION_KEYWORDS.items():
        if re.search(pat, message, re.IGNORECASE):
            section = key
            break
    return self.review_service.generate(section=section)
```

Call `_try_review_intent` after URL/topic intent checks, before normal LLM routing.

**AC:**
- [ ] "give me a review of my week" → full review
- [ ] "how did my habits go this week?" → habits section only, with expanded detail
- [ ] "weekly review" / "how was my week" both trigger
- [ ] False-positive guard: "let me review the code" does NOT trigger (no "my week" / "weekly")

---

### 3.5 — WhatsApp multi-message delivery

**File:** `app/webhook/server.py`

Reviews are typically too long for a single WhatsApp message (1600 char limit). Split the synthesized review into ~3 chunks at section boundaries:

```python
def split_review_for_whatsapp(review: str, limit: int = 1500) -> list[str]:
    # Split on section headers; greedily pack sections into messages under `limit`
    sections = re.split(r"(?=(?:🏋️|✅|🧠|💬|📌|🔮))", review)
    messages, current = [], ""
    for section in sections:
        if len(current) + len(section) > limit and current:
            messages.append(current)
            current = section
        else:
            current += section
    if current:
        messages.append(current)
    return messages
```

In the webhook handler, when the reply is a review (detected by header `📊 Your Week`), send via `whatsapp_service.send_message_chunks(to, messages)`. Add this method to `WhatsAppService` if not already present (Phase 2 of Wave 1 has `split_message`; reuse if compatible).

**AC:**
- [ ] Review split into 2–3 messages, each < 1600 chars
- [ ] Sections are not split mid-section (only between sections)
- [ ] Single short reviews still send as one message

---

### 3.6 — CLI rich rendering

**File:** `app/cli/commands_chat.py`

In CLI, the synthesized review is the body; wrap it with `rich` formatting:

- `rich.Panel` around the full review with title `📊 Your Week`
- For the Habits section, replace the LLM's text representation with a progress-bar table (reuse the existing `/habits` display from `HabitService`)
- Render URLs in the "What You Learned" section as clickable hyperlinks (`rich` supports `[link=...]`)

```python
def render_review_cli(review_text: str, data: WeeklyReviewData) -> None:
    console.print(Panel(review_text, title="📊 Your Week", border_style="cyan"))
    if data.habits:
        console.print(habit_progress_table(data.habits))
```

**AC:**
- [ ] CLI review shows a panel + habit progress bars
- [ ] URLs render as clickable links in supporting terminals
- [ ] Non-CLI consumers (WhatsApp) still get plain text

---

### 3.7 — Settings + env vars

**File:** `app/config/settings.py`

```python
REVIEW_DEFAULT_DAYS: int = 7
REVIEW_WHATSAPP_MAX_WORDS: int = 300
REVIEW_INCLUDE_TOP_TOPICS: bool = True
```

**File:** `.env.example`

```env
REVIEW_DEFAULT_DAYS=7
REVIEW_WHATSAPP_MAX_WORDS=300
REVIEW_INCLUDE_TOP_TOPICS=true
```

**AC:**
- [ ] `REVIEW_INCLUDE_TOP_TOPICS=false` skips the optional LLM topic-extraction call
- [ ] `REVIEW_DEFAULT_DAYS=14` widens the window (verified by test)

---

### 3.8 — Empty-state handling

When the user has zero data in some category — common for new installs:

- Habits empty → omit `🏋️ Habits` section entirely
- Todos all empty → omit
- No URLs ingested → omit `🧠 What You Learned`
- No facts saved → omit `📌 Facts You Saved`
- No sessions → still produce a review with whatever data exists; if literally everything is empty, return: `"Not much to review yet — we haven't built up enough history this week. Try logging some habits or saving an article!"`

**AC:**
- [ ] Empty-everything case returns the friendly "not much to review" message
- [ ] Partial data: review prints only sections with content
- [ ] Section-specific request on empty section: `"how did my habits go?"` with no habits → `"You haven't logged any habits this week."`

---

## Acceptance Criteria (phase complete)

- [ ] "give me a review of my week" + variants trigger the review
- [ ] Review covers: habits, todos, facts, sessions, ingested URLs, followed topics
- [ ] LLM synthesizes data into a narrative, not a raw dump
- [ ] Habits section shows logged days, streak, qualitative note
- [ ] Todos section shows completed / open / overdue counts + notable items
- [ ] Sources section lists URLs ingested in the window (relies on Phase 1)
- [ ] Review ends with one actionable focus for next week
- [ ] WhatsApp delivery splits into 2–3 messages within 1600-char limit
- [ ] CLI delivery uses rich formatting (panel + habit progress bars)
- [ ] Section-specific routing: "how did my habits go this week?" returns only that section
- [ ] Empty data categories handled gracefully (sections omitted, friendly message when fully empty)
