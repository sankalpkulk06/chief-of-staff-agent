# Sage — Complete System Review

**Last updated:** May 20, 2026 (HITL WhatsApp approval, auto-doc search, profile UI, bug fixes)
**Branch:** `main`  
**Live URL:** https://sage-2607286466.us-central1.run.app  
**Purpose:** Full reference for the Wipro FDE assignment review — agents, pipeline, security, storage, deployment.

---

## Table of Contents

1. [What Sage Is](#1-what-sage-is)
2. [System Architecture](#2-system-architecture)
3. [The Six Agents](#3-the-six-agents)
4. [Multi-Agent Pipeline — Step by Step](#4-multi-agent-pipeline--step-by-step)
5. [RAG Evaluation Layer](#5-rag-evaluation-layer)
6. [Security Layer](#6-security-layer)
7. [Infinite Loop Prevention](#7-infinite-loop-prevention)
8. [LLM & Embeddings Provider Layer](#8-llm--embeddings-provider-layer)
9. [Data & Persistence](#9-data--persistence)
10. [Authentication & Multi-User Isolation](#10-authentication--multi-user-isolation)
11. [User Surfaces](#11-user-surfaces)
12. [REST API](#12-rest-api)
13. [Deployment](#13-deployment)
14. [Configuration Reference](#14-configuration-reference)
15. [HITL Gate](#15-hitl-human-in-the-loop-gate)
16. [Known Limitations](#16-known-limitations)
17. [What's Not Built Yet](#17-whats-not-built-yet)

---

## 1. What Sage Is

Sage is a **personal AI chief-of-staff** built as a multi-agent system. It combines:

- RAG over personal documents with LLM-extracted metadata filters
- **Auto-search user documents for any personal question** — the orchestrator routes to `rag_agent` when a question might be answered by uploaded docs, even if not explicitly asked
- Live web search and news
- Gmail integration with per-user OAuth tokens stored in Supabase
- Action execution (todos, habits, facts, reminders)
- A multi-agent orchestration layer (Orchestrator → specialized agents)
- Dependency-aware parallel execution for independent agent work
- A security pipeline that guards every input and output
- HITL approval for writes, with independent read-only work continuing while approval is pending
- **HITL approval via WhatsApp** — reply `yes`/`no` to approve or reject pending write actions directly from WhatsApp
- Friendly, skimmable answer formatting with concise bullets, compact links, and tasteful emoji anchors
- **Inline LLM-as-judge evaluation** on every RAG turn (faithfulness + answer relevancy)
- **Daily overview** — "what's on my plate?" combines todos + habit summary in one answer
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
                                          │  → [AgentStep{id, mode,  │
                                          │      depends_on, group}] │
                                          └────────────┬────────────┘
                                                       │
                                          ┌────────────▼────────────┐
                                          │  Dependency Batcher      │
                                          │  plan.execution_batches()│
                                          │  read steps fan out      │
                                          │  writes/synthesis isolate│
                                          └────────────┬────────────┘
                                                       │
                         ┌─────────────────────────────┼──────────────────────────┐
                         ▼                             ▼                          ▼
                    RAGAgent                   ResearchAgent               ActionAgent
              (document search            (web_search: / fetch_news:)  (todos/habits/facts)
               + LLM filter extract               │                          │
               + retrieved_contexts               │                     EmailAgent
                 in metadata)                     │                          │
                    └─────────────── parallel read batches ───────────────────┘
                                                   ▼
                                      OrchestratorAgent.synthesize()
                                                   │
                                      ┌────────────▼────────────┐
                                      │  SecurityAgent           │
                                      │  check_output()          │
                                      │  ┌─ secret scrub  REDACT │
                                      │  └─ max length   TRIM   │
                                      └────────────┬────────────┘
                                                   │
                                      ┌────────────▼────────────┐
                                      │  RagasService.score()    │  ← RAG turns only
                                      │  (app/core/ragas_service) │    no-fallback only
                                      │  ┌─ faithfulness judge   │
                                      │  └─ relevancy judge      │
                                      │  parallel, 5s timeout    │
                                      └────────────┬────────────┘
                                                   ▼
                                    RunResult → QAResult → ChatResponse
                                    (reply, sources, steps, ragas{...})

Storage Layer (app/storage/factory.py):
  ├── Local dev:  SQLiteRegistry + ChromaDB
  └── Cloud:      PostgresRegistry (Supabase) + PgVectorStore (pgvector)

LLM / Embeddings:
  ├── Orchestrator: Gemini 2.5 Flash (structured JSON planning, full intent understanding)
  ├── Sub-agents:   Groq llama-3.3-70b-versatile (cloud) / Ollama (local)
  ├── RAGAS judge:  Same provider as sub-agents (no extra cost, no extra dependency)
  └── Embeddings:   sentence-transformers/all-MiniLM-L6-v2 (in-process, 384-dim)

HITL Gate (Human-in-the-Loop):
  ├── ActionAgent intercepts all write actions before execution
  ├── Pending record inserted into hitl_requests (Supabase)
  ├── Independent read-only siblings keep running while approval waits
  ├── Completed sibling output is attached to the HITL request
  ├── Frontend renders approve/reject buttons inline in chat
  └── POST /api/v1/hitl/{id}/resolve executes/discards and returns combined follow-up
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
- Produces a structured plan: an ordered list of dependency-aware `AgentStep` objects.
- After all steps execute, synthesizes a single coherent reply from all sub-agent results.

**Plan schema:**
```python
AgentStep(
    id="todos",
    agent="action_agent",
    task="list_todos: retrieve all reminders and tasks due today",
    mode="read",                       # read | write | synthesize
    depends_on=[],
    parallel_group="today_overview",
)
```

**Prompt design:** Comprehensive few-shot examples covering:
- Habits, facts, todos, compound queries
- Email in any phrasing ("check my email", "any urgent messages?", "what's new in my inbox?")
- Document queries — same-session implicit ("give me a summary of the doc"), cross-session by filename, by topic
- Research tasks always prefixed with `fetch_news:` or `web_search:` so ResearchAgent never guesses

**Research task format:** The orchestrator always prefixes research tasks:
- `fetch_news: <query>` → news service
- `web_search: <query>` → web search

**Parallel planning:** Independent read-only steps use empty `depends_on` and may share a `parallel_group`. Synthesis steps depend on every prior tool step. Write steps are isolated unless a future policy proves a narrower conflict boundary is safe.

**HITL behavior:** If a write step returns `metadata.hitl_pending=True`, the runner pauses dependent synthesis but continues independent read-only work. Any completed sibling output is stored on the HITL request so approval/rejection can return a combined follow-up.

**Fallback:** If the LLM plan parse fails → single `conversational` step.

**Guardrails applied by runner:**
- Invalid agent names stripped (allowlist: `rag_agent`, `research_agent`, `action_agent`, `conversational`, `email_agent`)
- Plan capped at 5 steps
- Missing legacy dependency fields backfilled (`id`, `mode`, `depends_on`)
- Writes and synthesis isolated into single-step batches

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
7. Includes retrieved chunk text in `AgentResult.metadata["retrieved_contexts"]` for downstream RAGAS evaluation

**Examples of filter extraction:**
```
task: "summarize README.md"       → query: "summarize", file_name: "README.md"
task: "give me the title of the README file" → query: "title", file_name: "README.md"
task: "key points on Sage AI"    → query: "key points Sage AI", file_name: null
```

**AgentResult metadata (success path):**
```python
metadata = {
    "chunks_found": int,
    "top_score": float,          # cosine distance of best chunk
    "retrieved_contexts": [...], # chunk texts, passed to RagasService
    "source_ids": [...],         # chunk_id list for tracing
}
```

**RAG → Web fallback:**  
If `chunks_found == 0` OR `top_score > 0.65` (poor cosine match), ResearchAgent runs instead. The fallback research result is tagged `metadata["triggered_by_rag_fallback"] = True` — `RagasService` is skipped for these turns.

---

### 3.3 ResearchAgent
**File:** `app/agents/research_agent.py`  
**Role:** Live external data — web search and news.

**Tools available:** `web_search` (Tavily → DuckDuckGo → DuckDuckGo Lite fallback), `fetch_news` (Google News RSS)

**Routing:** Fully trust the orchestrator prefix — no keyword heuristics:
- `fetch_news:` prefix → news service
- `web_search:` prefix → web search
- No prefix (shouldn't happen) → defaults to web search

**Meta-language stripping:** Phrases like "with a quick search tell me..." stripped before the search API call.

**Search reliability fallback:** DuckDuckGo can intermittently return an empty list from one backend even for obvious queries. `WebSearchService` now tries multiple DDG backends and finally parses DuckDuckGo Lite HTML before returning no results.

**Answer style:** Research summaries are written so the user understands the story without opening links. Links are compact Markdown citations, not raw URL dumps. News/resources use short bullets, enough context to act on, and restrained emoji anchors such as `📰`, `🔎`, `🏏`, or `🥊`.

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
- Uses short paragraphs, concise bullets, compact Markdown links, and light emoji section labels when they improve readability

---

## 4. Multi-Agent Pipeline — Step by Step

### Full pipeline for a complex query with parallel reads

**Input:** `"what are my tasks for today and also get the latest news on IPL"`

```
Step 0: SecurityAgent.check_input()
  → OK

Step 1: OrchestratorAgent.plan()
  → LLM produces dependency-aware steps:
    [
      {id: "todos",    agent: "action_agent",   mode: "read",
       task: "list_todos: retrieve all reminders and tasks due today",
       depends_on: [], parallel_group: "today_overview"},
      {id: "ipl_news", agent: "research_agent", mode: "read",
       task: "fetch_news: latest IPL news, matches, and playoff updates",
       depends_on: [], parallel_group: "today_overview"},
      {id: "merge",    agent: "conversational", mode: "synthesize",
       task: "present today's tasks and the latest IPL news clearly",
       depends_on: ["todos", "ipl_news"]}
    ]

Step 2: AgentRunner.plan.execution_batches()
  → Batch 1: [todos, ipl_news]       # runs concurrently
  → Batch 2: [merge]                 # waits for both reads

Step 3a: ActionAgent → list_todos
Step 3b: ResearchAgent → fetch_news
  → both complete independently

Step 4: OrchestratorAgent.synthesize()
  → one friendly, skimmable answer with sections like:
     **📌 Tasks**
     **🏏 IPL news**

Step 5: SecurityAgent.check_output()
Step 6: ChatResponse(reply, steps, sources, latency_ms)
```

### HITL + independent sibling work

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

Step 2: AgentRunner builds batches
  → independent reads can continue
  → writes are isolated

Step 3: ResearchAgent → web_search: prefix → web search "LangGraph"
  → AgentResult(output="LangGraph is a...", citations=[...])

Step 4: ActionAgent → remember_fact (HITL gate fires)
  → hitl_requests row created (status=pending, expires in 10 min)
  → AgentResult(output="I'm about to save personal fact: studying agent frameworks. Please confirm.",
                metadata={"hitl_pending": True, "hitl_id": "uuid"})
  → dependent synthesis is skipped
  → independent completed research output is attached to hitl_requests.action_payload.__hitl_context
  → ChatResponse(reply="approval prompt + research summary", hitl_pending=True, hitl_id="uuid")
  → frontend renders Approve / Reject buttons

  [User clicks Approve]
  → POST /api/v1/hitl/{uuid}/resolve {"approved": true}
  → ActionAgent.execute_approved() runs the deferred action
  → response includes:
     {
       "status": "approved",
       "output": "Personal fact saved: studying agent frameworks",
       "final_reply": "Personal fact saved...\n\nLangGraph summary..."
     }
```

Dependent tasks do **not** continue past HITL. Only independent sibling reads do. True post-approval resumption of dependent steps is still a future enhancement.

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
  → metadata["retrieved_contexts"] = [chunk texts...]

AgentRunner:
  → rag_eval_inputs = {question: "...", contexts: [...]}

ChatService (after SecurityAgent.check_output()):
  → RagasService.score(question, answer, contexts)
  → ChatResponse.ragas = {faithfulness: 0.95, answer_relevancy: 1.00, ...}
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
  (no RAG → ragas: null)
```

---

## 5. RAG Evaluation Layer

**Files:** `app/core/ragas_service.py`, `app/agents/rag_agent.py`, `app/agents/runner.py`, `app/core/chat_service.py`

Sage runs **inline LLM-as-judge evaluation** on every successful RAG turn, scoring the final scrubbed answer on two independent axes. No external eval library — implemented directly against the existing chat provider.

### 5.1 Metrics

| Metric | Measures | Prompt asks |
|--------|---------|-------------|
| **Faithfulness** | Whether every claim in the answer is grounded in the retrieved document chunks | "Is every claim directly supported by the contexts?" |
| **Answer Relevancy** | Whether the answer directly and completely addresses the question asked | "How directly and completely does this answer address the question?" |

Both scores are 0.0–1.0 floats. Both are evaluated by the same LLM used for sub-agents (Groq / Gemini / Ollama) — no extra API keys required.

### 5.2 Data Flow

```
RAGAgent.execute()
  → on success with chunks found:
     metadata["retrieved_contexts"] = [chunk.text for chunk in chunks]
     metadata["source_ids"]         = [chunk.chunk_id for chunk in chunks]

AgentRunner (after agent loop):
  _extract_rag_eval_inputs(question, agent_results)
  → if any result has metadata["triggered_by_rag_fallback"]: return None
  → find first rag_agent result with success=True and retrieved_contexts
  → return {"question": question, "contexts": [...]}
  → stored on RunResult.rag_eval_inputs

ChatService.answer_in_session()
  → AFTER SecurityAgent.check_output() — scores the final scrubbed answer
  → if run.rag_eval_inputs and settings.ragas_enabled:
       RagasService(chat_provider).score(question, answer, contexts, timeout=5.0)
  → result attached to QAResult.ragas_result → ChatResponse.ragas
```

### 5.3 When Eval Runs vs. Is Skipped

| Turn type | Eval runs? | Reason |
|-----------|-----------|--------|
| RAG with good chunks | ✅ Yes | `retrieved_contexts` populated, no fallback flag |
| RAG → web fallback (poor match or no chunks) | ❌ No | `triggered_by_rag_fallback: True` set on research result |
| Web search / news | ❌ No | No `rag_agent` result in agent_results |
| Email, action, conversational | ❌ No | No `rag_agent` result in agent_results |
| HITL pending | ❌ No | Pending write pauses synthesis/eval; independent sibling outputs can still be returned |
| HITL with independent sibling reads | ❌ No | Pending write pauses synthesis; sibling outputs can still be returned |
| Security blocked | ❌ No | Runner exits before eval |

### 5.4 RagasService Implementation

```python
class RagasService:
    def score(self, question, answer, contexts, timeout=5.0) -> RagasResult:
        # Two independent LLM judge calls submitted to a 2-worker thread pool
        # Both run in parallel, sharing a single deadline (not two separate timeouts)
        # pool.shutdown(wait=False) — abandoned threads never block the response

        # Faithfulness prompt:
        #   Question, Answer, and formatted contexts [1], [2], ...
        #   "Score how faithful the answer is to the retrieved contexts"
        #   Returns: {"faithfulness": float}

        # Answer relevancy prompt:
        #   Question and Answer only (no contexts needed)
        #   "Score how relevant and complete this answer is"
        #   Returns: {"answer_relevancy": float}
```

**Error handling per call:**
- `TimeoutError` → `error="timeout"`, that score is `None`
- `json.JSONDecodeError` / `ValueError` → `error="parse_error"`, score is `None`
- Any other exception → `error="llm_error"`, score is `None`

`evaluated=True` if at least one score returned. `evaluated=False` only if both fail. The answer is **always** returned regardless of eval outcome.

### 5.5 API Shape

```json
// RAG turn — eval succeeded
{
  "reply": "...",
  "ragas": {
    "faithfulness": 0.92,
    "answer_relevancy": 1.00,
    "evaluated": true,
    "contexts_count": 5,
    "error": null
  }
}

// RAG turn — eval timed out on faithfulness only (partial)
{
  "ragas": {
    "faithfulness": null,
    "answer_relevancy": 0.88,
    "evaluated": true,
    "contexts_count": 5,
    "error": null
  }
}

// Non-RAG turn or fallback
{
  "ragas": null
}
```

### 5.6 Frontend Badge

Displayed below the sources/latency line on RAG replies:

```
✓ Faithfulness: 0.92  ·  Relevancy: 1.00
```

Color coding:
- **≥ 0.80** — green `#1D9E75` (well grounded / highly relevant)
- **0.60–0.79** — amber `#F0A500` (partial grounding / partial answer)
- **< 0.60** — red `#D85A30` (likely hallucination or off-topic)

Silent (no badge) when `evaluated: false` or `ragas: null`.

### 5.7 What These Scores Tell You

**High faithfulness + high relevancy** (e.g. 0.92 / 1.00) — the answer is grounded in your documents and directly answers what you asked. This is the target state.

**Low faithfulness** (< 0.60) — the model made claims not supported by the retrieved chunks. Likely hallucination or the retrieved chunks were marginally relevant. Check whether the question needed more or different documents.

**Low relevancy** — the answer exists in the documents but didn't directly address the question. May indicate an ambiguous orchestrator task or a chunk context that pulled the answer off-topic.

**Only one score** — the other LLM call timed out (common when the provider is under load on the first turn). The shown score is still valid.

---

## 6. Security Layer

The SecurityAgent (`app/agents/security_agent.py`) wraps the pipeline at two hook points.

### 6.1 Input Pipeline (check_input)

| # | Check | Action |
|---|-------|--------|
| 0 | **Rate limit** | BLOCK if > 10 req/min per user |
| 1 | **Length limit** | BLOCK if > 2000 chars |
| 2 | **HTML sanitization** | STRIP dangerous tags |
| 3 | **Injection regex** | BLOCK on known patterns |
| 4 | **LLM fallback** | BLOCK if LLM classifier says `{"inject": true}` |
| 5 | **PII detection** | FLAG only — never block |

### 6.2 Prompt Injection Patterns

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

### 6.3 HTML Sanitization

Strips `<script>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `javascript:` URLs, and `on*=` event handlers. Entity-decodes first. Benign HTML untouched.

### 6.4 PII Detection

Flags (never blocks) emails, US phone numbers, SSNs, and credit card numbers.

### 6.5 Output Pipeline (check_output)

| Check | Action |
|-------|--------|
| **Secret scrubbing** | Redacts `sk-...`, `gsk_...`, `AIza...`, `Bearer`, `Authorization:`, `ALL_CAPS_KEY=value` |
| **Max output length** | Truncates at 8000 chars |

### 6.6 Security Events Log

Every security action writes to `security_events` (Supabase):

```sql
event_id, user_id, event_type, severity, snippet, created_at
-- event_type: rate_limit_exceeded | length_exceeded | html_injection |
--             prompt_injection | pii_detected | secret_leak | output_truncated
```

---

## 7. Infinite Loop Prevention

| Guard | Where | Rule |
|-------|-------|------|
| **Plan step cap** | `runner.py` after `orchestrator.plan()` | `plan.steps = plan.steps[:5]` |
| **Agent allowlist** | `runner.py` after plan | Strip any step not in valid agents |
| **History truncation** | `runner.py` at start of `run()` | `history = history[-20:]` |
| **LLM retry** | Chat providers | 3 retries, exponential backoff |

---

## 8. LLM & Embeddings Provider Layer

### Chat providers

| Provider | Class | When used |
|----------|-------|-----------|
| Gemini | `GeminiChatProvider` | Orchestrator + fallback |
| Groq | `GroqChatProvider` | Sub-agents + RAGAS judge (cloud default) |
| Ollama | `OllamaChatProvider` | Local dev |

**Gemini fallback to Groq:** `FallbackChatProvider` wraps Gemini with Groq as fallback — if Gemini rate-limits, Groq takes over transparently.

### Embeddings providers

| Provider | Class | When used |
|----------|-------|-----------|
| sentence-transformers | `SentenceTransformersEmbeddingsProvider` | Cloud (no Ollama) |
| Ollama | `OllamaEmbeddingsProvider` | Local dev |

**Model:** `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions. Runs in-process — no external service needed in cloud.

---

## 9. Data & Persistence

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

## 10. Authentication & Multi-User Isolation

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

## 11. User Surfaces

### CLI (`sage chat`)

Full interactive REPL with Rich formatting.

**Slash commands:** `/help`, `/remember-personal`, `/remember-work`, `/facts`, `/forget`, `/todo`, `/habits`, `/habit add|log|unlog|delete`, `/news`, `/sources`, `/sessions`, `/analytics`, `/usage`, `/topk`, `/configure`

### Web Frontend (`frontend/index.html`)

Static single-page app served by FastAPI at `/`.

- **Auth:** Login / Sign Up — username + password stored in `localStorage`
- **Chat:** Session sidebar, message thread, file upload, HITL approve/reject buttons
- **Empty state:** Example queries shown on new chat to onboard users immediately
- **RAG eval badge:** `✓ Faithfulness: 0.92 · Relevancy: 1.00` displayed below source citations on RAG replies, color-coded by score threshold
- **Agent trace stream:** Shows security, planning, parallel batches, agent steps, HITL wait states, synthesis, and output scrub events
- **Readable Markdown renderer:** Supports paragraphs, bullets, numbered lists, bold/italic/code, clean `[label](url)` links, and compact labels for raw long URLs
- **Friendly output style:** Prompts and renderer work together for short bullets, useful summaries, and restrained emoji section anchors
- **Profile:**
  - Facts — view + add facts inline from the profile page (no chat required)
  - Habits — view + add habits inline from the profile page
  - Knowledge base — view + **delete** uploaded sources directly from the profile page
  - Analytics, activity
- **Integrations:** Connect / Disconnect Gmail per-user OAuth flow

### WhatsApp (Twilio)

- `POST /webhook` → looks up session by phone number → `ChatService`
- Replies split at 1600 chars (WhatsApp limit)
- Fast-reply for habit nudges: `done` / `skipped` bypasses LLM
- **HITL approval via WhatsApp:** pending write actions send a confirmation message with `yes`/`no` prompt; phone reply resolves the HITL and executes or discards the action
- WhatsApp messages associated with a configured `SAGE_USERNAME` — all history tied to a real user account
- Daily quota: 50 messages; alerts at 25/45/49

---

## 12. REST API

All endpoints under `/api/v1/*`. Authenticated endpoints require `X-Sage-Username` + `X-Sage-Key` headers.

| Resource | Endpoints |
|----------|-----------|
| **Auth** | `GET /auth/info`, `POST /auth/login`, `POST /auth/signup` |
| **Sessions** | `GET /sessions`, `POST /sessions`, `GET /sessions/{id}/messages`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}`, `POST /sessions/{id}/generate-title` |
| **Chat** | `POST /sessions/{id}/chat` → see response shape below |
| **Upload** | `POST /sessions/{id}/upload` → ingest a file into the knowledge base |
| **HITL** | `POST /hitl/{id}/resolve` → `{status, output, final_reply, success}` |
| **Facts** | `GET /facts`, `POST /facts`, `DELETE /facts/{id}` |
| **Habits** | `GET /habits`, `POST /habits`, `POST /habits/{id}/log`, `DELETE /habits/{id}/log`, `DELETE /habits/{id}` |
| **Sources** | `GET /sources`, `POST /sources/ingest` |
| **Analytics** | `GET /analytics`, `GET /profile` |
| **Email** | `GET /email/status`, `GET /email/oauth/start`, `GET /email/callback`, `DELETE /email/disconnect` |
| **Health** | `GET /health` → `{"status": "ok"}` |

### `POST /sessions/{id}/chat` response shape

```json
{
  "reply": "string",
  "sources": [{"document_id": "...", "file_name": "...", "source_url": "...", "source_type": "..."}],
  "steps": [{"agent": "...", "task": "...", "success": true, "error": null}],
  "latency_ms": 1234,
  "hitl_pending": false,
  "hitl_id": null,
  "ragas": {
    "faithfulness": 0.92,
    "answer_relevancy": 1.00,
    "evaluated": true,
    "contexts_count": 5,
    "error": null
  }
}
```

`ragas` is `null` for non-RAG turns and RAG→web fallback turns.

### `POST /hitl/{id}/resolve` response shape

```json
// Approved action, with independent sibling output attached
{
  "status": "approved",
  "output": "Added reminder: call Harry due Tue, May 19 at 12:00PM.",
  "final_reply": "Added reminder: call Harry due Tue, May 19 at 12:00PM.\n\nHere are useful Python tutorials...",
  "success": true
}

// Rejected action, with independent sibling output preserved
{
  "status": "rejected",
  "final_reply": "Rejected — action was not taken.\n\nHere are useful Python tutorials..."
}
```

`final_reply` is optional and appears when the original turn had independent work that completed while HITL was waiting.

---

## 13. Deployment

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
- `--min-instances 1` — keeps one container always warm; eliminates the 20-30s cold-start hang on first request (~$15/month)

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

## 14. Configuration Reference

```env
# LLM — Gemini (orchestrator)
GEMINI_API_KEY=
GEMINI_CHAT_MODEL=gemini-2.5-flash

# LLM — Groq (sub-agents + RAGAS judge)
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
AGENT_PARALLELISM_ENABLED=true
AGENT_PARALLELISM_MAX_WORKERS=3

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

# RAG evaluation
RAGAS_ENABLED=true          # set false to disable inline LLM-as-judge scoring

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

## 15. HITL (Human-in-the-Loop) Gate

### Overview

All ActionAgent write actions require explicit human approval. Read actions (`get_habits`, `list_facts`, `list_todos`) bypass HITL.

HITL is **non-blocking for independent sibling reads**. If a user asks for a write plus an unrelated read, Sage still runs and returns the read result while waiting for approval.

Example:
```
"remind me to call Harry at 12pm and find Python tutorials"

Action branch:
  → add_todo requires approval
  → approval prompt returned

Research branch:
  → web_search runs anyway
  → tutorial summary returned in the same response

After approval:
  → deferred reminder executes
  → approval endpoint returns final_reply with reminder result + preserved tutorial summary
```

### Flow

```
User message → Orchestrator → ActionAgent._dispatch()
  → write action detected (add_todo | add_habit | log_habit | remember_fact)
  → create_hitl_request() → DB commit immediately
  → return AgentResult(output="I'm about to <action>. Please confirm.",
                       metadata={hitl_pending: true, hitl_id: uuid})
  → runner skips dependent synthesis but continues independent read-only sibling steps
  → completed sibling output stored in action_payload.__hitl_context.continuation_output
  → ChatResponse includes hitl_pending=true, hitl_id=uuid, plus sibling results when present
  → frontend renders Approve / Reject buttons inline

User clicks Approve:
  POST /api/v1/hitl/{id}/resolve {"approved": true}
  → auth: user_id must match hitl_requests.user_id (404 if mismatch)
  → status check: must be "pending" (409 if already resolved)
  → expiry check: NOW() > expires_at → mark expired, return 410
  → ActionAgent.execute_approved(hitl_id, user_id) runs the deferred action
  → hitl_requests.status = "approved", resolved_at = NOW()
  → response.final_reply combines approved action output + preserved sibling output

User clicks Reject:
  → hitl_requests.status = "rejected"
  → response.final_reply preserves sibling output and states action was not taken
```

### Human-readable confirmation text

| Action | Example output |
|--------|---------------|
| `add_todo` | "add a reminder: call mom — due Friday" |
| `add_habit` | "start tracking habit 'reading' with a daily reminder at 21:00" |
| `log_habit` | "mark 'going to the gym' as done for today" |
| `remember_fact` | "save personal fact: I am 23 years old" |

### WhatsApp HITL flow

When a write action triggers via WhatsApp:
1. Sage sends a confirmation message: `"I'm about to add a reminder: drink coffee — due today. Reply yes to confirm or no to cancel."`
2. `whatsapp_hitl_context` table stores `phone_number → hitl_id` for up to 10 minutes
3. Next message from that phone number is checked: if it's `yes`/`no`, it resolves the HITL instead of going to `ChatService`
4. On `yes` → `ActionAgent.execute_approved()` → Sage sends the result via WhatsApp
5. On `no` → marked rejected, Sage confirms cancellation
6. Any other reply clears the pending HITL context and is processed as a normal message

### `hitl_requests` schema

```sql
id              TEXT PRIMARY KEY
user_id         TEXT NOT NULL
session_id      TEXT
action_type     TEXT NOT NULL       -- add_todo | add_habit | log_habit | remember_fact
action_payload  JSONB               -- params extracted by LLM
                                -- may include __hitl_context.continuation_output
status          TEXT DEFAULT 'pending'  -- pending | approved | rejected | expired
created_at      TIMESTAMPTZ
expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '10 minutes'
resolved_at     TIMESTAMPTZ
```

---

## 16. Known Limitations

### Agent & Pipeline

| Limitation | Details |
|------------|---------|
| **Parallelism is conservative** | Independent read-only steps can run in parallel; writes and synthesis are isolated. |
| **Single RAG → Research fallback** | Fallback fires once per step; no third level. |
| **No mid-plan re-planning** | Orchestrator plans once; if a step fails mid-plan, remaining steps run with incomplete context. |
| **No cross-session memory** | Facts are persistent but conversation history is session-scoped. |
| **RAG filter LLM call adds latency** | Every RAG query makes an extra LLM call for metadata extraction (~300-500ms). |
| **No dependent post-HITL resume yet** | Independent sibling work continues while approval waits, but steps that depend on the approved action are not resumed automatically after approval. |

### RAG Evaluation

| Limitation | Details |
|------------|---------|
| **Eval scores not persisted** | Faithfulness and relevancy are returned in the API response only — not written to the database. No historical trend tracking. |
| **5-second eval timeout** | On slow provider warm-up (e.g. first request), one or both scores may time out and return `null`. The answer is always returned. |
| **Faithfulness is grounding-only** | Measures whether the answer is supported by retrieved chunks, not whether the retrieved chunks were the right ones. Low faithfulness means possible hallucination; high faithfulness does not guarantee accuracy. |
| **No retrieval quality metrics** | Context precision/recall, MRR, and NDCG are not tracked. Adding an eval set would enable objective comparison of embedding models, chunk sizes, and top-K settings. |

### Security

| Limitation | Details |
|------------|---------|
| **Rate limiting is in-memory** | Resets on Cloud Run instance restart. Multiple instances have independent counters. |
| **PII is flagged, not redacted** | SSNs and card numbers reach the LLM unchanged. |
| **LLM injection classifier non-deterministic** | Fails open (allows through) on error. |

### Infrastructure

| Limitation | Details |
|------------|---------|
| **Cold start latency** | ~~First request after Cloud Run scales to zero takes ~20-30s.~~ Fixed — `--min-instances 1` keeps one container always warm. |
| **Gmail OAuth in Testing mode** | Only manually-added test users can connect Gmail until the app is verified by Google. |

---

## 17. What's Not Built Yet

| Feature | Status | Notes |
|---------|--------|-------|
| Agno workflow/team wiring | Not built | Custom runner used instead |
| SecurityAgent tool authorization (per-agent allowed tools) | Not built | |
| System prompt leakage detection in output | Not built | |
| Twilio webhook signature validation | Not built | URL construction was fixed; full HMAC request validation not yet added |
| Architecture diagram (visual) | Not built | |
| Assignment report | Not built | |
| Parallel agent execution | ✅ Done | Dependency-aware batches in `app/agents/base.py` + `app/agents/runner.py` |
| RAG reranker / hybrid BM25+vector | Not built | |
| `--min-instances 1` for warm Cloud Run | ✅ Done | Applied live + in deploy.yml |
| HITL expiry background cleanup | Not built | Expired rows accumulate |
| HITL continuation for independent sibling work | ✅ Done | Completed sibling output stored in `hitl_requests.action_payload.__hitl_context` |
| HITL approve/reject bug fix | ✅ Done | Postgres TIMESTAMPTZ vs naive `datetime.now()` comparison raised TypeError inside the scheduler callback — todo was created but response showed "unknown error". Fixed in `_coerce_datetime` + callback isolation. |
| HITL on WhatsApp | ✅ Done | `yes`/`no` reply resolves pending action; `whatsapp_hitl_context` table tracks per-phone pending HITL id |
| Auto-search user docs for personal questions | ✅ Done | Orchestrator routes personal/knowledge questions to `rag_agent` even without explicit "search my docs" phrasing |
| Daily overview (todos + habits combined) | ✅ Done | "what's on my plate?" returns unified today-view from `action_agent` |
| Delete sources from knowledge base (profile UI) | ✅ Done | Sources tab in profile page supports deletion |
| Add facts/habits from profile page | ✅ Done | Inline add forms in profile — no chat required |
| Example queries on new chat empty state | ✅ Done | Onboarding prompts shown when session has no messages |
| Gmail verification (Google) | Not submitted | App is in Testing mode — only approved test users can connect |
| RAG eval score persistence / trend tracking | Not built | Scores returned in API, not stored — no historical dashboard |
| Retrieval quality metrics (precision/recall/MRR) | Not built | Faithfulness measures grounding; retrieval quality requires an eval set |
| LLM-as-judge eval — inline (faithfulness + relevancy) | ✅ Done | `app/core/ragas_service.py`, shown as badge in chat UI |
| Friendly Markdown + emoji answer style | ✅ Done | Prompt guidance + frontend Markdown renderer |

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

### Why no RAGAS library dependency

The `ragas` pip package pulls in `langchain`, `langchain-core`, `datasets`, and the OpenAI SDK — ~15 heavy transitive dependencies for a single metric. Since Sage already has a chat provider that speaks to Groq/Gemini/Ollama, the faithfulness and answer relevancy judges are implemented as two direct LLM calls (~40 lines) with no new dependencies. `concurrent.futures.ThreadPoolExecutor` handles parallelism and timeout; `shutdown(wait=False)` ensures timed-out provider threads never block the response.
