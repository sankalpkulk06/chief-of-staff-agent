# Feature Build Plan — URL Ingestion · Proactive News Digest · Weekly Review

**Status:** Planned  
**PRD:** [docs/prd/WAVE_2.md](../../prd/WAVE_2.md)  
**Builds on:** Wave 1 (Web Search · WhatsApp · Habit Tracker · Scheduler)

---

## Summary

Three features that push Sage toward being a **genuinely useful daily agent** — one that saves what you read, watches topics you care about, and reflects your week back to you.

| Phase | Feature | Est. Effort | Status |
|-------|---------|-------------|--------|
| 1 | [URL & Article Ingestion](phase1-url-ingestion.md) | 3–4 days | Not started |
| 2 | [Proactive News Digest](phase2-proactive-news.md) | 4–5 days | Not started |
| 3 | [Weekly Review](phase3-weekly-review.md) | 3–4 days | Not started |

**Total estimated:** 2–3 weeks part-time

---

## Architecture at a Glance

```
app/
  services/
    url_ingestion_service.py    ← Phase 1 (new)
    topic_watch_service.py      ← Phase 2 (new)
    review_service.py           ← Phase 3 (new)
  ingestion/
    ingest_service.py           ← Phase 1 (extended — reuse chunking logic)
  storage/
    sql_schema.sql              ← Phase 1 + 2 (schema additions)
  scheduler/
    scheduler.py                ← Phase 2 (add topic check job)
  core/
    chat_service.py             ← Phase 1 + 2 + 3 (extend intent routing)
  cli/
    app.py                      ← Phase 1 + 2 (add /sources, /following, /unfollow)
```

---

## Dependency Graph

```
Phase 1 — URL Ingestion         (no deps — start here)
Phase 2 — Proactive News        (needs: NewsService + APScheduler from Wave 1)
                                 (optionally uses Phase 1 for "save" reply)
Phase 3 — Weekly Review         (richest after Phase 1 data is being collected;
                                  reads all existing stores — habits, todos, facts, sessions)
```

Build Phase 1 first. Phase 2 and 3 can proceed in parallel after Phase 1 is done.

---

## Phase 1 — URL & Article Ingestion

**New file:** `app/services/url_ingestion_service.py`  
**Extended:** `app/ingestion/ingest_service.py` (reuse `CHUNK_SIZE`, `CHUNK_OVERLAP`, ChromaDB write path)  
**Extended:** `app/storage/sql_schema.sql` — add columns to `documents` table  
**Extended:** `app/core/chat_service.py` — URL detection before normal routing  
**Extended:** `app/cli/app.py` — `/sources` command  

**Schema changes:**
```sql
ALTER TABLE documents ADD COLUMN source_type TEXT DEFAULT 'local';
ALTER TABLE documents ADD COLUMN source_url  TEXT;
ALTER TABLE documents ADD COLUMN ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP;
```

**Key implementation steps:**
1. `URLIngestionService`: `is_url()`, `extract_url()`, `scrape()` (httpx + BeautifulSoup), `ingest()`, `already_ingested()`, `list_url_sources()`
2. Scraper: strip nav/footer/header/script; prefer `<article>` or `<main>`; extract title from `<title>`
3. Reuse `IngestService` chunking — pass chunks to ChromaDB with metadata `{source_type: "url", source_url, title, ingested_at}`
4. After ingestion: pass first 500 words to LLM for a 2-sentence summary in the confirmation reply
5. In `ChatService.handle_message`: detect URL regex `r'https?://[^\s]+'` before all other routing
6. Dedup: `already_ingested(url)` checks `documents` table by `source_url`
7. Citation format: `🌐` prefix for URL sources, `📄` for local — applied in `app/retrieval/prompt_builder.py`
8. `/sources` CLI command lists all ingested documents grouped by type

**New dependencies:** `httpx`, `beautifulsoup4` (add to `requirements.txt`)

**Error handling table:**

| Condition | Message |
|-----------|---------|
| 404 / connection error | "Couldn't access that page — it may be behind a login or no longer exists." |
| Timeout (>10s) | "The page took too long to load. Try again or paste the text directly." |
| Content < 100 words | "That page doesn't have much readable content — it might be a login wall." |
| Already ingested | "Already saved that one! Ask me anything about it." |

**Env vars to add to `.env.example`:**
```
URL_INGESTION_ENABLED=true
URL_SCRAPE_TIMEOUT=10
URL_MIN_CONTENT_WORDS=100
URL_MAX_CONTENT_WORDS=50000
```

---

## Phase 2 — Proactive News Digest

**New file:** `app/services/topic_watch_service.py`  
**Extended:** `app/scheduler/scheduler.py` — add `check_followed_topics` job (every 6h)  
**Extended:** `app/storage/sql_schema.sql` — two new tables  
**Extended:** `app/core/chat_service.py` — intent routing for follow/unfollow  
**Extended:** `app/cli/app.py` — `/following`, `/unfollow` commands  

**Schema additions:**
```sql
CREATE TABLE IF NOT EXISTS followed_topics (
    id              TEXT PRIMARY KEY,
    topic           TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_checked_at DATETIME,
    active          BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS topic_news_sent (
    id          TEXT PRIMARY KEY,
    topic_id    TEXT REFERENCES followed_topics(id),
    article_url TEXT NOT NULL,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Key implementation steps:**
1. `TopicWatchService`: `follow()`, `unfollow()`, `list_topics()`, `check_topic()`, `is_significant()`, `already_sent()`, `mark_sent()`
2. Intent detection in `ChatService`: keyword patterns for follow (`"follow"`, `"watch"`, `"track"`) and unfollow — route to `TopicWatchService` before LLM
3. Significance judgment: LLM call with `SIGNIFICANCE_PROMPT` → JSON response `{significant, reason, notable_articles}`
4. Scheduler job `check_followed_topics()`: fetch news → filter already-sent → LLM significance check → WhatsApp alert if significant
5. WhatsApp alert format: `🚨 Sage News Alert — {topic}` + summary + article list
6. Short-lived reply context: store sent articles keyed to WhatsApp sender number; handle `"more"` (full summary) and `"save"` (invoke `URLIngestionService` on each article URL)
7. On-demand check: "any news on my topics?" → check all topics synchronously → return per-topic summary

**Env vars:**
```
TOPIC_CHECK_INTERVAL_HOURS=6
MAX_FOLLOWED_TOPICS=20
```

---

## Phase 3 — Weekly Review

**New file:** `app/services/review_service.py`  
**Extended:** `app/core/chat_service.py` — REVIEW_TRIGGERS intent routing  
**Extended:** `app/webhook/server.py` — multi-message WhatsApp split  

**Key implementation steps:**
1. `ReviewService.generate(days=7)` — collect data from all existing stores:
   - `SQLiteRegistry`: habit logs, todos (completed/open/overdue), facts, sessions, turn counts
   - ChromaDB metadata: documents with `source_type='url'` and `ingested_at >= period_start`
   - `followed_topics`: topics added during the period
2. `synthesize(data)` — single LLM call with `REVIEW_PROMPT` → narrative string
3. "Top topics from sessions": pass session message text to LLM in a lightweight extraction call (or skip if no sessions)
4. Intent routing in `ChatService`: match `REVIEW_TRIGGERS` list; if trigger + domain keyword ("habits", "todos", "saved") → return only that section
5. WhatsApp split: split output into 3 messages (Habits+Todos / Learning+Topics / Facts+Focus) to stay under 1600 chars
6. CLI output: richer formatting with `rich` progress bars for habits (reuse existing `/habits` display from `HabitService`)
7. Handle empty data categories gracefully — omit sections with no data rather than showing empty headers

**Env vars:**
```
REVIEW_DEFAULT_DAYS=7
REVIEW_WHATSAPP_MAX_WORDS=300
REVIEW_INCLUDE_TOP_TOPICS=true
```

---

## Key Design Notes

- The PRD uses `sage/` as the package name; the actual package is `app/`. All paths here follow `app/`.
- `SQLiteRegistry.initialize_schema()` runs `app/storage/sql_schema.sql` at startup — new tables and `ALTER TABLE` statements go there, wrapped in `IF NOT EXISTS` / guarded with `IF NOT EXISTS` column checks to be idempotent.
- `ALTER TABLE` in SQLite cannot use `IF NOT EXISTS` — guard with a `PRAGMA table_info(documents)` check in `SQLiteRegistry.initialize_schema()` or use a migration helper.
- New services (`URLIngestionService`, `TopicWatchService`, `ReviewService`) follow the existing injection pattern — passed into `ChatService.__init__` as optional dependencies.
- `NewsService` already exists in `app/services/news_service.py` — `TopicWatchService` should use it for fetching, not reimplement news fetching.
- ChromaDB metadata filtering: add `source_type` to chunk metadata in Phase 1 so Phase 3 can query `where={"source_type": "url"}` for the ingested sources section.

---

## Verification Checklist

**Phase 1 — URL Ingestion:**
- `sage chat` → paste a bare URL → confirm `📥 Saved!` with title + summary
- Ask a question about the saved URL content → RAG returns a cited answer with `🌐` prefix
- Paste the same URL again → "Already saved that one!"
- `/sources` → lists all ingested documents grouped by type
- Test 404 URL, login-wall URL, very short page

**Phase 2 — Proactive News:**
- "follow Tesla" → `GET /api/topics` shows Tesla active
- `/following` lists Tesla
- Trigger `check_followed_topics()` manually → verify LLM significance check fires
- Mock a significant article → WhatsApp message sent; second run with same article → no duplicate
- "unfollow Tesla" → topic deactivated
- "any news on my topics?" → on-demand per-topic summary

**Phase 3 — Weekly Review:**
- "give me a review of my week" → full review returned with all populated sections
- "how did my habits go this week?" → habits-only section with detail
- WhatsApp delivery → 3 sequential messages, each under 1600 chars
- Run with empty habits or no URLs saved → empty sections omitted cleanly
