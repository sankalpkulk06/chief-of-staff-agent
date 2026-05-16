# Frontend ↔ Backend Integration Plan

## Context

The `frontend/index.html` is a complete, self-contained UI prototype for Sage. It currently runs entirely with mock data (hardcoded sessions, facts, habits, etc.) and has no connection to the Python backend.

The backend today is a **CLI + WhatsApp webhook tool** with only two HTTP endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/webhook` | WhatsApp inbound messages (Twilio) |
| `GET` | `/health` | Server liveness check |

To connect the frontend, a **REST API layer** needs to be added to the existing FastAPI server (`app/webhook/server.py` or a new `app/api/` router). All new endpoints sit behind the same `uvicorn` process started by `sage serve`.

---

## Existing Endpoints (Backend Today)

### HTTP (FastAPI — `app/webhook/server.py`)

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/health` | Returns `{"status": "ok"}` |
| `POST` | `/webhook` | WhatsApp Twilio webhook — not for frontend use |

### CLI Commands (`app/cli/app.py`)

| Command | What it does |
|---------|-------------|
| `sage config` | Show current config |
| `sage ingest --path <path>` | Ingest files/dirs into ChromaDB |
| `sage ask <question>` | One-shot RAG query |
| `sage sources` | List ingested sources |
| `sage chat [--resume <id>]` | Start interactive chat |
| `sage email-personal` | Triage personal Gmail |
| `sage email-work` | Triage work Gmail |
| `sage serve` | Start FastAPI server |

### Interactive Chat Slash Commands (`app/cli/commands_chat.py`)

| Command | Signature | What it does |
|---------|-----------|-------------|
| `/help` | `/help` | Show command list |
| `/session` | `/session` | Show current session ID |
| `/sessions` | `/sessions` | List recent sessions |
| `/topk` | `/topk <n>` | Set retrieval depth |
| `/analytics` | `/analytics` | Usage dashboard |
| `/usage` | `/usage` | Today's Twilio quota |
| `/remember-personal` | `/remember-personal <fact>` | Store personal fact |
| `/remember-work` | `/remember-work <fact>` | Store work fact |
| `/facts` | `/facts [personal\|work]` | List stored facts |
| `/forget` | `/forget <fact-id>` | Delete a fact |
| `/email` | `/email` | Triage Gmail inbox |
| `/news` | `/news [topic]` | Fetch live news |
| `/search` | `/search <query>` | Web search |
| `/todo` | `/todo <task> [#list] [@due]` | Add Sage reminder |
| `/apple-reminder` | `/apple-reminder <task> [#list] [@due]` | Add Apple Reminders task |
| `/habit add` | `/habit add <name> [@time]` | Track new habit |
| `/habit log` | `/habit log <name> [skipped]` | Log habit done/skipped |
| `/habit unlog` | `/habit unlog <name>` | Remove today's log |
| `/habit delete` | `/habit delete <name>` | Stop tracking habit |
| `/habits` | `/habits` | Weekly habit summary |

---

## New Endpoints Needed (Frontend API)

All new routes live under `/api/v1/`. Authentication is a simple passphrase check via `X-Sage-Key` header (matching the `SAGE_PASSPHRASE` env var) — no JWT needed since this is a single-user local tool.

### Auth

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `POST` | `/api/v1/auth/login` | `{ passphrase }` | `{ ok: true }` or 401 | Login screen → enter chat |

### Sessions

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/sessions` | — | `[{ id, title, created_at, last_message }]` | Sidebar session list |
| `POST` | `/api/v1/sessions` | `{ title? }` | `{ id, title, created_at }` | "New chat" button |
| `GET` | `/api/v1/sessions/{id}/messages` | — | `[{ role, content, created_at, meta? }]` | Load session thread |
| `PATCH` | `/api/v1/sessions/{id}` | `{ title }` | `{ id, title }` | Editable session title in topbar |
| `DELETE` | `/api/v1/sessions/{id}` | — | `{ ok: true }` | (future) delete session |

### Chat (Send Message)

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `POST` | `/api/v1/sessions/{id}/chat` | `{ message }` | `{ reply, sources?, steps?, latency_ms }` | Composer send → thread reply |

`steps` is an array of `{ type: "thought"|"act"|"observe", body }` objects for the step-stream component. `sources` is a list of cited document IDs.

Streaming variant (optional, later): `GET /api/v1/sessions/{id}/chat/stream` via SSE for token-by-token output.

### Facts

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/facts` | — | `[{ id, category, content, created_at }]` | Profile → Learned facts grid |
| `POST` | `/api/v1/facts` | `{ category: "personal"|"work", content }` | `{ id, ... }` | "Add fact" button on profile |
| `DELETE` | `/api/v1/facts/{id}` | — | `{ ok: true }` | "forget" button on fact card |

### Habits

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/habits` | — | `[{ id, name, icon, streak, week: [done\|skip\|future] }]` | Profile → Habits section |
| `POST` | `/api/v1/habits` | `{ name, reminder_time? }` | `{ id, name }` | (future) Add habit UI |
| `POST` | `/api/v1/habits/{id}/log` | `{ status: "done"\|"skipped" }` | `{ ok: true }` | (future) inline log |
| `DELETE` | `/api/v1/habits/{id}/log` | — | `{ ok: true }` | Unlog today |
| `DELETE` | `/api/v1/habits/{id}` | — | `{ ok: true }` | Delete habit |

### Knowledge Base / Sources

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/sources` | — | `[{ id, title, url, type, chunks, ingested_at }]` | Profile → Knowledge base section |
| `POST` | `/api/v1/sources/ingest` | `{ url }` | `{ id, title, chunks }` | Paste URL in chat → auto-ingest |

### Email

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `POST` | `/api/v1/email/triage` | `{ account: "personal"\|"work", max_results? }` | `[{ id, from, subject, label, summary, urgency }]` | `/email` command in chat |

### News & Search

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/news?topic=<str>` | — | `[{ title, url, summary, published_at }]` | `/news` command in chat |
| `GET` | `/api/v1/search?q=<str>` | — | `[{ title, url, snippet }]` | `/search` command in chat |

### Todos / Reminders

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `POST` | `/api/v1/todos` | `{ task, list?, due? }` | `{ id, task, due }` | `/todo` command in chat |
| `GET` | `/api/v1/todos` | — | `[{ id, task, list, due, done }]` | (future) todo panel |

### Analytics / Profile

| Method | Path | Body | Returns | Frontend use |
|--------|------|------|---------|--------------|
| `GET` | `/api/v1/analytics` | — | `{ sessions_total, active_days, heatmap, top_topics, top_commands }` | Profile → Activity section |
| `GET` | `/api/v1/profile` | — | Aggregates sessions, facts count, habit streaks, KB size | Profile hero + stat row |

---

## Integration Plan

### Phase 1 — Wiring Infrastructure (no visible feature change)

1. **Create `app/api/` package** with a FastAPI `APIRouter` mounted at `/api/v1` in `server.py`.
2. **Add passphrase auth middleware** — read `SAGE_PASSPHRASE` from config; all `/api/v1/*` routes require `X-Sage-Key` header matching it.
3. **Add CORS middleware** to `server.py` allowing `localhost:*` origins (for dev; tighten for prod).
4. **Frontend: replace mock login** — `loginForm` submit calls `POST /api/v1/auth/login`, stores passphrase in `sessionStorage`, attaches `X-Sage-Key` header to all subsequent requests.

### Phase 2 — Sessions + Chat

5. **Implement `/api/v1/sessions` CRUD** backed by the existing `SQLite` sessions table.
6. **Implement `POST /api/v1/sessions/{id}/chat`** — this is the core: pipe message through `ChatService`, return `{ reply, sources, steps, latency_ms }`.
7. **Frontend: replace mock thread** — on login success, fetch sessions for sidebar; clicking a session loads its messages; composer calls the chat endpoint and renders the real reply (including step-stream if `steps` present).

### Phase 3 — Profile Data

8. **Implement `/api/v1/facts` CRUD** — thin wrappers around the existing `FactsStorage` service.
9. **Implement `/api/v1/habits` + log endpoints** — wrap `HabitStorage`.
10. **Implement `/api/v1/analytics`** — reuse `AnalyticsService` (already powers `/analytics` CLI command).
11. **Implement `/api/v1/profile`** — aggregate query: sessions count, facts count, habit streaks, KB size.
12. **Frontend: populate profile screen** — replace all hardcoded `renderProfileWidgets()` data with real API calls on `openProfile()`.

### Phase 4 — Commands (Chat-side)

13. **Implement `/api/v1/sources` + ingest** — wrap ingestion pipeline.
14. **Implement `/api/v1/email/triage`** — wrap existing Gmail service.
15. **Implement `/api/v1/news` and `/api/v1/search`** — thin pass-throughs.
16. **Implement `/api/v1/todos`** — wrap todo storage.
17. **Frontend: slash command dispatch** — when the chat reply detects a slash-command intent (e.g. `/email`, `/habits`, `/facts`), the frontend calls the corresponding REST endpoint and renders a structured card instead of a plain text bubble.

### Phase 5 — Polish

18. **SSE streaming** for chat replies — replace the fake 1.1s typing delay with real token streaming.
19. **Optimistic UI** — append user message immediately; replace typing indicator on stream end.
20. **Session title auto-generation** — after first message, `PATCH /api/v1/sessions/{id}` with an LLM-generated title.
21. **Mobile safe-area + PWA manifest** — add `manifest.json` so it can be installed as a home-screen app.

---

## File Change Map

```
app/
  api/                        ← NEW
    __init__.py
    router.py                 ← mounts all sub-routers at /api/v1
    auth.py                   ← POST /auth/login
    sessions.py               ← sessions CRUD + POST /sessions/{id}/chat
    facts.py                  ← facts CRUD
    habits.py                 ← habits CRUD + log endpoints
    analytics.py              ← GET /analytics, GET /profile
    sources.py                ← GET /sources, POST /sources/ingest
    email.py                  ← POST /email/triage
    news.py                   ← GET /news
    search.py                 ← GET /search
    todos.py                  ← todos CRUD
  webhook/
    server.py                 ← mount app/api/router.py here, add CORS

frontend/
  index.html                  ← add API calls (replace mock data)
```

---

## Key Decisions

- **No JWT** — passphrase in `sessionStorage` sent as `X-Sage-Key`. This is a local tool; cookie-based sessions would be overkill.
- **Reuse existing services** — `ChatService`, `FactsStorage`, `HabitStorage`, `AnalyticsService` are already tested via CLI. API layer is thin wrappers, no business logic duplication.
- **No new DB schema** — all new endpoints read/write the existing `registry.db` SQLite tables.
- **Same process** — the frontend is served statically from `/` and the API lives at `/api/v1`. No separate dev server needed; `sage serve` handles everything.
- **Streaming last** — SSE is Phase 5 to avoid complexity during initial wiring.
