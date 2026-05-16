# Sage — Feature PRD v2
### Proactive News Digest · URL Ingestion · Weekly Review

**Status:** Planned  
**Author:** Sankalp  
**Last Updated:** April 2026  
**Builds on:** PRD v1 (Web Search · WhatsApp · Habit Tracker)

---

## Table of Contents

1. [Overview](#overview)
2. [Feature 1 — Proactive News Digest](#feature-1--proactive-news-digest)
3. [Feature 2 — URL & Article Ingestion](#feature-2--url--article-ingestion)
4. [Feature 3 — Weekly Review](#feature-3--weekly-review)
5. [Cross-Feature Dependencies](#cross-feature-dependencies)
6. [Build Order](#build-order)
7. [Out of Scope](#out-of-scope)

---

## Overview

These three features push Sage further toward being a **genuinely useful daily agent** rather than a tool you have to remember to use:

- **Proactive News Digest** — Sage watches topics you care about and messages you on WhatsApp only when something significant happens, AI-judged
- **URL Ingestion** — paste any link in WhatsApp or CLI and Sage scrapes, chunks, and stores it in RAG so you can ask questions on it later
- **Weekly Review** — ask Sage for a review of your week and get a synthesized summary across habits, todos, facts, and conversations — via CLI or WhatsApp

All three are natural language-first: no explicit commands required.

---

## Feature 1 — Proactive News Digest

### Goal

Let Sage watch topics you care about and proactively message you on WhatsApp **only when something significant happens** — using the LLM to judge significance, not just a keyword match.

### Problem

Right now Sage fetches news reactively — you have to ask. A personal agent should surface important developments on topics you care about without you having to remember to check.

The key design challenge: most news on any topic is noise. A naive daily digest of "here are 5 articles about AI" is annoying. Sage should message you only when it judges the news to be genuinely worth your attention.

### Solution

A background scheduler job (extending the existing APScheduler setup) that:
1. Fetches news on your followed topics at a configured interval
2. Passes the articles to the LLM with a significance prompt
3. Sends a WhatsApp message only if the LLM judges something significant
4. Tracks what it has already sent to avoid duplicates

### User Stories

- As a user, I can tell Sage "follow Tesla for me" and it will watch that topic
- As a user, I only get messaged when something genuinely significant happens — not for routine updates
- As a user, I can see all topics I'm following with `/following`
- As a user, I can unfollow a topic with "stop following Tesla" or `/unfollow Tesla`
- As a user, I can ask "any news on my topics?" and get an on-demand check

### Interaction Design

**Following a topic (natural language):**
```
you: follow Tesla for me
Sage: Got it — I'll watch Tesla and message you when something significant happens.

you: also follow AI regulation and climate tech
Sage: Added! You're now following: Tesla, AI regulation, climate tech.
```

**Proactive WhatsApp message (only when significant):**
```
🚨 Sage News Alert — Tesla

Something significant: Tesla announced full autonomy rollout in 10 US cities,
marking the first commercial robotaxi deployment in the US.

[1] Tesla launches robotaxi service — Reuters
[2] Full self-driving approved by NHTSA — The Verge

Reply "more" for full summary or "save" to add to your knowledge base.
```

**No significant news (silence):**
Sage checks, finds nothing worth alerting, sends nothing. No "nothing to report" messages.

**On-demand check:**
```
you: any news on my topics?
Sage: Checked Tesla, AI regulation, climate tech.
      • Tesla — nothing major since yesterday
      • AI regulation — EU passed new model transparency rules (sending details)
      • Climate tech — nothing major
```

### Technical Design

**New file:** `sage/services/topic_watch_service.py`  
**Extended:** `sage/scheduler/scheduler.py`  
**Extended:** SQLite schema

**Database schema:**

```sql
CREATE TABLE followed_topics (
    id              TEXT PRIMARY KEY,
    topic           TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_checked_at DATETIME,
    active          BOOLEAN DEFAULT 1
);

CREATE TABLE topic_news_sent (
    id          TEXT PRIMARY KEY,
    topic_id    TEXT REFERENCES followed_topics(id),
    article_url TEXT NOT NULL,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- Used to avoid sending the same article twice
```

**TopicWatchService API:**

```python
class TopicWatchService:
    def follow(self, topic: str) -> FollowedTopic
    def unfollow(self, topic: str) -> bool
    def list_topics(self) -> list[FollowedTopic]
    def check_topic(self, topic: FollowedTopic) -> list[SignificantArticle]
    def is_significant(self, articles: list[Article]) -> tuple[bool, str]  # (significant, reason)
    def already_sent(self, article_url: str) -> bool
    def mark_sent(self, topic_id: str, article_url: str) -> None
```

**Significance judgment prompt:**

```python
SIGNIFICANCE_PROMPT = """
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
"""
```

**Scheduler job:**

```python
# Runs every 6 hours (configurable)
scheduler.add_job(check_followed_topics, 'interval', hours=6)

def check_followed_topics():
    topics = topic_watch_service.list_topics()
    for topic in topics:
        articles = news_service.fetch(topic.topic)
        new_articles = [a for a in articles if not topic_watch_service.already_sent(a.url)]
        if not new_articles:
            continue
        significant, reason = topic_watch_service.is_significant(new_articles)
        if significant:
            msg = format_alert(topic, new_articles, reason)
            whatsapp_service.send_message(YOUR_NUMBER, msg)
            for article in new_articles:
                topic_watch_service.mark_sent(topic.topic_id, article.url)
```

**Intent detection (in ChatService):**

No new command needed. Detect follow/unfollow intent from natural language:

```python
FOLLOW_PATTERNS = ["follow", "watch", "track", "keep an eye on", "monitor"]
UNFOLLOW_PATTERNS = ["unfollow", "stop following", "stop watching", "remove"]
```

If intent detected → route to `TopicWatchService` instead of `ChatService`.

**Reply handling ("more" / "save"):**

When a news alert is sent, store the articles in a short-lived context keyed to the WhatsApp session. If the user replies:
- `"more"` → send full AI-generated summary of the articles
- `"save"` → ingest all article URLs into RAG (uses Feature 2 below)
- Anything else → falls through to normal `ChatService`

### Environment Variables

```env
TOPIC_CHECK_INTERVAL_HOURS=6       # How often to check followed topics
TOPIC_SIGNIFICANCE_THRESHOLD=true  # Always use AI judgment (no bypass)
MAX_FOLLOWED_TOPICS=20
```

### Acceptance Criteria

- [ ] "follow X" and "watch X" detected and routed to `TopicWatchService`
- [ ] "unfollow X" and "stop following X" remove the topic
- [ ] `/following` lists all active followed topics
- [ ] Scheduler checks topics every N hours (configurable)
- [ ] LLM significance check runs on new articles only
- [ ] WhatsApp alert sent only when LLM judges something significant
- [ ] Same article never sent twice (`topic_news_sent` dedup)
- [ ] "any news on my topics?" triggers an on-demand check with summary
- [ ] Reply "more" sends full summary of the alert articles
- [ ] Reply "save" ingests the articles into RAG
- [ ] Silence (no alert) when nothing significant — no "nothing to report" spam

---

## Feature 2 — URL & Article Ingestion

### Goal

When you paste a URL — in WhatsApp or CLI — Sage automatically scrapes the page, chunks it, stores it in RAG, and confirms it's ready to query. No explicit command needed.

### Problem

Currently you can only ingest local files (`sage ingest --path ./docs`). Your knowledge base can only grow when you're at your laptop. Most interesting content you encounter is on the web, discovered on your phone.

### Solution

URL detection in the message handler. When Sage sees a URL (with or without "remember this"), it:
1. Scrapes the page content
2. Cleans and extracts meaningful text
3. Chunks and embeds into ChromaDB
4. Confirms with a brief summary of what was saved

### User Stories

- As a user, I can paste a URL in WhatsApp and Sage automatically saves it to my knowledge base
- As a user, I can say "remember this" + URL and Sage saves and confirms
- As a user, I can later ask questions about saved articles as if they were local documents
- As a user, I can see all ingested URLs with `/sources` or "what have you saved?"
- As a user, sources from URLs are cited distinctly from local documents in answers
- As a user, if a page can't be scraped, Sage tells me clearly instead of failing silently

### Interaction Design

**Paste a URL (WhatsApp or CLI):**
```
you: https://lilianweng.github.io/posts/2023-06-23-agent/

Sage: 📥 Saved to your knowledge base!
      Title: LLM Powered Autonomous Agents — Lil'Log
      Summary: Covers planning, memory, and tool use in LLM agents.
               Includes ReAct, Reflexion, and Chain of Thought frameworks.
      You can now ask me questions about it.
```

**With natural language:**
```
you: remember this https://arxiv.org/abs/2305.10601

Sage: 📥 Saved!
      Title: Tree of Thoughts: Deliberate Problem Solving with LLMs
      Summary: Introduces a framework for LLM reasoning using tree search...
```

**Asking questions later:**
```
you: what does the agent planning paper say about ReAct?

Sage: According to the LLM Powered Autonomous Agents article [1], ReAct combines...

sources:
- [1] LLM Powered Autonomous Agents — lilianweng.github.io (saved Apr 30)
```

**Listing saved URLs:**
```
you: what have you saved?

Sage: 📚 Your saved sources (5):
      [1] LLM Powered Autonomous Agents — lilianweng.github.io
      [2] Tree of Thoughts paper — arxiv.org
      [3] Tesla robotaxi article — reuters.com
      [4] The Illustrated Transformer — jalammar.github.io
      [5] your-local-doc.md (local file)
```

### Technical Design

**New file:** `sage/services/url_ingestion_service.py`  
**Extended:** `sage/services/ingestion_service.py`  
**Extended:** SQLite schema

**Database schema (extend existing documents table):**

```sql
-- Add columns to existing documents table
ALTER TABLE documents ADD COLUMN source_type TEXT DEFAULT 'local';  -- 'local' | 'url'
ALTER TABLE documents ADD COLUMN source_url  TEXT;
ALTER TABLE documents ADD COLUMN ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP;
```

**URLIngestionService API:**

```python
class URLIngestionService:
    def is_url(self, text: str) -> bool                          # detect URLs in message
    def extract_url(self, text: str) -> str | None               # pull URL from message
    def scrape(self, url: str) -> ScrapedPage                    # fetch + clean content
    def ingest(self, url: str) -> IngestionResult                # scrape + chunk + embed
    def already_ingested(self, url: str) -> bool                 # dedup check
    def list_url_sources(self) -> list[SourceSummary]            # for /sources command
```

**ScrapedPage schema:**

```python
@dataclass
class ScrapedPage:
    url: str
    title: str
    content: str          # cleaned main text
    word_count: int
    scraped_at: datetime
```

**Scraping stack:**

```python
# Primary: httpx + BeautifulSoup
# For JS-heavy pages: playwright (optional, heavier)
# Cleaning: extract <article>, <main>, <p> tags; strip nav/footer/ads

def scrape(self, url: str) -> ScrapedPage:
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Remove noise
    for tag in soup(["nav", "footer", "header", "script", "style", "aside"]):
        tag.decompose()
    
    # Extract meaningful content
    main = soup.find("article") or soup.find("main") or soup.find("body")
    title = soup.find("title").text.strip()
    content = main.get_text(separator="\n", strip=True)
    
    return ScrapedPage(url=url, title=title, content=content, ...)
```

**Chunking and embedding:**

Reuse the existing `IngestionService` chunking logic (same `CHUNK_SIZE`, `CHUNK_OVERLAP` from `.env`). Store chunks in ChromaDB with metadata:

```python
metadata = {
    "source_type": "url",
    "source_url": url,
    "title": page.title,
    "ingested_at": datetime.now().isoformat()
}
```

**URL detection in message handler:**

```python
URL_REGEX = r'https?://[^\s]+'

def handle_message(self, message: str) -> str:
    url = url_ingestion_service.extract_url(message)
    if url:
        if url_ingestion_service.already_ingested(url):
            return f"I already have that saved. Ask me anything about it!"
        result = url_ingestion_service.ingest(url)
        return format_ingestion_confirmation(result)
    # else: normal ChatService routing
```

**AI-generated summary on confirmation:**

After ingestion, pass the first 500 words to the LLM for a 2-sentence summary to include in the confirmation message. Makes the response feel intelligent rather than mechanical.

**Error handling:**

| Error | Response |
|-------|----------|
| Page not found (404) | "Couldn't access that page — it may be behind a login or no longer exists." |
| Timeout | "The page took too long to load. Try again or paste the article text directly." |
| Too little content (<100 words) | "That page doesn't have much readable content — it might be a login wall or redirect." |
| Already ingested | "Already saved that one! Ask me anything about it." |

**Citation format (distinct from local docs):**

```
sources:
- [1] The Illustrated Transformer — jalammar.github.io  🌐 (saved Apr 28)
- [2] my-notes.md  📄 (local)
```

### Environment Variables

```env
URL_INGESTION_ENABLED=true
URL_SCRAPE_TIMEOUT=10              # seconds
URL_MIN_CONTENT_WORDS=100          # reject pages with less content
URL_MAX_CONTENT_WORDS=50000        # truncate very long pages
```

### Acceptance Criteria

- [ ] Bare URL in message (WhatsApp or CLI) triggers automatic ingestion
- [ ] "remember this <url>" and "save this <url>" also trigger ingestion
- [ ] Page scraped, cleaned, chunked, and stored in ChromaDB
- [ ] Confirmation message includes title + 2-sentence AI summary
- [ ] Saved URLs queryable via RAG — cited with title + domain + save date
- [ ] URL sources visually distinct from local files in citations
- [ ] Duplicate URLs detected and skipped with friendly message
- [ ] `/sources` or "what have you saved?" lists all URL and local sources
- [ ] Graceful error messages for 404, timeout, login walls
- [ ] Works identically in WhatsApp and CLI

---

## Feature 3 — Weekly Review

### Goal

When you ask for a review of your week, Sage synthesizes data from across all its systems — habits, todos, facts, conversations, saved URLs — into a single coherent summary, delivered via WhatsApp or CLI.

### Problem

Sage accumulates a lot of data about you across sessions but never reflects it back in a meaningful way. A weekly review turns raw logs into insight — helping you notice patterns, celebrate progress, and plan ahead — without you having to dig through history manually.

### Solution

A `ReviewService` that queries all existing data stores (SQLite habits, todos, sessions, facts; ChromaDB for saved sources) for the past 7 days, passes structured data to the LLM for synthesis, and returns a clean narrative summary.

Triggered on demand — when you ask for it via WhatsApp or CLI. No scheduled push.

### User Stories

- As a user, I can say "give me a review of my week" and get a full summary
- As a user, the review covers: habits, todos, facts I saved, topics I explored, URLs I ingested
- As a user, I get the review on WhatsApp if I ask there, or in the terminal if I ask via CLI
- As a user, the review feels like a thoughtful reflection, not a raw data dump
- As a user, I can ask for a specific section: "how did my habits go this week?"

### Interaction Design

**Triggering (natural language — no command needed):**
```
you: give me a review of my week
you: how was my week?
you: weekly review
you: what did I do this week?
```

**Full review output:**

```
📊 Your Week — Apr 24 to Apr 30, 2026

🏋️ Habits
  gym          5/7 days  🔥 5-day streak — your best this month
  reading      3/7 days  — missed 2 days mid-week
  meditation   2/7 days  — room to improve here

✅ Todos
  Completed: 6  |  Still open: 3  |  Overdue: 1
  Notable: Finished "Email tax documents" and "Call dentist"
  Still open: "Review job applications" (added Monday)

🧠 What You Learned
  Saved 3 new sources to your knowledge base:
  • LLM Powered Autonomous Agents — lilianweng.github.io
  • Tree of Thoughts paper — arxiv.org
  • Tesla robotaxi article — reuters.com

💬 Topics You Explored
  Most asked about: AI agents, job applications, habit building
  Sessions this week: 12  |  Total turns: 47

📌 Facts You Saved
  • Started following: Tesla, AI regulation
  • New personal fact: "Interview at Anthropic on May 5"

🔮 One Thing to Focus on Next Week
  Meditation has been inconsistent — consider setting a specific time for it.
  You have 3 open todos worth clearing before the weekend.
```

**Section-specific:**
```
you: how did my habits go this week?
Sage: [returns only the habits section with more detail]
```

### Technical Design

**New file:** `sage/services/review_service.py`

**ReviewService API:**

```python
class ReviewService:
    def generate(self, days: int = 7) -> WeeklyReview
    def get_habit_summary(self, since: datetime) -> HabitReviewData
    def get_todo_summary(self, since: datetime) -> TodoReviewData
    def get_session_summary(self, since: datetime) -> SessionReviewData
    def get_ingested_sources(self, since: datetime) -> list[SourceSummary]
    def get_saved_facts(self, since: datetime) -> list[Fact]
    def synthesize(self, data: WeeklyReviewData) -> str   # LLM call
```

**Data collection (all from existing stores — no new APIs):**

```python
@dataclass
class WeeklyReviewData:
    period_start: datetime
    period_end: datetime
    habits: list[HabitSummary]          # from habit_logs table
    todos_completed: list[Todo]          # from todos where completed_at >= period_start
    todos_open: list[Todo]               # from todos where completed_at IS NULL
    todos_overdue: list[Todo]            # open + due_date < now
    facts_saved: list[Fact]              # from facts where created_at >= period_start
    topics_followed: list[str]           # from followed_topics where created_at >= period_start
    sessions: list[Session]              # from sessions where created_at >= period_start
    total_turns: int                     # sum of turns across sessions
    ingested_urls: list[SourceSummary]   # from documents where source_type='url'
    top_topics: list[str]                # extracted from session messages via LLM
```

**LLM synthesis prompt:**

```python
REVIEW_PROMPT = """
You are Sage, a personal AI assistant generating a weekly review for your user.

Here is the data from their past 7 days:
{structured_data_as_json}

Write a warm, concise weekly review that:
- Celebrates wins (streaks, completed todos)
- Honestly notes areas for improvement (missed habits, overdue todos)
- Summarizes what they learned and explored
- Ends with one concrete, actionable focus for next week

Tone: like a thoughtful coach, not a robot reading a spreadsheet.
Format: use the section headers provided. Keep total length under 300 words for WhatsApp.
"""
```

**WhatsApp length handling:**

The review is split into sections and sent as 2-3 sequential WhatsApp messages to stay under the 1600 character limit:
- Message 1: Habits + Todos
- Message 2: What You Learned + Topics Explored
- Message 3: Facts Saved + One Focus for Next Week

**Intent detection (in ChatService):**

```python
REVIEW_TRIGGERS = [
    "review of my week", "weekly review", "how was my week",
    "what did i do this week", "week in review", "my week"
]

def route_message(self, message: str) -> str:
    if any(trigger in message.lower() for trigger in REVIEW_TRIGGERS):
        return review_service.generate()
    # else: normal routing
```

**Section-specific routing:**

If the message contains a review trigger + a specific domain keyword ("habits", "todos", "what I saved"), return only that section with expanded detail instead of the full review.

**CLI output:**

In CLI, the full review renders with richer formatting — progress bars for habits (reusing existing `/habits` display), full todo list, clickable URLs for sources.

### Environment Variables

```env
REVIEW_DEFAULT_DAYS=7              # lookback window
REVIEW_WHATSAPP_MAX_WORDS=300      # keep it concise for mobile
REVIEW_INCLUDE_TOP_TOPICS=true     # LLM-extracted topic summary from sessions
```

### Acceptance Criteria

- [ ] "give me a review of my week" and natural variants trigger the review
- [ ] Review covers: habits, todos, facts, sessions, ingested URLs, followed topics
- [ ] LLM synthesizes data into a narrative — not a raw data dump
- [ ] Habit section shows logged days, streak, and a qualitative note
- [ ] Todo section shows completed vs. open vs. overdue counts + notable items
- [ ] Sources section lists URLs ingested this week
- [ ] Review ends with one actionable focus for next week
- [ ] WhatsApp delivery splits into 2-3 messages to respect character limit
- [ ] CLI delivery uses richer formatting (progress bars, full lists)
- [ ] "how did my habits go this week?" returns section-specific detail
- [ ] Works correctly when some data categories are empty (e.g. no URLs saved)

---

## Cross-Feature Dependencies

| Feature | Depends On |
|---------|-----------|
| Proactive News Digest | `NewsService`, `APScheduler`, `WhatsAppService` (all from PRD v1) |
| URL Ingestion | `IngestionService`, `ChromaDB`, existing message handler |
| Weekly Review | All data stores: habits, todos, facts, sessions, ingested URLs |
| "save" reply on news alert | URL Ingestion (Feature 2 of this PRD) |
| Review — ingested sources section | URL Ingestion (Feature 2 of this PRD) |

**Recommended build order within this PRD:** URL Ingestion → Proactive News → Weekly Review

URL Ingestion is fully independent. Proactive News can use "save" → URL Ingestion once both exist. Weekly Review is richest when URL Ingestion data is already being collected.

---

## Build Order

### Phase 1 — URL Ingestion *(~3-4 days)*
1. Build `URLIngestionService` with `httpx` + `BeautifulSoup`
2. Add URL detection to message handler (WhatsApp + CLI)
3. Extend SQLite documents table with `source_type`, `source_url`
4. Store chunks in ChromaDB with URL metadata
5. Update citation format to distinguish URL vs. local sources
6. Add `/sources` command

### Phase 2 — Proactive News Digest *(~4-5 days)*
1. Build `TopicWatchService` with follow/unfollow logic
2. Add `followed_topics` and `topic_news_sent` tables to SQLite
3. Add intent detection for "follow X" / "unfollow X" in `ChatService`
4. Write significance judgment prompt + LLM call
5. Add scheduler job for periodic topic checks
6. Wire up WhatsApp alert with "more" / "save" reply handling

### Phase 3 — Weekly Review *(~3-4 days)*
1. Build `ReviewService` with data collection methods
2. Write synthesis prompt + LLM call
3. Add intent detection for review triggers in `ChatService`
4. Handle WhatsApp multi-message splitting
5. Add section-specific routing ("how did my habits go?")
6. Test with empty data categories

**Total estimated effort:** 2-3 weeks of part-time building

---

## Out of Scope

- Scheduled weekly review push (review is on-demand only — you ask for it)
- Email digest of the weekly review
- Review for custom date ranges beyond 7 days (future enhancement)
- Scraping JavaScript-heavy pages (Playwright support is optional/future)
- Podcast or YouTube transcript ingestion (separate future PRD)
- Multi-user topic following