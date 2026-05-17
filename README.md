# Sage — Personal AI Agent

Sage is a **multi-agent personal AI assistant** that helps you manage your daily life. Ask it anything in plain English — it decomposes your request, dispatches specialized agents, and returns a single coherent answer.

Built with a multi-user CLI, cloud LLMs (Gemini, Groq), and cloud-hosted storage (Supabase + Qdrant).

---

## What Sage Does

- **Answers questions** from your saved documents and notes (RAG)
- **Searches the web and fetches live news** for current information
- **Creates and manages todos and reminders** with natural language due dates
- **Tracks habits** with streaks and weekly summaries
- **Triages your Gmail inbox** — classifies emails as ACTION, FYI, or IGNORE
- **Remembers facts** about you (personal and work) and injects them into every response
- **Supports multiple users** — each user's data is fully isolated

---

## Multi-Agent Architecture

Every message goes through a pipeline of specialized agents:

```
User Input
    │
    ▼
Orchestrator Agent  ← plans which agents to call and in what order
    │
    ├──▶ RAG Agent         — searches your personal documents
    ├──▶ Research Agent    — web search + live news
    ├──▶ Action Agent      — todos, habits, facts (read & write)
    └──▶ Conversational    — general chat and acknowledgements
    │
    ▼
Synthesizer         ← merges outputs into one natural reply
```

The orchestrator uses the LLM to decompose compound requests. For example:

> *"Search for LangGraph tutorials and remind me to study it tonight at 9pm"*

→ `research_agent` fetches tutorials → `action_agent` creates the todo → synthesizer combines both into one reply.

---

## Multi-User Support

Each user has an isolated account:

```
python3 -m app.main chat

Welcome to Sage
━━━━━━━━━━━━━━━━━━━━━━
[1] Login
[2] Sign up
[3] Exit

> 2
Username: sankalp
Password: ****
Confirm password: ****

Account created. Welcome, sankalp!
```

All data — sessions, facts, todos, habits, documents — is scoped to the logged-in user. No user can see another user's data.

---

## Features

### Chat & Memory
- **Persistent sessions** — resume any conversation with `--resume <session-id>`
- **Learned facts** — `/remember-personal` and `/remember-work` to store things Sage should always know about you
- **Automatic context** — your facts are injected into every response

### Document RAG
- Ingest `.txt`, `.md`, `.pdf` files — `sage ingest --path ./my-docs`
- Semantic search across all your documents
- Answers cite the exact source file

### Web & News
- **Live web search** — Tavily (primary), DuckDuckGo (fallback)
- **Live news** — `/news <topic>` or ask naturally
- Cited sources with URLs

### Todos & Habits
- Natural language reminders — *"remind me to call mom tomorrow at 3pm"*
- `/todo Buy milk @next Monday` — structured due dates
- Habit tracking with streaks and weekly summaries
- `/habit add`, `/habit log`, `/habits`

### Gmail Triage
- Connect your Gmail with `/configure email` — OAuth token stored per-user
- `/email` or *"check my email"* — classifies inbox as ACTION / FYI / IGNORE

### Analytics
- `/analytics` — sessions, turns, top commands, active hours

---

## Quick Start

### Prerequisites

- Python 3.11+
- A Groq or Gemini API key (model-agnostic — swap providers via env var)

### Install

```bash
git clone https://github.com/sankalpkulk06/chief-of-staff-agent.git
cd chief-of-staff-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

Create a `.env` file:

```env
# LLM Provider — pick one
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_CHAT_MODEL=llama-3.3-70b-versatile

# Or use Gemini
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=AIza...
# GEMINI_CHAT_MODEL=gemini-1.5-flash

# Embeddings
EMBEDDINGS_PROVIDER=ollama          # or gemini
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Web Search (optional — falls back to DuckDuckGo)
TAVILY_API_KEY=tvly-...

# Assistant name
ASSISTANT_NAME=Sage
```

### Run

```bash
python3 -m app.main chat
```

Sign up, then start chatting.

---

## Chat Commands

| Command | What it does |
|---------|-------------|
| `/help` | Show all commands |
| `/configure email` | Connect your Gmail (per-user OAuth) |
| `/configure status` | Show your account's configuration |
| `/remember-personal <fact>` | Save a personal fact |
| `/remember-work <fact>` | Save a work fact |
| `/facts [personal\|work]` | List stored facts |
| `/forget <fact-id>` | Delete a fact |
| `/email` | Triage your Gmail inbox |
| `/news [topic]` | Fetch live news |
| `/search <query>` | Search the web |
| `/todo <task> [@due]` | Add a reminder |
| `/habit add <name>` | Start tracking a habit |
| `/habit log <name>` | Mark a habit done today |
| `/habits` | Weekly habit summary |
| `/analytics` | Usage stats |
| `/sessions` | List recent sessions |
| `/session` | Show current session ID |
| `exit` | Quit |

---

## Model-Agnostic Design

Sage uses a provider abstraction — swap LLMs without touching any agent code:

```env
LLM_PROVIDER=groq     # uses Groq API
LLM_PROVIDER=gemini   # uses Google Gemini
LLM_PROVIDER=ollama   # local Ollama (dev/offline)
```

All agents call the same `LLMProvider` interface. The factory resolves the right implementation at startup.

---

## Environment Variables

```env
# LLM
LLM_PROVIDER=groq
GROQ_API_KEY=
GROQ_CHAT_MODEL=llama-3.3-70b-versatile
GEMINI_API_KEY=
GEMINI_CHAT_MODEL=gemini-1.5-flash

# Embeddings
EMBEDDINGS_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Storage (cloud)
STORAGE_BACKEND=sqlite          # sqlite | postgres
DATABASE_URL=                   # Supabase connection string (when postgres)
VECTOR_BACKEND=chroma           # chroma | qdrant
QDRANT_URL=                     # Qdrant Cloud URL
QDRANT_API_KEY=                 # Qdrant API key

# Web Search
TAVILY_API_KEY=
WEB_SEARCH_PROVIDER=tavily      # tavily | duckduckgo
WEB_SEARCH_MAX_RESULTS=5

# Retrieval
RETRIEVAL_TOP_K=5
CHUNK_SIZE=800
CHUNK_OVERLAP=120

# WhatsApp (optional)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=
TWILIO_DAILY_MESSAGE_LIMIT=50
WHATSAPP_ENABLED=false

# Assistant
ASSISTANT_NAME=Sage
APP_ENV=development
```

---

## Architecture

### Agent Pipeline

| Agent | Responsibility |
|-------|---------------|
| **Orchestrator** | Decomposes requests into steps, synthesizes final reply |
| **RAG Agent** | Semantic search over ingested documents |
| **Research Agent** | Web search (Tavily/DDG) and live news |
| **Action Agent** | Todos, habits, facts — all state-changing operations |
| **Conversational** | General chat with full history and fact injection |

### Storage

| Layer | Technology | What's stored |
|-------|-----------|--------------|
| Relational | SQLite (dev) / Supabase Postgres (prod) | Sessions, facts, todos, habits, users |
| Vector | ChromaDB (dev) / Qdrant Cloud (prod) | Document embeddings |
| Credentials | `data/credentials/{user_id}/` | Per-user Gmail OAuth tokens |

---

## Roadmap

### Done
- [x] Multi-agent orchestrator (Orchestrator → RAG, Research, Action, Conversational)
- [x] Multi-user login/signup with isolated data
- [x] Model-agnostic provider layer (Groq, Gemini, Ollama)
- [x] Per-user Gmail OAuth configuration
- [x] Document RAG (txt, md, pdf)
- [x] Web search and live news
- [x] Todos with natural language due dates
- [x] Habit tracker with streaks
- [x] Learned facts (personal and work)
- [x] Conversation analytics
- [x] WhatsApp integration (Twilio)

### Upcoming
- [ ] Cloud deployment (GCP Cloud Run)
- [ ] Migrate to Supabase (Postgres) + Qdrant Cloud
- [ ] REST API for third-party integrations
- [ ] Web dashboard UI
- [ ] Voice note transcription
- [ ] Proactive daily briefing

---

## License

MIT
