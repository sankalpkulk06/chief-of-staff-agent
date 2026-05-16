# Sage — Product Overview

## What Is Sage?

Sage is a local-first, privacy-respecting personal AI assistant that runs entirely on your machine. It combines retrieval-augmented generation (RAG), persistent memory, live news, intelligent conversation, habit tracking, and proactive nudges — all without sending your data to any cloud.

**Core promise:** No cloud. No tracking. Everything stays on your machine.

Sage is designed as a personal study companion and life assistant. It answers questions about your documents, remembers personal facts you share, fetches live news, maintains conversation context across sessions, and proactively reminds you of todos and habits.

---

## Who Is Sage For?

Sage is a single-user personal tool. It is not a SaaS product and does not support multi-user accounts. The target user is someone who:

- Values privacy and wants their AI assistant to run locally
- Wants a unified interface for chat, documents, reminders, habits, and news
- Uses WhatsApp daily and wants their AI assistant there too
- Is technical enough to run a Python CLI or Docker container

---

## Current State (as of April 2026)

Sage has completed two full development waves and is in active use. The core product is feature-complete for personal productivity use.

---

## Features Built

### Smart Chat
- Natural language conversations with persistent session history
- Resume any previous session with `sage --resume <session-id>`
- Multi-turn context — Sage remembers what was said earlier in the conversation
- LLM-powered tool invocation: Sage decides when to search, fetch news, or look up facts

### Document Knowledge Base
- Ingest `.txt`, `.md`, and `.pdf` files into a local vector database
- Semantic search: ask questions, get answers with cited sources
- Automatic URL ingestion — paste a link in chat and Sage scrapes, chunks, and embeds the article
- `/sources` command lists all ingested URLs

### Learned Facts
- `/remember-personal` and `/remember-work` to teach Sage things about you
- Facts are auto-injected into responses when relevant
- `/facts` to view all stored facts, `/forget` to delete one

### Live News
- Ask "What's the news on [topic] today?" in natural language
- `/news` command with AI-generated summaries
- Persistent news context so you can follow up on articles

### Web Search
- Live web answers with cited sources
- Tavily API (primary) with DuckDuckGo as a free fallback
- Automatic routing based on intent; `/search` as an explicit shortcut

### Reminders & Todos
- `/todo` with natural language dates ("@tomorrow", "@next Tuesday at 3pm")
- Proactive WhatsApp delivery when a todo is due
- Missed reminder recovery on startup

### Habit Tracker
- `/habit add`, `/habit log`, `/habit delete`
- Streak tracking (consecutive days completed)
- Weekly summary with visual progress bars
- Natural language habit logging ("I did my workout")

### Proactive Briefings
- Morning briefing via WhatsApp: habits + news + pending todos
- Habit nudges at configured times
- Quick reply with "done" or "skipped"

### WhatsApp Integration
- Full Sage experience over WhatsApp via Twilio
- Persistent sessions tied to phone number
- All commands and natural language queries work over WhatsApp
- Long responses automatically split into multiple messages
- `sage serve` starts the webhook server

### Gmail Email Triage
- Fetch and classify your Gmail inbox
- AI labels each email: ACTION / FYI / IGNORE
- Actionable summaries with no noise from promotions or social
- `/email` command or natural language trigger

### Conversation Analytics
- `/analytics` shows usage patterns, active hours, command stats, topic analysis

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| LLM | Ollama (local — llama3.2:3b / llama3.1:8b) |
| Embeddings | Ollama nomic-embed-text |
| Vector DB | ChromaDB (persistent, local) |
| Database | SQLite |
| Web Server | FastAPI + Uvicorn |
| Messaging | Twilio (WhatsApp) |
| Scheduler | APScheduler |
| Web Scraping | BeautifulSoup4 + httpx |
| Search | Tavily API + DuckDuckGo fallback |
| Email | Gmail API + OAuth 2.0 |
| CLI | Typer + Rich |
| Deployment | Docker + Docker Compose |

---

## Architecture

```
User Input (CLI / WhatsApp)
        │
        ▼
   ChatService
   ├── Pattern detection (conversational vs. factual vs. command)
   ├── Tool calling orchestration
   └── Session + context management
        │
        ▼
   Tools (invoked by LLM)
   ├── search_documents     → ChromaDB semantic search
   ├── fetch_news          → Google News RSS
   ├── web_search          → Tavily / DuckDuckGo
   ├── add_todo            → SQLite todos
   ├── remember_fact       → SQLite facts
   ├── log_habit           → SQLite habit_logs
   ├── classify_email      → Gmail API
   └── ingest_url          → Scrape → Chunk → Embed
        │
        ▼
   LLM (Ollama, local)
   └── Generates final response with citations
        │
        ▼
   Delivery
   ├── CLI: Rich formatted output
   └── WhatsApp: Twilio, split at 1600 chars

Persistence
   ├── SQLite (registry.db)
   │   ├── sessions, facts, todos, habits, habit_logs
   │   ├── documents (metadata + URL sources)
   │   └── whatsapp_sessions, usage stats
   └── ChromaDB (/data/chroma/)
       └── Document + URL vector embeddings
```

---

## What Sage Is Not

- Not a SaaS or web app (no hosted version, no login)
- Not multi-user
- Not connected to any cloud LLM by default
- No browser extension or GUI dashboard (CLI + WhatsApp only)
- Does not auto-send emails — all email actions require user approval

---

## What's Next (Wave 3 — Planned)

Wave 3 will add agentic orchestration: a ReAct-loop orchestrator that can plan and execute multi-step tasks using specialized sub-agents.

Planned sub-agents:
- **EmailAgent** — classify urgency, draft replies, summarize threads
- **CalendarAgent** — Google Calendar read/write
- **SearchAgent** — wraps existing web search
- **MemoryAgent** — ChromaDB + SQLite facts lookup
- **ProductivityAgent** — todos, habits, reminders

Planned additions:
- Live step streaming to WhatsApp ("Searching the web… Checking your calendar… Done.")
- User confirmation gates before destructive actions (e.g., sending a draft)
- Tool registry for dynamic agent capability discovery

---

## Deployment

Sage runs via Docker or directly via Python:

```bash
# Docker
docker compose up -d

# Or directly
pip install -e .
sage chat
sage serve   # starts WhatsApp webhook
```

All data lives in `/data/` — SQLite DB, ChromaDB embeddings, and OAuth credentials. No external state.
