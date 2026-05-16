# Sage — Project Overview

A single-document reference covering everything Sage does today: product surface, features, architecture, tech stack, data model, request flows, deployment, and operational characteristics. Wave 3 (planned agentic orchestration) is intentionally excluded — this document describes only what is shipped and running.

---

## 1. Elevator Pitch

Sage is a **local-first, privacy-respecting personal AI assistant** that combines retrieval-augmented generation (RAG), persistent memory, live news, web search, email triage, habit tracking, and proactive reminders into one interface.

- **No cloud LLM by default.** Generation and embeddings run on a local Ollama server.
- **Three surfaces, one brain.** A CLI (`sage`), a web frontend (`frontend/index.html`), and a WhatsApp number (via Twilio webhook) all hit the same Python core.
- **Open-source tool calling.** Sage routes user intent to tools (RAG search, web search, news, todos, habits, facts, Apple Reminders) via the local LLM — no hardcoded command parsing required.
- **All state local.** SQLite + ChromaDB under `./data`. Nothing leaves the machine except the calls the user explicitly opts into (Twilio, Gmail OAuth, Tavily, Google News RSS).

---

## 2. Target User

Single-user personal tool. Designed for someone who:

- Wants an AI assistant whose data stays on their machine.
- Uses WhatsApp as a daily inbox and wants their assistant reachable there.
- Has a small personal knowledge base (notes, PDFs, articles) they want to query conversationally.
- Is comfortable running a Python CLI or Docker Compose stack.

Not a SaaS. No multi-tenant support. No public hosted version.

---

## 3. Feature Inventory (Shipped)

| # | Feature | Surfaces | Notes |
|---|---------|----------|-------|
| 1 | Smart chat with persistent sessions | CLI, Web, WhatsApp | Multi-turn context; resume with session ID |
| 2 | Tool-calling LLM router | All | LLM decides when to call which tool from natural language |
| 3 | Learned facts (personal / work) | All | `/remember-personal`, `/remember-work`, auto-injected into prompts |
| 4 | RAG over local documents | All | `.txt`, `.md`, `.pdf`; semantic chunked search with citations |
| 5 | URL ingestion | All | Paste a URL in chat → scrape → chunk → embed |
| 6 | Live news fetch | All | Google News RSS + LLM-generated summary |
| 7 | Web search | All | Tavily (primary) → DuckDuckGo (fallback) |
| 8 | Sage-owned reminders / todos | All | SQLite-backed; natural-language due dates; proactive WhatsApp delivery |
| 9 | Apple Reminders (opt-in) | CLI/macOS | Explicit `/apple-reminder` only; never auto-written |
| 10 | Habit tracker | All | `/habit add/log/delete`; streaks; weekly summary |
| 11 | Proactive morning briefing | WhatsApp | Habits + news + due todos at configured hour |
| 12 | Habit nudges with fast-reply | WhatsApp | `done` / `skipped` short-circuits the LLM |
| 13 | Gmail triage | All | Pulls primary-tab mail, classifies ACTION / FYI / IGNORE |
| 14 | WhatsApp integration | WhatsApp | Twilio webhook; long-message splitting; per-phone session affinity |
| 15 | Twilio usage tracking + alerts | All | Daily counts, thresholds at 25/45/49 messages |
| 16 | Conversation analytics | CLI, Web | Sessions, hours, top commands, top topics |
| 17 | Web frontend | Web | Static `frontend/index.html` served by FastAPI; passphrase login |
| 18 | REST API | Web | `/api/v1/*` behind `X-Sage-Key` header |
| 19 | Docker deployment | All | Compose stack with optional Ollama container |

---

## 4. User Surfaces

### 4.1 CLI (`sage`)
- Built with Typer + Rich.
- Commands: `config`, `ingest`, `ask`, `sources`, `chat`, `email-personal`, `serve`.
- Slash commands inside `chat`: `/help`, `/session(s)`, `/topk`, `/analytics`, `/usage`, `/remember-personal`, `/remember-work`, `/facts`, `/forget`, `/email`, `/news`, `/search`, `/todo`, `/apple-reminder`, `/habit add|log|unlog|delete`, `/habits`.

### 4.2 Web frontend
- Single self-contained `frontend/index.html`.
- Talks to the FastAPI server at `/api/v1/*`.
- Login = passphrase posted to `/api/v1/auth/login`, stored in `sessionStorage`, sent as `X-Sage-Key` on every request.
- Pages: chat (with sidebar of sessions) + profile (facts, habits, knowledge base, analytics).

### 4.3 WhatsApp
- Twilio Sandbox or paid number → Twilio posts inbound messages to `POST /webhook`.
- Sage looks up `whatsapp_sessions` by phone number → resumes the user's persistent session.
- Replies sent via Twilio REST API, split at 1600-char sentence boundaries.
- Daily outbound quota tracked; usage alerts auto-sent at thresholds.

---

## 5. Architecture

### 5.1 High-level

```
┌─────────────┐  ┌─────────────┐  ┌──────────────────┐
│ CLI (Typer) │  │ Web (HTML)  │  │ WhatsApp (Twilio)│
└──────┬──────┘  └──────┬──────┘  └────────┬─────────┘
       │                │                  │
       │         ┌──────▼──────────────────▼──────┐
       │         │  FastAPI (app/webhook/server)  │
       │         │  ├─ /webhook  (Twilio)         │
       │         │  ├─ /api/v1/* (frontend REST)  │
       │         │  └─ static /  (frontend HTML)  │
       │         └────────────────┬───────────────┘
       │                          │
       └──────────────┬───────────┘
                      ▼
              ┌───────────────┐
              │ ChatService   │  ← single entry point for any user turn
              │ (app/core)    │
              └──────┬────────┘
                     │
        ┌────────────┼──────────────────────────────────────────────┐
        ▼            ▼            ▼          ▼          ▼           ▼
   Pattern      ToolExecutor  FactService  Retriever  Prompt    Provider
   detection    (registry)               (Chroma)    builder   (Ollama)
        │            │            │          │          │           │
        │            ▼            ▼          ▼          │           │
        │       Tools:                                  │           │
        │       ─ search_documents → Chroma             │           │
        │       ─ web_search       → Tavily/DDG         │           │
        │       ─ fetch_news       → Google News RSS    │           │
        │       ─ add_todo         → SQLite             │           │
        │       ─ add_apple_reminder → macOS osascript  │           │
        │       ─ remember/list_fact → SQLite           │           │
        │       ─ add_habit/log_habit/get_habits → SQLite │         │
        │                                                ▼           ▼
        │                                          Prompt assembled, sent to LLM
        ▼
   Cited reply
   (web 🌐 / news 📰 / docs 📄)

Scheduler (APScheduler) — runs inside `sage serve`:
  ├─ Morning briefing job        (MORNING_BRIEFING_TIME)
  ├─ Habit nudge job             (HABIT_NUDGE_TIME)
  ├─ Per-reminder `date` jobs    (reloaded from SQLite on startup)
  └─ 1-minute fallback scanner   (catches missed reminders after downtime)

Persistence:
  ├─ SQLite (./data/sqlite/registry.db)
  └─ ChromaDB (./data/chroma/)
```

### 5.2 Module layout (`app/`)

| Package | Responsibility |
|---------|---------------|
| `app/cli/` | Typer CLI entry, interactive chat REPL, slash-command dispatch |
| `app/api/` | FastAPI routers for `/api/v1/*` (auth, sessions, facts, habits, sources, analytics, profile) |
| `app/webhook/` | FastAPI server, Twilio webhook, mounts API + static frontend |
| `app/core/` | Orchestration: `ChatService`, `ToolExecutor`, `tools.py`, `FactService`, `HabitService`, `AnalyticsService`, `IngestCoordinator`, `QAService`, `todo_parser` |
| `app/providers/` | `OllamaChatProvider`, `OllamaEmbeddingsProvider` |
| `app/retrieval/` | `Retriever` (Chroma query), `PromptBuilder` (context assembly with cited sources) |
| `app/ingestion/` | `IngestService`, `Chunker`, ID generation |
| `app/parsers/` | Per-format parsers (`.txt`, `.md`, `.pdf`) |
| `app/services/` | External integrations: `EmailService` (Gmail), `NewsService`, `WebSearchService`, `WhatsAppService`, `RemindersService` (Apple), `UrlIngestionService` |
| `app/scheduler/` | APScheduler setup, briefing/nudge/reminder jobs |
| `app/storage/` | `SQLiteRegistry`, `ChromaStore`, repositories, `sql_schema.sql` |
| `app/schemas/` | Pydantic models |
| `app/config/` | Settings loader (env + defaults) |
| `app/export/` | Markdown export for `sage ask --export` |

### 5.3 Request flow — chat turn

1. **Inbound** — CLI keypress, REST `POST /api/v1/sessions/{id}/chat`, or Twilio `POST /webhook` with phone number → looks up session.
2. **Pattern detection** in `ChatService`:
   - Conversational (greeting, meta) → skip RAG, go straight to LLM.
   - Slash command (`/news`, `/search`, `/email`, `/todo`, etc.) → direct service call.
   - Otherwise → tool-calling LLM loop.
3. **Tool-calling loop** — `OllamaChatProvider` is given the tool schema. Model emits structured tool calls; `ToolExecutor` runs them; results are fed back; loop terminates when the model produces a final assistant message.
4. **Prompt assembly** (`PromptBuilder`) — injects: system persona, learned facts (personal + work), prior turns, tool outputs, source citations.
5. **Persist** — turn (user + assistant) saved to `chat_turns`; any side-effects (todos, facts, habit logs, ingested URLs) committed to their tables; scheduler `date` job added for new dated todos.
6. **Deliver**:
   - CLI → Rich-rendered output with citation footers.
   - Web → JSON `{ reply, sources, steps, latency_ms }`.
   - WhatsApp → Twilio REST send, split at sentence boundaries; usage counter incremented.

### 5.4 RAG pipeline

```
Ingest:
  File or URL ─▶ Parser (md/txt/pdf/html) ─▶ Chunker (CHUNK_SIZE=800, OVERLAP=120)
              ─▶ OllamaEmbeddingsProvider (nomic-embed-text)
              ─▶ ChromaStore.upsert(chunks)
              ─▶ SQLite: documents + chunks rows with checksum

Query:
  User question ─▶ embed query ─▶ Chroma.similarity_search(top_k)
                ─▶ PromptBuilder assembles cited context block
                ─▶ Ollama generates answer with [N] citations
                ─▶ Citation footer rendered per surface
```

Document chunks are deduplicated by SHA-256 checksum at the document level; re-ingesting an unchanged file is a no-op.

---

## 6. Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| LLM | Ollama (local — `llama3.2:3b` default; `llama3.1:8b` / `mistral:7b` recommended for tool calling) |
| Embeddings | Ollama `nomic-embed-text` |
| Vector DB | ChromaDB (persistent, local) |
| Database | SQLite |
| Web server | FastAPI + Uvicorn |
| CLI | Typer + Rich |
| Scheduler | APScheduler |
| Messaging | Twilio (WhatsApp) |
| Email | Gmail API + OAuth 2.0 |
| Web search | Tavily API (primary), DuckDuckGo (fallback) |
| News | Google News RSS |
| Web scraping | BeautifulSoup4 + httpx |
| Frontend | Single static `index.html` (vanilla JS) |
| Container | Docker + Docker Compose |

---

## 7. Data Model

All persistent state lives in `./data/`:

```
data/
├── sqlite/registry.db          # operational DB
├── chroma/                     # vector embeddings
├── cache/                      # transient files
└── credentials/
    ├── credentials.json        # Gmail OAuth client secret
    └── personal_token.json     # Gmail OAuth refresh token (auto)
```

### SQLite schema (`app/storage/sql_schema.sql`)

| Table | Purpose |
|---|---|
| `documents` | Ingested files/URLs: path, checksum, source type/url, metadata |
| `chunks` | Per-document chunks with offsets and token counts |
| `chat_sessions` | Session metadata |
| `chat_turns` | Ordered user/assistant turns per session |
| `learned_facts` | Personal / work facts with usage counters |
| `todos` | Sage-owned reminders (title, list, due_at, notified_at, completed_at) |
| `habits` | Tracked habits with reminder time |
| `habit_logs` | Per-day done/skipped status |
| `nudge_context` | Last habit nudge per phone (for fast-reply attribution) |
| `whatsapp_sessions` | Phone-number → session_id mapping |
| `whatsapp_usage_daily` | Outbound message counter per UTC day |
| `whatsapp_usage_alerts` | Throttle for 25/45/49-message alerts |
| `named_sessions` | Human-friendly aliases for session IDs |

### ChromaDB
One collection per content type (documents, URL articles). Embeddings produced by `nomic-embed-text`. Persistent on disk; no network.

---

## 8. Tools Available to the LLM

Defined in `app/core/tools.py`, executed by `ToolExecutor`:

| Tool | Backend service | Effect |
|---|---|---|
| `search_documents(query, top_k)` | `Retriever` + `ChromaStore` | Semantic search over user's docs |
| `web_search(query)` | `WebSearchService` | Tavily → DDG fallback |
| `fetch_news(query)` | `NewsService` | Google News RSS articles |
| `remember_fact(fact, category)` | `FactService` | Insert into `learned_facts` |
| `list_facts(category)` | `FactService` | Return stored facts |
| `add_todo(task, list_name, due_date)` | `todo_parser` + SQLite | New SQLite todo + APScheduler date job |
| `add_apple_reminder(task, list_name, due_date)` | `RemindersService` (osascript) | macOS Reminders via AppleScript |
| `add_habit(name, reminder_time)` | `HabitService` | Tracked in `habits` table |
| `log_habit(name, status)` | `HabitService` | Insert into `habit_logs` |
| `get_habits()` | `HabitService` | Streak summary |

The LLM is given JSON-Schema descriptions for these tools and decides which to call. Manual slash-commands in the CLI hit the same service methods, bypassing the LLM tool-calling loop for low-latency UX.

---

## 9. REST API Surface (`/api/v1/*`)

Single-user auth via `X-Sage-Key` header matched against `SAGE_PASSPHRASE`.

| Resource | Endpoints |
|---|---|
| Auth | `POST /auth/login` |
| Sessions | `GET /sessions`, `POST /sessions`, `GET /sessions/{id}/messages`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}` |
| Chat | `POST /sessions/{id}/chat` → `{ reply, sources, steps, latency_ms }` |
| Facts | `GET/POST/DELETE /facts` |
| Habits | `GET/POST /habits`, `POST/DELETE /habits/{id}/log`, `DELETE /habits/{id}` |
| Sources | `GET /sources`, `POST /sources/ingest` |
| Analytics | `GET /analytics`, `GET /profile` |

CORS is open in dev; tighten before any non-LAN exposure.

---

## 10. Scheduler & Proactive Behavior

Runs inside the `sage serve` process via APScheduler:

- **Morning briefing job** — at `MORNING_BRIEFING_TIME`, sends habits status + top news + due todos to `YOUR_WHATSAPP_NUMBER`.
- **Habit nudge job** — at `HABIT_NUDGE_TIME`, asks about unlogged habits; user replies `done` / `skipped` for one-tap logging via `nudge_context`.
- **Reminder jobs** — each todo with a `due_at` registers a one-shot `date` job. On startup, pending future jobs are reloaded from SQLite.
- **Missed-reminder scanner** — runs every minute, finds todos whose `due_at` has passed but `notified_at` is null. Recovers anything missed while the server was down.
- **Twilio failure handling** — failed sends are logged and `notified_at` is left null so the scanner can retry.
- **Usage alerts** — at 25, 45, and 49 outbound WhatsApp messages per day, Sage sends a self-warning (the 49 alert intentionally uses the last available send slot).

---

## 11. Configuration

All via `.env` (loaded by `app/config/`):

```env
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=llama3.2:3b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Chunking / Retrieval
CHUNK_SIZE=800
CHUNK_OVERLAP=120
RETRIEVAL_TOP_K=5
NEWS_MAX_RESULTS=5
EMAIL_MAX_RESULTS=20

# Web search
TAVILY_API_KEY=
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROVIDER=tavily   # or duckduckgo

# WhatsApp / Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TWILIO_DAILY_MESSAGE_LIMIT=50
WEBHOOK_PORT=8000
WHATSAPP_ENABLED=true

# Scheduler / Proactive
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
HABIT_NUDGE_TIME=21:00
YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234

# Apple Reminders (opt-in)
REMINDERS_DEFAULT_LIST=Reminders

# Frontend / API
SAGE_PASSPHRASE=
SAGE_USERNAME=

# Personality / Storage
ASSISTANT_NAME=Sage
DATA_DIR=./data
APP_ENV=development
```

---

## 12. Deployment

### Local Python
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama serve &
ollama pull nomic-embed-text && ollama pull llama3.2:3b
sage chat                          # interactive
sage serve --port 8000             # webhook + REST API + frontend
```

### Docker Compose
```bash
docker compose up -d               # sage + ollama
docker compose -f docker-compose.host-ollama.yml up -d   # use host's Ollama
```

`./data` is bind-mounted; the container is stateless aside from that. Linux containers cannot create macOS Apple Reminders — every other feature still works.

### LAN exposure
The webhook server binds `0.0.0.0` and CORS is open, so the web frontend and WhatsApp webhook are reachable from the local network. For internet exposure (e.g., Twilio reaching a dev laptop), use `ngrok http 8000`.

---

## 13. Security & Privacy

- **Local-first:** LLM inference, embeddings, and vector storage never leave the machine.
- **Explicit network egress:** Twilio (when WhatsApp on), Gmail API (when triage invoked), Tavily / DuckDuckGo (when web tool invoked), Google News RSS (when news invoked). Each is opt-in via config or tool invocation.
- **Auth:** Single-user passphrase on the REST API (`X-Sage-Key`). CLI relies on local OS user permissions.
- **Credentials at rest:** Gmail OAuth token and Twilio creds live in `./data/credentials/` and `.env` respectively — no secrets in source.
- **Webhook validation:** Twilio webhook should be reached over HTTPS (ngrok / reverse proxy). Signature validation is not enforced; do not expose `/webhook` to the open internet without adding it.
- **No telemetry:** No analytics calls home.

---

## 14. Performance & Limits

Typical latencies (local Ollama on a developer laptop):

| Query type | Latency |
|---|---|
| Conversational (no RAG) | <100 ms (excluding LLM tokens) |
| RAG / document query | 1–2 s |
| News query | 2–3 s |
| Web search | 1–3 s |
| Cached follow-up (news still in context) | 1–2 s |

Resource footprint: ~300 MB base process; ~800 MB with one model loaded; +~500 MB per 100 MB of ingested documents.

Known limits:
- Single-user. SQLite is fine for this scope; not designed for concurrent writers.
- WhatsApp Twilio sandbox is rate-limited (default `TWILIO_DAILY_MESSAGE_LIMIT=50`).
- Tool-calling reliability degrades on the 3B model; 7–8B is recommended for production-feeling behavior.
- RAG is single-stage similarity search — no reranker, no hybrid (BM25 + vector) retrieval today.

---

## 15. Testing

- Unit + integration suites under `tests/` mirror `app/` packages (cli, core, services, providers, retrieval, scheduler, storage, parsers, ingestion, webhook, e2e).
- Fixtures in `tests/fixtures/`.
- No formal eval harness for RAG / tool-calling quality yet — interactions are validated via integration tests and manual dogfood.

---

## 16. Repository Map

```
.
├── app/                  # Application code (see §5.2)
├── frontend/index.html   # Static single-page web UI
├── docs/
│   ├── PROJECT_OVERVIEW.md       # ← this file
│   ├── prd/                      # Wave PRDs + product overview
│   └── feat/                     # Per-wave phase plans
├── tests/                # Pytest suites mirroring app/
├── scripts/              # Operational scripts
├── data/                 # Runtime state (SQLite, Chroma, creds) — gitignored
├── Dockerfile
├── docker-compose.yml
├── docker-compose.host-ollama.yml
├── requirements.txt
├── setup.py
└── README.md
```

---

## 17. What Sage Is Not

- Not multi-tenant. No user accounts, no row-level isolation.
- Not a hosted SaaS. No public deployment.
- Not connected to any cloud LLM by default. Every generation runs on local Ollama.
- Not a browser extension. The web UI is a static page served by the same FastAPI process.
- Does not send email or post on the user's behalf without explicit invocation.
