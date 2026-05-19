# Sage — Complete System Review

**Last updated:** May 19, 2026  
**Branch:** main  
**Live URL:** https://sage-2607286466.us-central1.run.app  
**Purpose:** Full reference for the Wipro FDE assignment review — agents, pipeline, security, storage, deployment.

---

## Table of Contents

1. [What Sage Is](#1-what-sage-is)
2. [System Architecture](#2-system-architecture)
3. [The Six Agents](#3-the-six-agents)
4. [Multi-Agent Pipeline — Step by Step](#4-multi-agent-pipeline--step-by-step)
5. [Security Layer](#5-security-layer)
6. [Infinite Loop Prevention](#6-infinite-loop-prevention)
7. [LLM & Embeddings Provider Layer](#7-llm--embeddings-provider-layer)
8. [Data & Persistence](#8-data--persistence)
9. [Authentication & Multi-User Isolation](#9-authentication--multi-user-isolation)
10. [User Surfaces](#10-user-surfaces)
11. [REST API](#11-rest-api)
12. [Deployment](#12-deployment)
13. [Configuration Reference](#13-configuration-reference)
14. [HITL Gate](#14-hitl-human-in-the-loop-gate)
15. [Known Limitations](#15-known-limitations)
16. [What's Not Built Yet](#16-whats-not-built-yet)

---

## 1. What Sage Is

Sage is a **personal AI chief-of-staff** built as a multi-agent system. It combines:

- RAG over personal documents with LLM-extracted metadata filters
- Live web search and news
- Gmail integration with per-user OAuth tokens stored in Supabase
- Action execution (todos, habits, facts, reminders)
- A multi-agent orchestration layer (Orchestrator → specialized agents)
- A security pipeline that guards every input and output
- Multi-user authentication backed by Supabase

**Three surfaces:** CLI (`sage chat`), Web frontend, WhatsApp (via Twilio)  
**Storage:** SQLite + ChromaDB locally; PostgreSQL (Supabase) + pgvector in cloud  
**LLM (Orchestrator):** Gemini 2.5 Flash (`gemini:gemini-2.5-flash`)  
**LLM (Sub-agents):** Groq (`llama-3.3-70b-versatile`) — fast execution  
**LLM (Local dev):** Ollama  
**Embeddings:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim) — runs in-process, no external service  
**Deployed:** GCP Cloud Run — `https://sage-2607286466.us-central1.run.app`

---

## 2. System Architecture

```
User Input (CLI / Web / WhatsApp)
         │
         ▼
    ChatService
    (app/core/chat_service.py)
         │
         ├── URL detected? ───────────────────────────── UrlIngestionService
         ├── Slash command? ──────────────────────────── Direct dispatch
         └── Everything else ─────────────────────────┐
                                                       ▼
                                               AgentRunner.run()
                                               (app/agents/runner.py)
                                                       │
                                          ┌────────────▼────────────┐
                                          │  SecurityAgent           │
                                          │  check_input()           │
                                          │  ┌─ rate limit?  BLOCK  │
                                          │  ├─ length?      BLOCK  │
                                          │  ├─ html?        CLEAN  │
                                          │  ├─ injection?   BLOCK  │
                                          │  └─ PII?         FLAG   │
                                          └────────────┬────────────┘
                                                       │ (if not blocked)
                                          ┌────────────▼────────────┐
                                          │  OrchestratorAgent       │
                                          │  plan(question, history) │
                                          │  → [AgentStep, ...]      │
                                          └────────────┬────────────┘
                                                       │
                         ┌─────────────────────────────┼──────────────────────────┐
                         ▼                             ▼                          ▼
                    RAGAgent                   ResearchAgent               ActionAgent
              (document search            (web_search: / fetch_news:)  (todos/habits/facts)
               + LLM filter extract)               │                          │
                    │                              │                     EmailAgent
                    └──────────────────────────────┼──────────────────────────┘
                                                   ▼
                                      OrchestratorAgent.synthesize()
                                                   │
                                      ┌────────────▼────────────┐
                                      │  SecurityAgent           │
                                      │  check_output()          │
                                      │  ┌─ secret scrub  REDACT │
                                      │  └─ max length   TRIM   │
                                      └────────────┬────────────┘
                                                   ▼
                                          RunResult → User

Storage Layer (app/storage/factory.py):
  ├── Local dev:  SQLiteRegistry + ChromaDB
  └── Cloud:      PostgresRegistry (Supabase) + PgVectorStore (pgvector)

LLM / Embeddings:
  ├── Orchestrator: Gemini 2.5 Flash (structured JSON planning, full intent understanding)
  ├── Sub-agents:   Groq llama-3.3-70b-versatile (cloud) / Ollama (local)
  └── Embeddings:   sentence-transformers/all-MiniLM-L6-v2 (in-process, 384-dim)

HITL Gate (Human-in-the-Loop):
  ├── ActionAgent intercepts all write actions before execution
  ├── Pending record inserted into hitl_requests (Supabase)
  ├── Frontend renders approve/reject buttons inline in chat
  └── POST /api/v1/hitl/{id}/resolve executes or discards on user decision
```

### Design principle: LLM-first routing

All routing decisions are made by the orchestrator LLM — there are **no keyword lists, regex triggers, or heuristics** deciding which agent handles a message. This means:

- "good catch, now fix the deployment" → not bypassed as a greeting
- "any unread messages?" → routes to email_agent, not missed
- "bookmark this for me: https://..." → LLM understands save-intent
- "what's the latest Alzheimer's research?" → correctly routes to web_search, not news (no "latest" keyword trap)

The only pre-LLM bypass is **URL detection** (structural, not semantic — regex to detect `https?://`) and **slash commands** (explicit user intent via `/`).

---

## 3. The Six Agents

### 3.1 OrchestratorAgent
**File:** `app/agents/orchestrator.py`  
**Role:** Planner and synthesizer — never executes tools directly.  
**Model:** Gemini 2.5 Flash (`ORCHESTRATOR_CHAT_MODEL=gemini:gemini-2.5-flash`)

**What it does:**
- Receives the user's question and recent conversation history (last 4 turns).
- Produces a structured plan: an ordered list of `AgentStep(agent, task)` objects.
- After all steps execute, synthesizes a single coherent reply from all sub-agent results.

**Prompt design:** Comprehensive few-shot examples covering:
- Habits, facts, todos, compound queries
- Email in any phrasing ("check my email", "any urgent messages?", "what's new in my inbox?")
- Document queries — same-session implicit ("give me a summary of the doc"), cross-session by filename, by topic
- Research tasks always prefixed with `fetch_news:` or `web_search:` so ResearchAgent never guesses

**Research task format:** The orchestrator always prefixes research tasks:
- `fetch_news: <query>` → news service
- `web_search: <query>` → web search

**HITL early exit:** If any step returns `metadata.hitl_pending=True`, the runner stops immediately — no further steps run and synthesis is skipped.

**Fallback:** If the LLM plan parse fails → single `conversational` step.

**Guardrails applied by runner:**
- Invalid agent names stripped (allowlist: `rag_agent`, `research_agent`, `action_agent`, `conversational`, `email_agent`)
- Plan capped at 5 steps

---

### 3.2 RAGAgent
**File:** `app/agents/rag_agent.py`  
**Role:** Searches the user's personal saved documents.

**What it does:**
1. Makes a fast LLM extraction call to parse the orchestrator task into:
   - `query` — the clean semantic search string
   - `file_name` — the exact filename if the user referenced one (or null)
2. If `file_name` is extracted, it becomes a **hard WHERE clause** in pgvector (`WHERE c.file_name = ?`), not part of the embedding — precise and fast
3. Embeds the `query` via `sentence-transformers/all-MiniLM-L6-v2`
4. Retrieves top-K most similar chunks from the user's documents
5. If `file_name` filter returns 0 chunks, retries without the filter (graceful fallback)
6. Builds a cited context block and asks the LLM to answer from it

**Examples of filter extraction:**
```
task: "summarize README.md"       → query: "summarize", file_name: "README.md"
task: "give me the title of the README file" → query: "title", file_name: "README.md"
task: "key points on Sage AI"    → query: "key points Sage AI", file_name: null
```

**RAG → Web fallback:**  
If `chunks_found == 0` and `top_score > rag_fallback_distance_threshold` (default `0.5`), ResearchAgent runs instead.

---

### 3.3 ResearchAgent
**File:** `app/agents/research_agent.py`  
**Role:** Live external data — web search and news.

**Tools available:** `web_search` (Tavily → DuckDuckGo fallback), `fetch_news` (Google News RSS)

**Routing:** Fully trust the orchestrator prefix — no keyword heuristics:
- `fetch_news:` prefix → news service
- `web_search:` prefix → web search
- No prefix (shouldn't happen) → defaults to web search

**Meta-language stripping:** Phrases like "with a quick search tell me..." stripped before the search API call.

---

### 3.4 ActionAgent
**File:** `app/agents/action_agent.py`  
**Role:** Side-effecting operations — the only agent that writes user state.

**Tools available:**

| Tool | Effect | HITL? |
|------|--------|-------|
| `add_todo` | Creates a todo with optional due date and list name | Yes |
| `add_habit` | Registers a new habit with reminder time | Yes |
| `log_habit` | Records a habit as done or skipped | Yes |
| `get_habits` | Returns weekly habit summary | No |
| `remember_fact` | Saves a personal or work fact to `learned_facts` | Yes |
| `list_facts` | Returns stored facts by category | No |

**Habit name context injection:** Before extracting the action, the agent fetches the user's existing habit names and injects them into the extraction prompt:
```
Existing habits (use exact name when logging): "read 10 pages", "gym", "meditation"
```
This means "log that I read 10 pages of a book today" correctly maps to `name: "read 10 pages"` without requiring fuzzy matching at lookup time.

**Habit name fuzzy matching (safety net):** `_get_habit_by_name` also does bidirectional partial matching:
1. Exact match (case-insensitive)
2. Forward: stored name CONTAINS query (e.g., "gym" → "going to the gym")
3. Reverse: query CONTAINS stored name (e.g., "read 10 pages of a book" → "read 10 pages")

---

### 3.5 EmailAgent
**File:** `app/agents/email_agent.py`  
**Role:** Fetches and triages the user's Gmail inbox.

**Per-user OAuth tokens stored in Supabase** — each user connects their own Gmail account. The agent:
1. Reads `token_json` from `user_email_tokens` table for the requesting `user_id`
2. Builds a Gmail API client from the stored token; auto-refreshes expired tokens
3. Persists refreshed tokens back to Supabase automatically
4. Fetches inbox, runs LLM triage (ACTION / FYI / IGNORE per email)
5. Returns a formatted summary

**Not connected:** Returns a clear message directing the user to Profile → Integrations → Connect Gmail.

**Any phrasing works:**
- "check my email" → `email_agent`
- "any urgent messages?" → `email_agent`
- "what's new in my inbox?" → `email_agent`
- "do I have anything that needs attention?" → `email_agent`

---

### 3.6 ConversationalAgent
**File:** `app/agents/conversational_agent.py`  
**Role:** General chat, greetings, acknowledgements, follow-ups.

- Injects stored personal facts into the system prompt
- Synthesizes previous agent results into a coherent reply when multiple agents ran
- Always the last step in multi-step plans

---

## 4. Multi-Agent Pipeline — Step by Step

### Full pipeline for a complex query

**Input:** `"Search for what LangGraph is, save that I'm studying agent frameworks, and remind me to review it this weekend"`

```
Step 0: SecurityAgent.check_input()
  → rate limit: OK, length: OK, HTML: clean, injection: clean, PII: none
  → blocked=False

Step 1: OrchestratorAgent.plan()
  → LLM produces:
    [
      {agent: "research_agent", task: "web_search: what is LangGraph"},
      {agent: "action_agent",   task: "remember_fact: user is studying agent frameworks"},
      {agent: "action_agent",   task: "add_todo: review LangGraph, due this weekend"},
      {agent: "conversational", task: "confirm what was done and share what was found"}
    ]

Step 2: ResearchAgent → web_search: prefix → web search "LangGraph"
  → AgentResult(output="LangGraph is a...", citations=[...])

Step 3: ActionAgent → remember_fact (HITL gate fires)
  → hitl_requests row created (status=pending, expires in 10 min)
  → AgentResult(output="I'm about to save personal fact: studying agent frameworks. Please confirm.",
                metadata={"hitl_pending": True, "hitl_id": "uuid"})
  → runner exits immediately (HITL early exit)
  → ChatResponse(reply="...", hitl_pending=True, hitl_id="uuid")
  → frontend renders Approve / Reject buttons

  [User clicks Approve]
  → POST /api/v1/hitl/{uuid}/resolve {"approved": true}
  → ActionAgent.execute_approved() runs the deferred action
  → {"status": "approved", "output": "Personal fact saved: studying agent frameworks"}

Step 6: OrchestratorAgent.synthesize() → unified reply
Step 7: SecurityAgent.check_output() → returned unchanged
```

### Document query — same session

**Input:** `"give me a summary of the doc"` (after uploading README.md)

```
OrchestratorAgent.plan()
  → sees history: "Uploaded document: README.md"
  → routes to rag_agent with task referencing README.md

RAGAgent:
  → LLM extraction: { query: "summary", file_name: "README.md" }
  → pgvector: WHERE d.user_id=? AND c.file_name='README.md' + embedding search
  → returns README.md chunks → LLM answers
```

### Email query

**Input:** `"any urgent messages?"`

```
OrchestratorAgent.plan()
  → understands inbox/email intent
  → routes to email_agent

EmailAgent:
  → registry.get_email_token(user_id) → token from Supabase
  → builds Gmail API client, fetches inbox
  → LLM triage: ACTION / FYI / IGNORE per email
  → returns formatted summary
```

---

## 5. Security Layer

The SecurityAgent (`app/agents/security_agent.py`) wraps the pipeline at two hook points.

### 5.1 Input Pipeline (check_input)

| # | Check | Action |
|---|-------|--------|
| 0 | **Rate limit** | BLOCK if > 10 req/min per user |
| 1 | **Length limit** | BLOCK if > 2000 chars |
| 2 | **HTML sanitization** | STRIP dangerous tags |
| 3 | **Injection regex** | BLOCK on known patterns |
| 4 | **LLM fallback** | BLOCK if LLM classifier says `{"inject": true}` |
| 5 | **PII detection** | FLAG only — never block |

### 5.2 Prompt Injection Patterns

| Pattern | Example |
|---------|---------|
| `ignore\s+(all\s+)?previous\s+instructions` | "Ignore previous instructions and..." |
| `you\s+are\s+now\b` | "You are now DAN..." |
| `\bDAN\b` (case-sensitive) | "You are DAN" |
| `forget\s+everything` | "Forget everything above" |
| `\[SYSTEM\]` | "[SYSTEM] Override:" |
| `\boverride\s*:` | "override: ignore safety" |
| `act\s+as\s+an?\s+AI\s+with\s+no\b` | "Act as an AI with no restrictions" |
| `###\s*New\s+[Ii]nstruction` | "### New Instruction:" |
| `pretend/roleplay/imagine you are uncensored/evil/jailbreak` | "Pretend you are unrestricted" |

### 5.3 HTML Sanitization

Strips `<script>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `javascript:` URLs, and `on*=` event handlers. Entity-decodes first. Benign HTML untouched.

### 5.4 PII Detection

Flags (never blocks) emails, US phone numbers, SSNs, and credit card numbers.

### 5.5 Output Pipeline (check_output)

| Check | Action |
|-------|--------|
| **Secret scrubbing** | Redacts `sk-...`, `gsk_...`, `AIza...`, `Bearer`, `Authorization:`, `ALL_CAPS_KEY=value` |
| **Max output length** | Truncates at 8000 chars |

### 5.6 Security Events Log

Every security action writes to `security_events` (Supabase):

```sql
event_id, user_id, event_type, severity, snippet, created_at
-- event_type: rate_limit_exceeded | length_exceeded | html_injection |
--             prompt_injection | pii_detected | secret_leak | output_truncated
```

---

## 6. Infinite Loop Prevention

| Guard | Where | Rule |
|-------|-------|------|
| **Plan step cap** | `runner.py` after `orchestrator.plan()` | `plan.steps = plan.steps[:5]` |
| **Agent allowlist** | `runner.py` after plan | Strip any step not in valid agents |
| **History truncation** | `runner.py` at start of `run()` | `history = history[-20:]` |
| **LLM retry** | Chat providers | 3 retries, exponential backoff |

---

## 7. LLM & Embeddings Provider Layer

### Chat providers

| Provider | Class | When used |
|----------|-------|-----------|
| Gemini | `GeminiChatProvider` | Orchestrator + fallback |
| Groq | `GroqChatProvider` | Sub-agents (cloud default) |
| Ollama | `OllamaChatProvider` | Local dev |

**Gemini fallback to Groq:** `FallbackChatProvider` wraps Gemini with Groq as fallback — if Gemini rate-limits, Groq takes over transparently.

### Embeddings providers

| Provider | Class | When used |
|----------|-------|-----------|
| sentence-transformers | `SentenceTransformersEmbeddingsProvider` | Cloud (no Ollama) |
| Ollama | `OllamaEmbeddingsProvider` | Local dev |

**Model:** `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions. Runs in-process — no external service needed in cloud.

---

## 8. Data & Persistence

### Storage factory (`app/storage/factory.py`)

| `DATABASE_URL` set? | Registry | Vector store |
|---------------------|----------|--------------|
| No (local dev) | `SQLiteRegistry` | `ChromaDB` |
| Yes (cloud) | `PostgresRegistry` | `PgVectorStore` (pgvector) |

### Cloud: Supabase (PostgreSQL + pgvector)

- **Project ref:** `qhzitilsywqtfxuzyioy`
- **Connection:** Shared IPv4 pooler `aws-1-us-east-1.pooler.supabase.com:6543`
- **Migrations:** `scripts/migrations/` — applied via `mcp__supabase__apply_migration`
- **pgvector:** Embeddings stored as `vector(384)`; similarity search via `<=>` operator

### PgVectorStore connection reliability

The retrieval `PgVectorStore` uses **separate connections** for reads (Retriever) and writes (URL ingestion). This prevents URL ingestion failures from corrupting the retriever's transaction state.

`_cursor()` detects and recovers from aborted transaction states (`TRANSACTION_STATUS_INERROR`) by rolling back and reconnecting — so a failed write never silently breaks future reads.

### Supabase schema (key tables)

| Table | Purpose |
|-------|---------|
| `users` | Auth — `user_id` (UUID PK), `username`, `password_hash` |
| `chat_sessions` | Session metadata, `user_id` FK |
| `chat_turns` | User/assistant messages, ordered by `turn_index` |
| `learned_facts` | Personal/work facts, `user_id` FK |
| `habits` | Tracked habits, `user_id` FK |
| `habit_logs` | Per-day done/skipped entries |
| `documents` | Ingested files/URLs, `user_id` FK |
| `chunks` | RAG chunks |
| `chunk_embeddings` | `vector(384)` embeddings, FK to chunks |
| `todos` | Reminders with due dates |
| `security_events` | All security blocks, flags, sanitizations |
| `hitl_requests` | Pending/approved/rejected write actions |
| `user_email_tokens` | Per-user Gmail OAuth token (access + refresh), `user_id` FK |
| `oauth_states` | Short-lived CSRF state tokens for OAuth handshake (10-min expiry) |

### Database migrations

Migration files: `scripts/migrations/YYYYMMDDHHMMSS_description.sql`  
Applied via: `mcp__supabase__apply_migration`  
**Never apply schema changes directly in the Supabase dashboard** — keep SQL files and DB in sync.

---

## 9. Authentication & Multi-User Isolation

### How auth works

**Web frontend:** Login form posts `{username, password}` to `POST /api/v1/auth/login`. On success, credentials stored in `localStorage` (survives full-page navigations like OAuth redirects). Every API request sends:
```
X-Sage-Username: sankalp
X-Sage-Key: <password>
```

**FastAPI dependency (`app/api/deps.py`):**
```python
def get_current_user(x_sage_username, x_sage_key) -> dict:
    user = registry.verify_password(username, key)
    if user is None:
        raise HTTP 401  # No WWW-Authenticate header (prevents browser native prompt)
    return {"user_id": "...", "username": "..."}
```

### Data isolation

Every registry call passes `user_id=current_user["user_id"]`:
- Sessions, facts, habits, documents, email tokens — all scoped to the authenticated user
- pgvector queries always include `WHERE d.user_id = ?`
- No cross-user data leakage possible through the API layer

### Gmail OAuth — per-user

Each user connects their own Gmail account:
1. `GET /api/v1/email/oauth/start` → server generates Google OAuth URL + stores CSRF state
2. User approves on Google consent screen
3. `GET /api/v1/email/callback` → server exchanges code for token, stores in `user_email_tokens`
4. Future email fetches read the token from DB, auto-refresh if expired

The Google OAuth client secret (`GOOGLE_CLIENT_SECRETS_JSON`, base64-encoded) is a server-level secret — users never see it.

---

## 10. User Surfaces

### CLI (`sage chat`)

Full interactive REPL with Rich formatting.

**Slash commands:** `/help`, `/remember-personal`, `/remember-work`, `/facts`, `/forget`, `/todo`, `/habits`, `/habit add|log|unlog|delete`, `/news`, `/sources`, `/sessions`, `/analytics`, `/usage`, `/topk`, `/configure`

### Web Frontend (`frontend/index.html`)

Static single-page app served by FastAPI at `/`.

- **Auth:** Login / Sign Up — username + password stored in `localStorage`
- **Chat:** Session sidebar, message thread, file upload, HITL approve/reject buttons
- **Profile:** Facts, habits, knowledge base, analytics, activity
- **Integrations:** Connect / Disconnect Gmail per-user OAuth flow

### WhatsApp (Twilio)

- `POST /webhook` → looks up session by phone number → `ChatService`
- Replies split at 1600 chars (WhatsApp limit)
- Fast-reply for habit nudges: `done` / `skipped` bypasses LLM
- Daily quota: 50 messages; alerts at 25/45/49

---

## 11. REST API

All endpoints under `/api/v1/*`. Authenticated endpoints require `X-Sage-Username` + `X-Sage-Key` headers.

| Resource | Endpoints |
|----------|-----------|
| **Auth** | `GET /auth/info`, `POST /auth/login`, `POST /auth/signup` |
| **Sessions** | `GET /sessions`, `POST /sessions`, `GET /sessions/{id}/messages`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}`, `POST /sessions/{id}/generate-title` |
| **Chat** | `POST /sessions/{id}/chat` → `{reply, sources, steps, latency_ms, hitl_pending, hitl_id}` |
| **Upload** | `POST /sessions/{id}/upload` → ingest a file into the knowledge base |
| **HITL** | `POST /hitl/{id}/resolve` → `{status, output, success}` |
| **Facts** | `GET /facts`, `POST /facts`, `DELETE /facts/{id}` |
| **Habits** | `GET /habits`, `POST /habits`, `POST /habits/{id}/log`, `DELETE /habits/{id}/log`, `DELETE /habits/{id}` |
| **Sources** | `GET /sources`, `POST /sources/ingest` |
| **Analytics** | `GET /analytics`, `GET /profile` |
| **Email** | `GET /email/status`, `GET /email/oauth/start`, `GET /email/callback`, `DELETE /email/disconnect` |
| **Health** | `GET /health` → `{"status": "ok"}` |

---

## 12. Deployment

### Local development

```bash
sage serve --port 8000
# or
python3 -m uvicorn app.webhook.server:app --port 8000
```

Storage auto-selects: no `DATABASE_URL` → SQLite + ChromaDB.

### GCP Cloud Run

**Live URL:** `https://sage-2607286466.us-central1.run.app`  
**Image registry:** `us-central1-docker.pkg.dev/personal-agent-494817/sage/app:latest`  
**Project:** `personal-agent-494817` | **Region:** `us-central1`

Key Cloud Run settings:
- `--memory 2Gi` — required for sentence-transformers model load
- `--timeout 300` — allows cold-start model initialization
- `--allow-unauthenticated` — auth handled at app layer

### GitHub Actions (CI/CD)

**File:** `.github/workflows/deploy.yml`  
**Trigger:** Manual (`workflow_dispatch`)

**Required GitHub secrets:**

| Secret | Purpose |
|--------|---------|
| `GCP_SA_KEY` | GCP service account key |
| `DATABASE_URL` | Supabase PostgreSQL DSN |
| `GROQ_API_KEY` | Groq API key |
| `GEMINI_API_KEY` | Gemini API key |
| `HUGGINGFACE_API_KEY` | HuggingFace API key |
| `GOOGLE_CLIENT_SECRETS_JSON` | Base64-encoded Google OAuth credentials.json (Web app type) |
| `SAGE_PUBLIC_URL` | Public URL of the deployment (for OAuth callback URI) |

---

## 13. Configuration Reference

```env
# LLM — Gemini (orchestrator)
GEMINI_API_KEY=
GEMINI_CHAT_MODEL=gemini-2.5-flash

# LLM — Groq (sub-agents)
GROQ_API_KEY=
ORCHESTRATOR_CHAT_MODEL=gemini:gemini-2.5-flash
ACTION_CHAT_MODEL=groq:llama-3.3-70b-versatile

# LLM — Ollama (local dev)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=llama3.2:3b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Embeddings (cloud — in-process, no Ollama)
EMBEDDINGS_PROVIDER=sentence-transformers
HUGGINGFACE_API_KEY=
HUGGINGFACE_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIMENSION=384

# Storage — leave unset for local SQLite+ChromaDB
DATABASE_URL=postgresql://...@aws-1-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require

# Gmail OAuth (Web app credentials.json, base64-encoded)
GOOGLE_CLIENT_SECRETS_JSON=
# Public URL used to build the OAuth redirect_uri
SAGE_PUBLIC_URL=https://sage-2607286466.us-central1.run.app

# LLM reliability
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=3

# Pipeline guards
MAX_AGENT_STEPS=5
MAX_HISTORY_TURNS=20

# Security
SECURITY_ENABLED=true
MAX_INPUT_LENGTH=2000
MAX_OUTPUT_LENGTH=8000
RATE_LIMIT_PER_MINUTE=10
RATE_LIMIT_ENABLED=true
HTML_SANITIZATION_ENABLED=true

# RAG
CHUNK_SIZE=800
CHUNK_OVERLAP=120
RETRIEVAL_TOP_K=5
RAG_FALLBACK_DISTANCE_THRESHOLD=0.5

# Web search
TAVILY_API_KEY=
WEB_SEARCH_PROVIDER=tavily
WEB_SEARCH_MAX_RESULTS=5

# WhatsApp / Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TWILIO_DAILY_MESSAGE_LIMIT=50
YOUR_WHATSAPP_NUMBER=
WHATSAPP_ENABLED=true

# Scheduler
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
HABIT_NUDGE_TIME=21:00

# App
ASSISTANT_NAME=Sage
DATA_DIR=./data
APP_ENV=development
```

---

## 14. HITL (Human-in-the-Loop) Gate

### Overview

All ActionAgent write actions require explicit human approval. Read actions (`get_habits`, `list_facts`) bypass HITL.

### Flow

```
User message → Orchestrator → ActionAgent._dispatch()
  → write action detected (add_todo | add_habit | log_habit | remember_fact)
  → create_hitl_request() → DB commit immediately
  → return AgentResult(output="I'm about to <action>. Please confirm.",
                       metadata={hitl_pending: true, hitl_id: uuid})
  → runner exits immediately (HITL early exit — no further steps, no synthesis)
  → ChatResponse includes hitl_pending=true, hitl_id=uuid
  → frontend renders Approve / Reject buttons inline

User clicks Approve:
  POST /api/v1/hitl/{id}/resolve {"approved": true}
  → auth: user_id must match hitl_requests.user_id (404 if mismatch)
  → status check: must be "pending" (409 if already resolved)
  → expiry check: NOW() > expires_at → mark expired, return 410
  → ActionAgent.execute_approved(hitl_id, user_id) runs the deferred action
  → hitl_requests.status = "approved", resolved_at = NOW()

User clicks Reject:
  → hitl_requests.status = "rejected"
```

### Human-readable confirmation text

| Action | Example output |
|--------|---------------|
| `add_todo` | "add a reminder: call mom — due Friday" |
| `add_habit` | "start tracking habit 'reading' with a daily reminder at 21:00" |
| `log_habit` | "mark 'going to the gym' as done for today" |
| `remember_fact` | "save personal fact: I am 23 years old" |

### `hitl_requests` schema

```sql
id              TEXT PRIMARY KEY
user_id         TEXT NOT NULL
session_id      TEXT
action_type     TEXT NOT NULL       -- add_todo | add_habit | log_habit | remember_fact
action_payload  JSONB               -- params extracted by LLM
status          TEXT DEFAULT 'pending'  -- pending | approved | rejected | expired
created_at      TIMESTAMPTZ
expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '10 minutes'
resolved_at     TIMESTAMPTZ
```

---

## 15. Known Limitations

### Agent & Pipeline

| Limitation | Details |
|------------|---------|
| **No parallel agent execution** | Steps execute sequentially — no async fan-out. |
| **Single RAG → Research fallback** | Fallback fires once per step; no third level. |
| **No mid-plan re-planning** | Orchestrator plans once; if a step fails mid-plan, remaining steps run with incomplete context. |
| **No cross-session memory** | Facts are persistent but conversation history is session-scoped. |
| **RAG filter LLM call adds latency** | Every RAG query makes an extra LLM call for metadata extraction (~300-500ms). |

### Security

| Limitation | Details |
|------------|---------|
| **Rate limiting is in-memory** | Resets on Cloud Run instance restart. Multiple instances have independent counters. |
| **PII is flagged, not redacted** | SSNs and card numbers reach the LLM unchanged. |
| **Webhook has no Twilio signature validation** | Anyone knowing the URL can POST fake WhatsApp messages. |
| **LLM injection classifier non-deterministic** | Fails open (allows through) on error. |

### Infrastructure

| Limitation | Details |
|------------|---------|
| **Cold start latency** | First request after Cloud Run scales to zero takes ~20-30s for sentence-transformers to initialize. |
| **Gmail OAuth in Testing mode** | Only manually-added test users can connect Gmail until the app is verified by Google. |

---

## 16. What's Not Built Yet

| Feature | Status | Notes |
|---------|--------|-------|
| Agno workflow/team wiring | Not built | Custom runner used instead |
| SecurityAgent tool authorization (per-agent allowed tools) | Not built | |
| System prompt leakage detection in output | Not built | |
| Twilio webhook signature validation | Not built | |
| Architecture diagram (visual) | Not built | |
| Assignment report | Not built | |
| Parallel agent execution | Not built | |
| RAG reranker / hybrid BM25+vector | Not built | |
| `--min-instances 1` for warm Cloud Run | Not configured | Costs ~$15/month |
| HITL expiry background cleanup | Not built | Expired rows accumulate |
| HITL on WhatsApp | Not built | WhatsApp path bypasses HITL |
| Gmail verification (Google) | Not submitted | App is in Testing mode — only approved test users can connect |

---

## Appendix: Key Architectural Decisions

### Why LLM-first routing (no keyword lists)

The original codebase had ~15 hardcoded keyword lists and regex triggers scattered across 5 files deciding routing: `_FAST_PATH_STARTS`, `_EMAIL_TRIGGERS`, `_EMAIL_ACTION_WORDS`, `NEWS_PATTERNS`, `_INGEST_TRIGGERS`, `_doc_refs`, `_doc_actions`, and more.

These were replaced entirely by the orchestrator LLM because:
- **False positives:** "good catch, fix the deployment" would fast-path to conversational; "find a file manager" would trigger RAG
- **False negatives:** "any urgent messages?" missed email detection; "bookmark this" missed URL save intent
- **Maintenance:** Each new capability required manually updating keyword lists in multiple files
- **The LLM is better at this:** Gemini 2.5 Flash understands intent from context, history, and semantics — not substring matching

### Why separate vector store connections for reads and writes

The shared `PgVectorStore` connection originally used for both the `Retriever` (reads) and `URLIngestionService` (writes) could enter psycopg2's `TRANSACTION_STATUS_INERROR` state if any URL ingestion failed mid-write. The old `_cursor()` only reconnected on `InterfaceError` — so a failed write permanently broke all subsequent reads, returning 0 RAG results silently.

Fix: separate connections + `_cursor()` that detects `INERROR` and rolls back before any query.

### Why per-user Gmail tokens in Supabase (not files)

The original `EmailService` used `InstalledAppFlow.run_local_server()` which opens a browser on the server machine — works on localhost but breaks entirely on Cloud Run (no browser in container). Replaced with a standard web OAuth flow (Google consent screen → server callback → token stored in `user_email_tokens` per user_id).
