# Sage — Complete System Review

**Last updated:** May 2026  
**Branch:** main  
**Live URL:** https://sage-2607286466.us-central1.run.app  
**Purpose:** Full reference for the Wipro FDE assignment review — agents, pipeline, security, storage, deployment.

---

## Table of Contents

1. [What Sage Is](#1-what-sage-is)
2. [System Architecture](#2-system-architecture)
3. [The Five Agents](#3-the-five-agents)
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
14. [Known Limitations](#14-known-limitations)
15. [What's Not Built Yet](#15-whats-not-built-yet)

---

## 1. What Sage Is

Sage is a **personal AI chief-of-staff** built as a multi-agent system. It combines:

- RAG over personal documents
- Live web search and news
- Action execution (todos, habits, facts, reminders)
- A multi-agent orchestration layer (Orchestrator → specialized agents)
- A security pipeline that guards every input and output
- Multi-user authentication backed by Supabase

**Three surfaces:** CLI (`sage chat`), Web frontend, WhatsApp (via Twilio)  
**Storage:** SQLite + ChromaDB locally; PostgreSQL (Supabase) + pgvector in cloud  
**LLM:** Groq (`llama-3.3-70b-versatile`) for cloud; Ollama (local) for dev  
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
         ├── URL ingestion? ──────────────────────────── UrlIngestionService
         ├── Slash command? ──────────────────────────── Direct dispatch
         ├── Email triage? ───────────────────────────── EmailService
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
                              ┌────────────────────────┼────────────────────────┐
                              ▼                        ▼                        ▼
                         RAGAgent              ResearchAgent             ActionAgent
                    (document search)     (web search / news)     (todos/habits/facts)
                              │                        │                        │
                              └────────────────────────┼────────────────────────┘
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
  ├── Chat:       Groq llama-3.3-70b-versatile (cloud) / Ollama (local)
  └── Embeddings: sentence-transformers/all-MiniLM-L6-v2 (in-process, 384-dim)
```

---

## 3. The Five Agents

### 3.1 OrchestratorAgent
**File:** `app/agents/orchestrator.py`  
**Role:** Planner and synthesizer — never executes tools directly.

**What it does:**
- Receives the user's question and recent conversation history.
- Produces a structured plan: an ordered list of `AgentStep(agent, task)` objects.
- After all steps execute, synthesizes a single coherent reply from all sub-agent results.

**Fast paths (no LLM planning):**
- Simple greetings (`hi`, `hello`, `thanks`) → single `conversational` step instantly
- Compound web+news queries detected by rule → two `research_agent` steps without LLM call

**Fallback:** If the LLM plan parse fails → single `conversational` step.

**Guardrails applied by runner:**
- Invalid agent names stripped (allowlist: `rag_agent`, `research_agent`, `action_agent`, `conversational`)
- Plan capped at 5 steps

---

### 3.2 RAGAgent
**File:** `app/agents/rag_agent.py`  
**Role:** Searches the user's personal saved documents.

**Tools available:** `search_documents` (pgvector / ChromaDB semantic search via `Retriever`)

**What it does:**
1. Embeds the task query via `sentence-transformers/all-MiniLM-L6-v2`
2. Retrieves top-K most similar chunks
3. Builds a cited context block and asks the LLM to answer from it
4. Returns `AgentResult` with `citations` and `metadata` (chunks_found, top_score)

**RAG → Web fallback:**  
If `top_score` exceeds `rag_fallback_distance_threshold` (default `0.5`), the RAG result is discarded and `ResearchAgent` runs instead.

---

### 3.3 ResearchAgent
**File:** `app/agents/research_agent.py`  
**Role:** Live external data — web search and news.

**Tools available:** `web_search` (Tavily → DuckDuckGo fallback), `fetch_news` (Google News RSS)

**Routing logic:**
- Explicit `web search:` prefix → web search
- Explicit `news:` prefix → news
- Heuristic detection ("news", "latest", etc.) → news
- Everything else → web search

**Meta-language stripping:** Phrases like "with a quick search tell me..." are stripped before the search API call.

**Limit:** Max 3 web search calls per user turn.

---

### 3.4 ActionAgent
**File:** `app/agents/action_agent.py`  
**Role:** Side-effecting operations — the only agent that writes state.

**Tools available:**

| Tool | Effect |
|------|--------|
| `add_todo` | Creates a todo with optional due date and list name |
| `add_habit` | Registers a new habit with reminder time |
| `log_habit` | Records a habit as done or skipped |
| `get_habits` | Returns weekly habit summary |
| `remember_fact` | Saves a personal or work fact to `learned_facts` |
| `list_facts` | Returns stored facts by category |

All tool calls are scoped to the authenticated `user_id` — no cross-user data leakage.

---

### 3.5 ConversationalAgent
**File:** `app/agents/conversational_agent.py`  
**Role:** General chat, greetings, acknowledgements, follow-ups.

- Injects stored personal facts into the system prompt
- Synthesizes previous agent results into a coherent reply when multiple agents ran
- Fast path for greetings/meta-questions — skips all other agents

---

## 4. Multi-Agent Pipeline — Step by Step

### Full pipeline for a complex query

**Input:** `"Search for what LangGraph is, save that I'm studying agent frameworks, and remind me to review it this weekend"`

```
Step 0: SecurityAgent.check_input()
  → rate limit: OK
  → length: OK (72 chars)
  → HTML: clean
  → injection: clean
  → PII: none
  → blocked=False

Step 1: OrchestratorAgent.plan()
  → LLM produces:
    [
      {agent: "research_agent", task: "Search the web for what LangGraph is"},
      {agent: "action_agent",   task: "Save fact: user is studying agent frameworks"},
      {agent: "action_agent",   task: "Add reminder to review LangGraph this weekend"},
      {agent: "conversational", task: "Confirm what was done and share what was found"}
    ]
  → runner strips invalid names: all valid
  → runner caps at 5: 4 steps, no change

Step 2: ResearchAgent → web search "LangGraph" via Tavily
  → AgentResult(output="LangGraph is a...", citations=[{url, title}])

Step 3: ActionAgent → remember_fact (user_id scoped)
  → FactService.remember(user_id=current_user_id)
  → AgentResult(output="Saved: studying agent frameworks")

Step 4: ActionAgent → add_todo (user_id scoped)
  → registry.create_todo(user_id=..., due_at=Saturday)
  → AgentResult(output="Reminder set for Saturday")

Step 5: ConversationalAgent → synthesizes all results

Step 6: OrchestratorAgent.synthesize() → unified reply

Step 7: SecurityAgent.check_output()
  → no secrets, length OK → returned unchanged
```

### Single-agent fast path

**Input:** `"what is my name"`

```
SecurityAgent.check_input() → clean
OrchestratorAgent.plan() → fast-path: [conversational]
ConversationalAgent.execute()
  → injects facts for user_id: "name: Sankalp Kulkarni"
  → LLM: "Your name is Sankalp Kulkarni."
OrchestratorAgent.synthesize() → single step, no LLM call
SecurityAgent.check_output() → clean
```
**Total LLM calls: 1**

---

## 5. Security Layer

The SecurityAgent (`app/agents/security_agent.py`) wraps the pipeline at two hook points. Configured by `SecurityPolicy` (`app/agents/security_policy.py`).

### 5.1 Input Pipeline (check_input)

| # | Check | Action |
|---|-------|--------|
| 0 | **Rate limit** | BLOCK if > 10 req/min per user |
| 1 | **Length limit** | BLOCK if > 2000 chars |
| 2 | **HTML sanitization** | STRIP dangerous tags |
| 3 | **Injection regex** | BLOCK on 9 patterns + DAN (case-sensitive) |
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

**LLM fallback:** If no regex matches, a second LLM call classifies subtle obfuscated attempts. Fails open on error.

### 5.3 HTML Sanitization

Strips `<script>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `javascript:` URLs, and `on*=` event handlers. Entity-decodes first (`&#60;script&#62;` → `<script>`). Benign HTML is untouched.

### 5.4 PII Detection

Flags (never blocks) emails, US phone numbers, SSNs, and credit card numbers.

### 5.5 Output Pipeline (check_output)

| Check | Action |
|-------|--------|
| **Secret scrubbing** | Redacts `sk-...`, `gsk_...`, `AIza...`, `Bearer`, `Authorization:`, `ALL_CAPS_KEY=value` |
| **Max output length** | Truncates at 8000 chars with ` [truncated]` |

### 5.6 Security Events Log

Every security action writes to `security_events` (Supabase / SQLite):

```sql
event_id, user_id, event_type, severity, snippet, created_at
-- event_type: rate_limit_exceeded | length_exceeded | html_injection |
--             prompt_injection | pii_detected | secret_leak | output_truncated
-- severity:   block | sanitize | flag | redact | info
```

---

## 6. Infinite Loop Prevention

| Guard | Where | Rule |
|-------|-------|------|
| **Plan step cap** | `runner.py` after `orchestrator.plan()` | `plan.steps = plan.steps[:5]` |
| **Agent allowlist** | `runner.py` after plan | Strip any step not in `{rag_agent, research_agent, action_agent, conversational}` |
| **History truncation** | `runner.py` at start of `run()` | `history = history[-20:]` |
| **LLM retry (tenacity)** | `OllamaChatProvider`, `GroqChatProvider` | 3 retries, exponential backoff (1s→10s), transient errors only |

---

## 7. LLM & Embeddings Provider Layer

### Chat providers

| Provider | Class | When used |
|----------|-------|-----------|
| Groq | `GroqChatProvider` | Cloud / default — `GROQ_API_KEY` set |
| Ollama | `OllamaChatProvider` | Local dev — no cloud key |

### Embeddings providers

| Provider | Class | When used |
|----------|-------|-----------|
| sentence-transformers | `SentenceTransformersEmbeddingsProvider` | Cloud (no Ollama) |
| Ollama | `OllamaEmbeddingsProvider` | Local dev |

**Model:** `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions. Runs in-process — no Ollama required in cloud.

### Per-agent model routing

Each agent can use a different model spec (`provider:model`):

```env
ORCHESTRATOR_CHAT_MODEL=groq:llama-3.3-70b-versatile
ACTION_CHAT_MODEL=groq:llama-3.3-70b-versatile
```

**Factory:** `app/providers/factory.py` — `create_chat_provider(settings, spec)` returns the correct provider.

---

## 8. Data & Persistence

### Storage factory (`app/storage/factory.py`)

The backend switches automatically based on `DATABASE_URL`:

| `DATABASE_URL` set? | Registry | Vector store |
|---------------------|----------|--------------|
| No (local dev) | `SQLiteRegistry` | `ChromaDB` |
| Yes (cloud) | `PostgresRegistry` | `PgVectorStore` (pgvector) |

**Rule:** Never instantiate storage classes directly — always use `create_registry()` / `create_vector_store()`.

### Cloud: Supabase (PostgreSQL + pgvector)

- **Project ref:** `qhzitilsywqtfxuzyioy`
- **Connection:** Shared IPv4 pooler `aws-1-us-east-1.pooler.supabase.com:6543` (required for Docker/Cloud Run — direct host is IPv6-only)
- **Migrations:** `scripts/migrations/` — applied via `mcp__supabase__apply_migration`
- **pgvector:** Embeddings stored as `vector(384)` columns; similarity search via `<=>` operator

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
| `chunks` | RAG chunks with embeddings (`vector(384)`) |
| `todos` | Reminders with due dates |
| `security_events` | All security blocks, flags, sanitizations |

### Local: SQLite + ChromaDB

- SQLite at `./data/sqlite/registry.db`
- ChromaDB at `./data/chroma/`
- Same interface via `SQLiteRegistry` / `ChromaStore` — factory returns these when `DATABASE_URL` is unset

### Database migrations

Migration files: `scripts/migrations/YYYYMMDDHHMMSS_description.sql`  
Applied via: `mcp__supabase__apply_migration` (tracked in `supabase_migrations`)  
**Never apply schema changes directly in Supabase dashboard** — keep SQL files and DB in sync.

---

## 9. Authentication & Multi-User Isolation

### How auth works

**CLI:** `sage chat` prompts for username + password on first run. Credentials verified against the `users` table. `user_id` persisted to local config.

**Web frontend:** Login form posts `{username, password}` to `POST /api/v1/auth/login`. On success, credentials stored in `sessionStorage`. Every subsequent API request sends:
```
X-Sage-Username: sankalp
X-Sage-Key: <password>
```

**FastAPI dependency (`app/api/deps.py`):**
```python
def get_current_user(x_sage_username, x_sage_key) -> dict:
    user = registry.verify_password(username, key)
    if user is None:
        raise HTTP 401
    return {"user_id": "...", "username": "..."}
```

All authenticated endpoints declare `current_user: Dict = Depends(get_current_user)`.

### Data isolation

Every registry call passes `user_id=current_user["user_id"]`:

- `registry.list_sessions(user_id=...)` 
- `registry.list_facts(user_id=...)`
- `HabitService(registry, user_id=...)` — created per-request
- `ChatService.answer_in_session(user_id=...)` — threads `user_id` through slash commands and agent calls

Users can only see and modify their own sessions, facts, habits, and documents.

### Auth endpoints (`app/api/auth.py`)

| Endpoint | Purpose |
|----------|---------|
| `GET /auth/info` (public) | Returns model, storage backend, version |
| `POST /auth/login` | Validates credentials, returns `{ok, username}` |
| `POST /auth/signup` | Creates new user (min lengths, duplicate check) |

---

## 10. User Surfaces

### CLI (`sage chat`)

Full interactive REPL with Rich formatting.

**Slash commands:** `/help`, `/remember-personal`, `/remember-work`, `/facts`, `/forget`, `/todo`, `/habits`, `/habit add|log|unlog|delete`, `/news`, `/sources`, `/sessions`, `/analytics`, `/usage`, `/topk`, `/configure`

### Web Frontend (`frontend/index.html`)

Static single-page app served by FastAPI at `/`.

- **Auth:** Login / Sign Up tabs — username + password
- **Chat:** Session sidebar (real sessions from API), message thread
- **Profile:** Facts, habits, knowledge base, analytics
- **Headers:** Every API call sends `X-Sage-Username` + `X-Sage-Key`
- **Sidebar:** Shows live model name, storage backend, username from `/auth/info`

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
| **Chat** | `POST /sessions/{id}/chat` → `{reply, sources, steps, latency_ms}` |
| **Facts** | `GET /facts`, `POST /facts`, `DELETE /facts/{id}` |
| **Habits** | `GET /habits`, `POST /habits`, `POST /habits/{id}/log`, `DELETE /habits/{id}/log`, `DELETE /habits/{id}` |
| **Sources** | `GET /sources`, `POST /sources/ingest` |
| **Analytics** | `GET /analytics`, `GET /profile` |
| **Health** | `GET /health` → `{"status": "ok"}` |

---

## 12. Deployment

### Local development

```bash
# Python
sage serve --port 8000

# Docker
docker compose up --build
# Frontend: http://localhost:8000
```

Storage auto-selects: no `DATABASE_URL` → SQLite + ChromaDB.

### Docker image

```dockerfile
FROM python:3.11-slim
# sentence-transformers included — no Ollama needed
CMD ["sage", "serve", "--port", "8000"]
```

Built image: `personal-agent-sage:latest` (~2.5 GB including sentence-transformers + PyTorch)

### GCP Cloud Run

**Live URL:** `https://sage-2607286466.us-central1.run.app`  
**Image registry:** `us-central1-docker.pkg.dev/personal-agent-494817/sage/app:latest`  
**Project:** `personal-agent-494817` | **Region:** `us-central1`

Deploy command:
```bash
gcloud run deploy sage \
  --image us-central1-docker.pkg.dev/personal-agent-494817/sage/app:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8000 \
  --memory 2Gi \
  --timeout 300 \
  --set-env-vars "DATABASE_URL=...,GROQ_API_KEY=..."
```

Key Cloud Run settings:
- `--memory 2Gi` — required for sentence-transformers model load
- `--timeout 300` — allows cold-start model initialization
- `--port 8000` — matches hardcoded Dockerfile `CMD`
- `--allow-unauthenticated` — public access (auth handled by app layer)

### GitHub Actions (CI/CD)

**File:** `.github/workflows/deploy.yml`  
**Trigger:** Manual (`workflow_dispatch`) — GitHub → Actions → Deploy to Cloud Run → Run workflow

**Pipeline:**
1. Checkout source
2. Auth to GCP via `GCP_SA_KEY` service account secret
3. `docker build` on GitHub runner (Ubuntu)
4. `docker push` to Artifact Registry
5. `gcloud run deploy`

**Required GitHub secrets:** `GCP_SA_KEY`, `DATABASE_URL`, `GROQ_API_KEY`, `HUGGINGFACE_API_KEY`

**Service account:** `sage-deployer@personal-agent-494817.iam.gserviceaccount.com`  
**Roles:** `roles/run.admin`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser`, `roles/cloudbuild.builds.editor`

---

## 13. Configuration Reference

```env
# LLM — Groq (cloud)
GROQ_API_KEY=
ORCHESTRATOR_CHAT_MODEL=groq:llama-3.3-70b-versatile
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

## 14. Known Limitations

### Agent & Pipeline

| Limitation | Details |
|------------|---------|
| **No parallel agent execution** | Steps execute sequentially — no async fan-out across agents. |
| **Single RAG → Research fallback** | Fallback fires once per step; no third level. |
| **No mid-plan re-planning** | Orchestrator plans once; if a step fails mid-plan, remaining steps run with incomplete context. |
| **No cross-session memory** | Facts are persistent but conversation history is session-scoped. |

### Security

| Limitation | Details |
|------------|---------|
| **Rate limiting is in-memory** | Resets on Cloud Run instance restart. Multiple instances each have independent counters. |
| **PII is flagged, not redacted** | SSNs and card numbers reach the LLM unchanged. |
| **Webhook has no Twilio signature validation** | Anyone knowing the URL can POST fake WhatsApp messages. |
| **LLM injection classifier non-deterministic** | Fails open (allows through) on error. |

### Infrastructure

| Limitation | Details |
|------------|---------|
| **Cold start latency** | First request after Cloud Run scales to zero takes ~20-30s for sentence-transformers to initialize. |
| **Single Cloud Run instance** | No horizontal scaling config — one instance handles all traffic. |
| **Apple Reminders macOS only** | `RemindersService` uses AppleScript; fails silently in Cloud Run. |

---

## 15. What's Not Built Yet

| Feature | Status | Notes |
|---------|--------|-------|
| Gemini provider | Not built | Groq covers the cloud LLM need |
| Agno workflow/team wiring | Not built | Custom runner used instead |
| SecurityAgent tool authorization (per-agent allowed tools) | Not built | |
| System prompt leakage detection in output | Not built | |
| Twilio webhook signature validation | Not built | |
| Architecture diagram (visual) | Not built | |
| Assignment report | Not built | |
| Parallel agent execution | Not built | |
| RAG reranker / hybrid BM25+vector | Not built | |
| `--min-instances 1` for warm Cloud Run | Not configured | Costs ~$15/month |

---

## Appendix: Commit History (key milestones)

```
b0f57cc  Add GitHub Actions deploy workflow and .dockerignore
7459d9b  Docker + Supabase IPv4 pooler fix (aws-1-us-east-1.pooler.supabase.com)
726cb72  Auth/user isolation — get_current_user, per-request HabitService/FactService
45a7445  Add Supabase + pgvector + Groq cloud stack for GCP Cloud Run deployment
a7b44b0  Add SecurityPolicy config, rate limiting, and HTML sanitization
b4cd4b7  Add infinite loop prevention — plan caps, history truncation, LLM retries
5586036  Add SecurityAgent — prompt injection blocking, PII flagging, secret scrubbing
56c087c  Use provider factory in commands_email — removes hardcoded Ollama dependency
```
