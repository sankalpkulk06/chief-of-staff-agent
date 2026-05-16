# Phase 2 — Proactive News Digest

**Est. effort:** 4–5 days
**Dependencies:** `NewsService`, `WhatsAppService`, APScheduler (all from Wave 1); optionally `URLIngestionService` (Phase 1) for `"save"` reply
**Status:** Not started

---

## Goal

Sage watches user-followed topics and proactively sends a WhatsApp alert **only when the LLM judges something significant**. Topics are added/removed via natural language. Duplicate articles are never sent twice.

---

## New Files

- `app/services/topic_watch_service.py` — follow/unfollow, significance check, dedup
- `app/core/topic_intent.py` — small helper for detecting follow/unfollow phrases (or inline in `chat_service.py`)
- `tests/services/test_topic_watch_service.py` — unit tests with mocked NewsService + LLM

## Modified Files

- `app/storage/sql_schema.sql` — `followed_topics`, `topic_news_sent` tables
- `app/storage/sqlite_registry.py` — CRUD for both tables
- `app/scheduler/scheduler.py` — register `check_followed_topics` job
- `app/core/chat_service.py` — intent routing for follow/unfollow + "any news on my topics?" + reply context
- `app/cli/app.py` — `/following`, `/unfollow` slash commands
- `app/config/settings.py` — `TOPIC_CHECK_INTERVAL_HOURS`, `MAX_FOLLOWED_TOPICS`
- `.env.example` — document new env vars

---

## Tasks

### 2.1 — SQLite schema: `followed_topics` + `topic_news_sent`

**File:** `app/storage/sql_schema.sql`

```sql
CREATE TABLE IF NOT EXISTS followed_topics (
    id              TEXT PRIMARY KEY,
    topic           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_checked_at DATETIME,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_followed_topics_active ON followed_topics(active);

CREATE TABLE IF NOT EXISTS topic_news_sent (
    id           TEXT PRIMARY KEY,
    topic_id     TEXT NOT NULL REFERENCES followed_topics(id) ON DELETE CASCADE,
    article_url  TEXT NOT NULL,
    article_hash TEXT NOT NULL,
    sent_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic_id, article_url)
);

CREATE INDEX IF NOT EXISTS idx_topic_news_sent_url ON topic_news_sent(article_url);
```

**File:** `app/storage/sqlite_registry.py`

```python
def add_followed_topic(self, topic: str) -> str: ...                  # returns topic_id
def deactivate_topic(self, topic: str) -> bool: ...                   # idempotent
def list_active_topics(self) -> list[dict]: ...
def update_topic_checked(self, topic_id: str) -> None: ...
def is_article_sent(self, article_url: str) -> bool: ...              # global dedup
def mark_article_sent(self, topic_id: str, article_url: str) -> None: ...
```

**AC:**
- [ ] Tables auto-created via `initialize_schema()`
- [ ] `add_followed_topic("Tesla")` then `add_followed_topic("tesla")` resolves to same row (case-insensitive)
- [ ] `deactivate_topic("Unknown")` returns `False`, doesn't raise

---

### 2.2 — `TopicWatchService`

**File:** `app/services/topic_watch_service.py`

```python
@dataclass
class FollowedTopic:
    id: str
    topic: str
    created_at: datetime
    last_checked_at: datetime | None
    active: bool

@dataclass
class SignificanceResult:
    significant: bool
    reason: str
    notable_indices: list[int]

class TopicWatchService:
    def __init__(
        self,
        registry: SQLiteRegistry,
        news_service: NewsService,
        chat_provider,                # for LLM significance call
        max_topics: int = 20,
    ): ...

    def follow(self, topic: str) -> FollowedTopic: ...
    def unfollow(self, topic: str) -> bool: ...
    def list_topics(self) -> list[FollowedTopic]: ...

    def check_topic(self, topic: FollowedTopic) -> tuple[SignificanceResult, list[Article]]:
        articles = self.news_service.fetch(topic.topic)
        new_articles = [a for a in articles if not self.registry.is_article_sent(a.url)]
        if not new_articles:
            return SignificanceResult(False, "no new articles", []), []
        result = self.is_significant(topic.topic, new_articles)
        return result, new_articles

    def is_significant(self, topic: str, articles: list[Article]) -> SignificanceResult:
        prompt = SIGNIFICANCE_PROMPT.format(topic=topic, articles=self._format_articles(articles))
        raw = self.chat_provider.generate(prompt, response_format="json")
        data = json.loads(raw)
        return SignificanceResult(
            significant=bool(data.get("significant")),
            reason=data.get("reason", ""),
            notable_indices=data.get("notable_articles", []),
        )

    def mark_sent(self, topic_id: str, article_url: str) -> None: ...
```

**`SIGNIFICANCE_PROMPT` (verbatim from PRD):**

```
You are a news significance filter for a personal assistant.

The user follows the topic: "{topic}"

Here are the latest news articles:
{articles}

Your job: decide if any of these articles represent a SIGNIFICANT development
worth interrupting the user for.

Significant = major product launch, policy change, funding round >$100M,
              acquisition, legal ruling, safety incident, or breakthrough research.

NOT significant = routine earnings, minor updates, opinion pieces,
                  market fluctuations, scheduled events.

Respond in JSON only:
{{
  "significant": true | false,
  "reason": "one sentence explaining why or why not",
  "notable_articles": [list of indices of significant articles, empty if none]
}}
```

**AC:**
- [ ] `follow("Tesla")` enforces `MAX_FOLLOWED_TOPICS` cap (raises `TopicLimitError`)
- [ ] `is_significant()` parses JSON robustly (strips code fences, handles malformed → `significant=False`)
- [ ] `check_topic()` filters out already-sent articles before calling the LLM (no LLM calls when nothing's new)

---

### 2.3 — Scheduler job: `check_followed_topics`

**File:** `app/scheduler/scheduler.py`

Add to the existing scheduler bootstrap:

```python
def register_topic_watch_job(scheduler, topic_watch_service, whatsapp_service, settings):
    if not settings.WHATSAPP_ENABLED:
        return
    scheduler.add_job(
        lambda: _run_topic_check(topic_watch_service, whatsapp_service, settings),
        trigger="interval",
        hours=settings.TOPIC_CHECK_INTERVAL_HOURS,
        id="topic_watch_check",
        replace_existing=True,
    )

def _run_topic_check(topic_watch_service, whatsapp_service, settings):
    for topic in topic_watch_service.list_topics():
        try:
            result, articles = topic_watch_service.check_topic(topic)
            topic_watch_service.registry.update_topic_checked(topic.id)
            if not result.significant:
                continue
            notable = [articles[i] for i in result.notable_indices if 0 <= i < len(articles)]
            msg = _format_alert(topic.topic, notable, result.reason)
            whatsapp_service.send_message(to=settings.OWNER_WHATSAPP_NUMBER, body=msg)
            for article in notable:
                topic_watch_service.mark_sent(topic.id, article.url)
            _store_alert_context(topic, notable)   # for "more"/"save" replies (Task 2.5)
        except Exception:
            logger.exception("topic_check_failed", topic=topic.topic)
```

**Alert format:**

```
🚨 Sage News Alert — {topic}

Something significant: {reason}

[1] {title} — {source}
[2] {title} — {source}

Reply "more" for full summary or "save" to add to your knowledge base.
```

**AC:**
- [ ] Job runs every `TOPIC_CHECK_INTERVAL_HOURS` hours
- [ ] One topic failing doesn't break others (try/except per topic)
- [ ] Single article never sent twice across runs (verified by `is_article_sent`)
- [ ] No WhatsApp message sent when significance is `False`

---

### 2.4 — Intent routing in `ChatService`

**File:** `app/core/chat_service.py`

Add a lightweight pattern matcher that runs before the LLM tool loop:

```python
FOLLOW_PATTERNS = [r"\b(follow|watch|track|monitor|keep an eye on)\b\s+(.+)"]
UNFOLLOW_PATTERNS = [r"\b(unfollow|stop\s+(?:following|watching|tracking))\b\s+(.+)"]
NEWS_CHECK_PATTERNS = [r"any news on my topics", r"check my topics", r"news on my (followed|watched) topics"]

def _try_topic_intent(self, message: str) -> str | None:
    if not self.topic_watch_service:
        return None
    for pat in UNFOLLOW_PATTERNS:
        if m := re.search(pat, message, re.IGNORECASE):
            topic = m.group(2).strip(" .?!")
            return self._handle_unfollow(topic)
    for pat in FOLLOW_PATTERNS:
        if m := re.search(pat, message, re.IGNORECASE):
            topic = m.group(2).strip(" .?!")
            return self._handle_follow(topic)
    for pat in NEWS_CHECK_PATTERNS:
        if re.search(pat, message, re.IGNORECASE):
            return self._handle_on_demand_check()
    return None
```

Call `_try_topic_intent` after the URL check (Phase 1) and before LLM routing.

**Multi-topic handling:**
"follow AI regulation and climate tech" → split on `\s+and\s+|,\s*` after the verb, follow each.

**`_handle_on_demand_check`:** Iterate all active topics, run `check_topic()` synchronously, return a per-topic summary line:

```
Checked Tesla, AI regulation, climate tech.
  • Tesla — nothing major since yesterday
  • AI regulation — EU passed new model transparency rules (sending details)
  • Climate tech — nothing major
```

For the topics with significant news, also send full alerts via WhatsApp (in WhatsApp context) or print in CLI.

**AC:**
- [ ] "follow Tesla" → adds topic, replies confirmation
- [ ] "follow Tesla and AI regulation" → adds both, lists all active topics
- [ ] "stop following Tesla" → deactivates
- [ ] "any news on my topics?" → triggers on-demand check
- [ ] "follow up on this" (false-positive guard) does NOT match — pattern requires word boundary + topic argument

---

### 2.5 — Reply context: `"more"` and `"save"` handlers

**Goal:** When user replies `more` or `save` to a recent alert, act on the alerted articles.

**Implementation:** Lightweight in-memory cache keyed by phone number with TTL (15 min). If reply is `more` or `save` (case-insensitive, exact match) and a cached alert exists → handle. Otherwise fall through to normal routing.

**File:** `app/services/topic_watch_service.py`

```python
@dataclass
class _AlertContext:
    topic: str
    articles: list[Article]
    expires_at: datetime

class AlertContextStore:
    def __init__(self, ttl_minutes: int = 15): ...
    def store(self, key: str, topic: str, articles: list[Article]) -> None: ...
    def get(self, key: str) -> _AlertContext | None: ...   # returns None if expired
    def clear(self, key: str) -> None: ...
```

**File:** `app/core/chat_service.py`

```python
def _try_alert_reply(self, message: str, session_key: str) -> str | None:
    if not self.alert_context_store:
        return None
    ctx = self.alert_context_store.get(session_key)
    if not ctx:
        return None
    cmd = message.strip().lower()
    if cmd == "more":
        summary = self._llm_summarize_articles(ctx.articles)
        self.alert_context_store.clear(session_key)
        return summary
    if cmd == "save" and self.url_ingestion_service:
        results = [self.url_ingestion_service.ingest(a.url) for a in ctx.articles]
        self.alert_context_store.clear(session_key)
        return self._format_save_results(results)
    return None
```

For CLI, `session_key` is a constant (`"cli"`). For WhatsApp, it's the phone number.

**AC:**
- [ ] "more" within 15 min returns LLM-generated full summary of alerted articles
- [ ] "save" within 15 min ingests all alert articles via Phase 1's `URLIngestionService`
- [ ] After 15 min the cache expires; reply falls through to normal routing
- [ ] After "more" or "save" is consumed, the cache entry is cleared (one-shot)

---

### 2.6 — `/following` and `/unfollow` slash commands

**File:** `app/cli/app.py` (and `ChatService` slash-command dispatcher for `sage chat`)

```
/following            → list active topics
/unfollow <topic>     → deactivate topic
```

`/following` from WhatsApp also works (handled in `ChatService` slash dispatcher).

**AC:**
- [ ] `/following` lists topics + creation dates
- [ ] `/unfollow Tesla` removes; subsequent `/following` doesn't show it
- [ ] Unknown topic in `/unfollow` returns "not currently following X"

---

### 2.7 — Settings + env vars

**File:** `app/config/settings.py`

```python
TOPIC_CHECK_INTERVAL_HOURS: int = 6
MAX_FOLLOWED_TOPICS: int = 20
OWNER_WHATSAPP_NUMBER: str = ""   # used by scheduler to know whom to alert
```

**File:** `.env.example`

```env
TOPIC_CHECK_INTERVAL_HOURS=6
MAX_FOLLOWED_TOPICS=20
OWNER_WHATSAPP_NUMBER=whatsapp:+14155551234
```

**AC:**
- [ ] Scheduler doesn't register the job if `OWNER_WHATSAPP_NUMBER` is empty (logs a warning)
- [ ] `MAX_FOLLOWED_TOPICS` is enforced at follow time

---

## Acceptance Criteria (phase complete)

- [ ] "follow X" / "watch X" detected and routed to `TopicWatchService`
- [ ] "unfollow X" / "stop following X" deactivates the topic
- [ ] `/following` lists active topics
- [ ] Scheduler runs every N hours, calls LLM significance check on new articles only
- [ ] WhatsApp alert sent only when LLM judges significant
- [ ] Same article never sent twice (`topic_news_sent` dedup)
- [ ] "any news on my topics?" runs on-demand check
- [ ] "more" reply returns full LLM summary; "save" ingests via `URLIngestionService`
- [ ] No "nothing to report" spam when nothing significant
- [ ] One topic erroring out doesn't crash the scheduler job
