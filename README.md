# Sage - Personal Agent

A **local-first, privacy-respecting personal AI assistant** that combines retrieval-augmented generation (RAG) with persistent memory, live news, and intelligent conversation. Sage is your personal study companion with personality—answering questions about your documents, remembering what you tell it, fetching live news, and maintaining full conversation context.

**No cloud. No tracking. Everything stays on your machine.**

---

## ✨ Core Capabilities

### **1. Smart Chat with Tool Calling** 💬
- **Natural language understanding** — ask for things naturally without commands
- **Automatic tool invocation** — model decides when to fetch news, save facts, add todos
- **Persistent session history** — conversations are saved and can be resumed later
- **Multi-turn context** — LLM remembers everything said in the session
- **Resume any session** — `sage --resume <session-id>` to continue where you left off
- **Session management** — list, view, and organize chat sessions

### **2. Learned Facts** 🧠
- **Remember personal facts** — `/remember-personal Your favorite hobby is reading`
- **Remember work facts** — `/remember-work I lead the ML team`
- **Smart categorization** — personal and work facts organized separately
- **Automatic context** — facts are automatically injected into responses
- **Manage facts** — view all facts, delete unwanted ones, organize by category

### **3. Selective RAG Retrieval** 🎯
- **Conversational questions** (greetings, meta-questions) — skip document retrieval entirely
- **Document-based questions** — retrieve and cite sources only when needed
- **Smart pattern detection** — knows when to use docs vs. just chat
- **Fast responses** — no embedding latency for casual conversation

### **4. Live News Fetching** 📰
- **Natural language news queries** — "What is the news on NASA today?"
- **Direct news command** — `/news SpaceX` for instant formatted news
- **AI-generated summaries** — get a consolidated summary (under 200 words) of all articles
- **Topic extraction** — automatically extracts topics from questions
- **Full sentence search** — `/news What happened with the Mars launch` works perfectly
- **Persistent news context** — follow-up questions remember the articles
- **Proper citations** — news sources cited separately from documents

### **5. Sage Reminders & Todos** ✅
- **Sage-owned reminders** — `/todo Buy oat milk` stores a reminder in SQLite
- **Natural language reminders** — "remind me to take the trash out at 8PM today"
- **Proactive WhatsApp delivery** — `sage serve` schedules and sends due reminders
- **Natural language due dates** — `/todo Buy milk @tomorrow` or `/todo Call mom @next Tuesday at 3pm`
- **Missed reminder recovery** — a fallback scanner catches reminders missed during restarts
- **Apple Reminders opt-in** — `/apple-reminder ...` only writes to macOS Reminders when explicitly requested

### **6. Document Management** 📚
- **Multi-format support** — `.txt`, `.md`, `.pdf` files
- **Bulk ingestion** — `sage ingest --path ./documents/`
- **Metadata tracking** — file size, type, date, checksums
- **Vector embeddings** — semantic search across all documents
- **Citation system** — all answers cite exact document sources

### **7. Personality-Driven Responses** 🎭
- **Named assistant** — "You are Sage — a wise, knowledgeable personal companion"
- **Natural tone** — conversational, not robotic
- **Context-aware** — different response style for personal facts vs. document answers
- **Thoughtful advice** — like a trusted advisor, not a search engine

### **8. Conversation Analytics** 📊
- **Usage patterns** — track total sessions, turns, and conversation frequency
- **Activity insights** — discover your most active days and times
- **Command statistics** — see which commands you use most
- **Topic analysis** — understand your top question types
- **Fact insights** — review learned facts by category
- **Dashboard view** — `/analytics` command for visual stats

### **9. Web Search** 🌐
- **Live web answers** — ask factual questions and get current, cited answers from the web
- **Automatic routing** — LLM decides when to use web search vs. RAG vs. news based on intent
- **Tavily primary** — fast, LLM-optimized results (1,000 searches/month free)
- **DuckDuckGo fallback** — works without an API key
- **Cited responses** — every web result includes title and URL
- **Explicit shortcut** — `/search <query>` to always hit the web directly

### **10. WhatsApp Integration** 💬
- **Chat over WhatsApp** — send any message to Sage from your phone via the Twilio sandbox
- **Persistent sessions** — same phone number always resumes the same conversation
- **All commands work** — `/todo`, `/news`, `/search`, `/remember-*`, natural language — everything available in chat
- **Long reply splitting** — responses over 1600 chars automatically split across multiple messages at sentence boundaries
- **`sage serve` command** — starts a FastAPI webhook server Twilio forwards messages to
- **ngrok-friendly** — works locally with ngrok for dev; any public HTTPS endpoint for prod
- **Twilio usage tracking** — `/usage` shows daily CLI chats, WhatsApp chats, and outbound Twilio quota

### **11. Proactive Briefings & Habit Nudges** ⏰
- **Morning briefing** — daily WhatsApp summary of habits, news, and due reminders
- **Habit nudges** — asks about unlogged habits at the configured nudge time
- **Fast nudge replies** — reply `done` or `skipped` to log a habit without going through the LLM
- **Scheduler lives in `sage serve`** — proactive behavior runs while the webhook server is up

### **12. Gmail Email Triage** 📧
- **Fetch personal inbox** — pulls recent primary-tab emails via Gmail API
- **AI-powered triage** — classifies each email as ACTION, FYI, or IGNORE
- **Actionable summary** — surfaces what needs a reply or decision
- **Chat-native** — type `/email` or just ask naturally ("check my email", "any emails to action?")
- **Standalone command** — `sage email-personal` for a quick triage outside chat
- **Filters automatically** — skips promotions, social, updates, and forums

### **13. Configuration & Customization** ⚙️
- **Environment variables** — all settings configurable via `.env`
- **Custom assistant name** — change who you're talking to
- **Retrieval depth** — adjust how many documents to retrieve (in-session)
- **Chunk size & overlap** — fine-tune document chunking
- **Max results** — control news and email result counts

---

## 🤖 How It Works: Open Source Tool Calling

Sage uses **open source Ollama models with tool calling** — the model understands when to call tools based on natural language, without needing explicit commands.

**The Flow:**
1. **You ask naturally:** "What's the news on Tesla?" or "Remember that I like coffee"
2. **Model understands intent:** Identifies that it needs to fetch news, search the web, save a fact, etc.
3. **Automatically calls tools:** Generates JSON with the tool name and parameters
4. **Tools execute:** Fetches news, searches the web, saves facts, adds Sage todos, searches documents
5. **Model responds:** Incorporates tool results into a natural response

**No `/commands` required** — but they still work as shortcuts if you prefer them.

Example:
```
you: What is LangGraph and can you add it to my reminders?
Sage: I'll search the web and add a reminder.
[Tool: web_search(query="What is LangGraph")]
[Tool: add_todo(task="Learn LangGraph", due_date="today")]
→ According to [1], LangGraph is a framework for building stateful multi-agent workflows...

web sources:
- [1] LangGraph Docs — https://langchain-ai.github.io/langgraph/
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Ollama installed and running
- 2GB+ RAM available

### Installation

```bash
git clone <repo-url>
cd personal-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Start Ollama

```bash
ollama serve
```

In another terminal, pull the models:

```bash
ollama pull nomic-embed-text      # Embeddings (small & fast)
ollama pull llama3.2:3b           # Chat model (lightweight)
```

### First Steps

```bash
# Show configuration
sage config

# Ingest your documents
sage ingest --path "./my-documents"

# Start chatting!
sage chat
```

### Docker / Keep Sage Running

Use Docker Compose to keep the WhatsApp webhook server, scheduler, and optional Ollama service running in the background.

```bash
# Build the Sage image
docker compose build

# Start Sage + Ollama in the background
docker compose up -d

# Pull the required models into the Ollama container
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b

# Watch logs
docker compose logs -f sage

# Stop services
docker compose down
```

Your Sage state is mounted at `./data`, including SQLite, Chroma, reports, and Gmail OAuth tokens.

If you already run Ollama on your Mac instead of in Docker:

```bash
docker compose -f docker-compose.host-ollama.yml up -d --build
```

Useful one-off commands:

```bash
docker compose run --rm sage sage config
docker compose run --rm sage sage sources
docker compose run --rm sage sage ingest --path /app/data/documents
docker compose run --rm sage sage chat
```

Note: Linux containers cannot create native macOS Apple Reminders. Sage-owned SQLite reminders, WhatsApp delivery, Gmail triage, RAG, URL ingestion, and webhook serving still work in Docker.

---

## 💬 Interactive Chat Mode

### Starting Chat

```bash
# New session
sage chat

# Resume a previous session
sage chat --resume <session-id>
sage --resume <session-id>  # Shortcut
```

### Chat Commands

| Command | Purpose |
|---------|---------|
| `/help` | Show all commands |
| `/session` | Display current session ID |
| `/sessions` | List recent chat sessions |
| `/analytics` | View usage statistics and patterns |
| `/topk <n>` | Set retrieval depth for this session |
| `/remember-personal <fact>` | Save a personal fact |
| `/remember-work <fact>` | Save a work fact |
| `/facts [personal\|work]` | List learned facts |
| `/forget <fact-id>` | Delete a fact |
| `/email` | Check personal email and triage action items |
| `/news [query]` | Fetch live news |
| `/search <query>` | Search the web for current information |
| `/usage` | Show today's CLI chats, WhatsApp chats, and Twilio outbound usage |
| `/todo <task> [#list] [@due]` | Add a Sage reminder stored in SQLite |
| `/apple-reminder <task> [#list] [@due]` | Add a task to Apple Reminders explicitly |
| `exit` or `quit` | Exit chat |

### Usage Examples

**Learn about yourself:**
```
you : /remember-personal I live in NYC
✓ Personal fact saved: I live in NYC

you : /remember-work I'm a software engineer
✓ Work fact saved: I'm a software engineer

you : where do I live?
assistant
You live in NYC.
```

**Fetch news with AI-generated summary:**
```
you : /news Tesla
━━━━━━ News: Tesla ━━━━━━

📋 Summary
Tesla's latest developments include advances in autonomous driving capabilities, expanding production facilities globally, and strategic partnerships in the EV market. The company continues to lead in battery technology innovation while facing competition from traditional automakers entering the electric vehicle space. Recent quarterly earnings show strong growth despite market volatility.

📰 Articles
[1] Tesla releases next-gen Model improvements
    Reuters | 2026-04-08
    https://news.google.com/...

[2] Elon Musk announces new Gigafactory plans
    Bloomberg | 2026-04-07
    https://news.google.com/...

[3] Tesla stock surges on strong earnings report
    CNBC | 2026-04-06
    https://news.google.com/...
```

**Chat with persistence:**
```
you : What is the news on SpaceX today?
# Fetches live articles, displays summary, injects into context

you : where was it launched from?
assistant
According to the latest news [1], SpaceX launched from...
news sources:
- [1] SpaceX launches Falcon 9... — Reuters

you : /sessions
# Resume this session later!
```

**Search documents:**
```
you : what did I write about machine learning?
assistant
According to your notes [1], machine learning is...
document sources:
- [1] machine-learning-notes.md
```

**Capture Sage reminders:**
```
you : /todo Buy oat milk
✓ Added Sage reminder: Buy oat milk

you : /todo Buy groceries #Shopping
✓ Added Sage reminder: Buy groceries

you : /todo Call mom @tomorrow
✓ Added Sage reminder: Call mom due Wed, Apr 08 at 12:00AM

you : remind me to take the trash out at 8PM today
✓ Added Sage reminder: take the trash out due Thu, Apr 30 at 08:00PM
```

**Add to Apple Reminders only when explicit:**
```
you : /apple-reminder Buy printer paper #Office @tomorrow
✓ Added Apple Reminder to Office: Buy printer paper due Fri, May 01 at 12:00AM

you : add renew passport to Apple Reminders for next Tuesday at 3pm
✓ Added to Apple Reminders Reminders: renew passport due Tue, May 05 at 03:00PM
```

**Check usage:**
```
you : /usage
Usage today
CLI chats: 3
WhatsApp chats: 7
Twilio WhatsApp outbound before this reply: 12/50 messages used, 38 remaining.
```

**Search the web:**
```
you : /search What is LangGraph?
━━━━━━ Web Search: What is LangGraph? ━━━━━━

[1] What is LangGraph - GeeksforGeeks
    https://www.geeksforgeeks.org/machine-learning/what-is-langgraph/
    LangGraph is a library for building stateful, multi-actor applications with LLMs...

[2] LangGraph: Agent Orchestration Framework — LangChain
    https://www.langchain.com/langgraph
    LangGraph is a low-level orchestration framework for building agents...

# Or ask naturally — Sage picks the right tool automatically:
you : Who won the 2024 US election?
# → Sage calls web_search automatically, returns a cited answer
```

**Triage your email:**
```
you : check my email
╭─ Personal Email ─╮

ACTION NEEDED (2)
  1. boss@company.com — Q2 planning doc needs your sign-off
     → Requires a decision or reply from you
  2. recruiter@startup.io — Following up on intro call
     → Awaiting your response

FYI (3)
  1. github@github.com — Your PR was merged
     → Informational, no action needed
  ...

╰──────────────────╯

# Works with /email too
you : /email
```

**Manage facts:**
```
you : /facts work
[1] I'm a software engineer
    a1b2c3d4... | 2026-04-07

[2] I lead the ML team
    b2c3d4e5... | 2026-04-06

you : /forget a1b2c3d4
✓ Fact forgotten
```

**View conversation analytics:**
```
you : /analytics
╭─ Analytics Dashboard ─╮

📊 Conversation Overview
  Total Sessions:         12
  Total Turns:            156
  Avg Turns per Session:  13.0
  Longest Session:        34 turns

📈 Activity Patterns
  First Session:          2026-03-20
  Last Session:           2026-04-08
  Days Active:            15
  Sessions per Day:       0.80
  Most Active Day:        Tuesday (3 sessions)
  Most Active Hour:       14:00

⚡ Top Commands
  /news                  24 times
  /todo                  18 times
  /facts                 12 times

💬 Top Question Words
  what                   28 times
  how                    15 times
  why                    8 times

🧠 Learned Facts by Category
  personal               8 facts
  work                   5 facts

╰──────────────────────╯
```

---

## 🛠️ CLI Commands

### Configuration

```bash
sage config
# Output: Shows all current settings
```

### Document Ingestion

```bash
# Ingest a single file
sage ingest --path "./document.pdf"

# Ingest entire directory
sage ingest --path "./documents/"

# Both .md, .txt, .pdf are supported
```

### Single Question Mode

```bash
# Ask a question and exit
sage ask "What are the key concepts in distributed systems?"

# Override retrieval depth
sage ask "Summarize my notes" --top-k 10

# Export answer to Markdown
sage ask "What did I learn?" --export
```

### Interactive Chat

```bash
sage chat                    # New session
sage chat --top-k 7          # Custom retrieval depth
sage chat --resume <id>      # Resume session
sage --resume <id>           # Quick resume
```

### WhatsApp Webhook Server

```bash
# Start the webhook server (default port 8000)
sage serve

# Custom port
sage serve --port 9000

# With auto-reload for development
sage serve --reload
```

### Email Triage

```bash
# Fetch and triage personal inbox
sage email-personal

# Skip AI triage, just list emails
sage email-personal --no-triage

# Limit results
sage email-personal --max-results 10
```

### Sage Reminders, Apple Reminders, and Usage

In chat mode, use `/todo` to add Sage-owned reminders to SQLite. These reminders are sent over WhatsApp by `sage serve` when due. Apple Reminders is explicit opt-in via `/apple-reminder`.

```bash
# Basic Sage reminder
/todo Buy milk

# Optional Sage list/category label
/todo Buy groceries #Shopping
/todo Pay utilities #Bills
/todo Review PR #Work

# Add with due date
/todo Call mom @tomorrow
/todo Meeting @next Tuesday at 3pm
/todo Workout @3pm

# Natural language reminder
remind me to take the trash out at 8PM today
remind me to call mom tomorrow morning

# Combine list and due date
/todo Dinner prep #Personal @6pm
/todo Project deadline #Work @next Friday
/todo Anniversary #Important Dates @April 15

# Explicit Apple Reminders
/apple-reminder Buy printer paper #Office @tomorrow
```

**List syntax:** Use `#ListName` to attach a list/category label. For `/todo`, this is stored in Sage's SQLite todo table. For `/apple-reminder`, this chooses the Apple Reminders list.

**Date patterns:**
- **Relative:** today, tomorrow, tonight, next Monday, next week, etc.
- **Specific dates:** April 15, 2026-04-20, April 15 at 9am
- **Times:** 3pm, 9:30am, 14:45, etc.
- **Combinations:** next Tuesday at 3pm, April 20 at 6:30pm

**Usage tracking:** `/usage` reports daily CLI chats, WhatsApp chats, and outbound Twilio WhatsApp messages. The Twilio count is outbound-only because inbound WhatsApp webhooks are tracked separately as chat activity.

---

## 📧 Gmail Setup

Email triage requires a Google Cloud OAuth 2.0 credential.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials**
2. Create an **OAuth 2.0 Client ID** (Desktop app)
3. Download the JSON and save it as `data/credentials/credentials.json`
4. On first run (`sage email-personal` or `/email` in chat), a browser window opens for Google sign-in
5. The token is saved to `data/credentials/personal_token.json` — future runs are silent

**Required API:** Enable **Gmail API** in your Google Cloud project.

---

## 📱 WhatsApp Setup

Requires a free [Twilio](https://twilio.com) account and [ngrok](https://ngrok.com) for local dev.

1. **Twilio** — grab your **Account SID** and **Auth Token** from the [Console dashboard](https://console.twilio.com/console). Activate the WhatsApp Sandbox under **Messaging → Try it out → Send a WhatsApp message** and note your sandbox number.

2. **Add to `.env`:**
   ```env
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
   TWILIO_DAILY_MESSAGE_LIMIT=50
   ```

3. **Join the sandbox** — from WhatsApp, send `join <sandbox-name>` (shown on the Twilio page) to `+1 415 523 8886`.

4. **Start the server and expose it:**
   ```bash
   # Terminal 1
   sage serve

   # Terminal 2
   ngrok http 8000
   ```

5. **Wire up Twilio** — in the Twilio sandbox settings, set **"When a message comes in"** to:
   ```
   https://xxxx.ngrok-free.app/webhook   (HTTP POST)
   ```

6. **Send a message** from WhatsApp to the sandbox number — Sage replies.

### Proactive Scheduler Setup

Proactive reminders, habit nudges, and morning briefings run inside `sage serve`. Keep that process running for scheduled WhatsApp messages.

Add these to `.env`:

```env
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
HABIT_NUDGE_TIME=21:00
YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234
```

Scheduler behavior:
- One-time Sage reminders use APScheduler `date` jobs.
- Pending future reminders are reloaded from SQLite on startup.
- A one-minute fallback scanner catches reminders missed during downtime.
- If Twilio rejects a send, Sage logs the failure and does not mark the reminder as notified.
- Usage alerts are sent once per day when outbound Twilio usage reaches 25, 45, and 49 messages. The 49-message alert consumes one message and effectively brings Twilio usage to 50/50.

---

## ⚙️ Configuration

### Environment Variables (`.env`)

```env
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=llama3.2:3b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=120

# Retrieval
RETRIEVAL_TOP_K=5
NEWS_MAX_RESULTS=5
EMAIL_MAX_RESULTS=20

# Web Search
TAVILY_API_KEY=              # Optional — falls back to DuckDuckGo if not set
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROVIDER=tavily  # "tavily" | "duckduckgo"

# WhatsApp (Twilio)
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TWILIO_DAILY_MESSAGE_LIMIT=50
WEBHOOK_PORT=8000
WHATSAPP_ENABLED=true

# Apple Reminders (explicit opt-in only)
REMINDERS_DEFAULT_LIST=Reminders

# Scheduler / Proactive Reminders
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
HABIT_NUDGE_TIME=21:00
YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234

# Personality
ASSISTANT_NAME=Sage

# Storage
DATA_DIR=./data
```

### Recommended Ollama Models

**For Tool Calling (recommended):**
```bash
ollama pull llama3.1:8b           # Excellent tool calling, good speed
ollama pull mistral:7b            # Very good tool calling, fast
```

**For Speed (lightweight, but less reliable tool calling):**
```bash
ollama pull llama3.2:3b           # Smaller, but tool calling less reliable
ollama pull nomic-embed-text      # 274MB — fast embeddings
```

**Note:** Smaller models (3b) have lower tool-calling accuracy. For best results with tool calling, use `llama3.1:8b` or `mistral:7b` (requires ~8GB RAM).

**For Embeddings:**
```bash
ollama pull nomic-embed-text      # Fast and effective
ollama pull all-minilm:22m        # Better quality, smaller than others
```

---

## 📊 How It Works

### Conversation Flow

```
User Input
    ↓
Pattern Detection
    ├─ Conversational? (greeting, meta-question)
    │  └→ No RAG retrieval, just chat
    ├─ /search <query>?
    │  └→ Always web search, display results
    ├─ /news <topic>?
    │  └→ Always NewsService, display articles
    └─ Everything else → LLM with tools available
    ↓
Tool-Calling Loop (LLM decides)
    ├─ web_search  → live web results (🌐)
    ├─ fetch_news  → live news articles (📰)
    ├─ add_todo    → Sage SQLite reminders
    ├─ add_apple_reminder → Apple Reminders (explicit only)
    ├─ remember_fact / list_facts → personal memory
    └─ search_documents → RAG retrieval (📄)
    ↓
Inject Context
    ├─ Learned facts (personal + work)
    ├─ Tool results (web, news, documents)
    └─ Conversation history
    ↓
LLM Response (Ollama)
    ↓
Display with Citations
    ├─ Web sources (🌐)
    ├─ News sources (📰)
    └─ Document sources (📄)
    ↓
Save to Session
```

### Session Persistence

```
Session created → Turns stored in SQLite
├─ Turn 1: User question
├─ Turn 2: Assistant answer
├─ Turn 3: User follow-up
└─ ...
    ↓
Later: Resume session
├─ Load all turns
├─ Re-inject context
└─ Continue conversation
```

---

## 🎯 Real-World Examples

### Scenario 1: Research with Follow-ups

```
you : What is the news on climate change today?
# Fetches 5 articles about climate change

you : What are the main solutions mentioned?
# Re-uses cached articles, LLM answers in context

you : Tell me about renewable energy
# Switches to document search (new topic)
# Clears news cache, does RAG
```

### Scenario 2: Personal Knowledge Base

```
you : /remember-personal I have a dog named Max
you : /remember-personal Max's birthday is July 15

you : When is Max's birthday?
# Sage remembers from learned facts

you : /facts personal
# Lists all personal facts with dates
```

### Scenario 3: Work Context

```
you : /remember-work I work on the payment system
you : /remember-work My team has 4 people

you : Tell me about payment systems in the book
# Sage knows you work on payments, includes in context
```

---

## 📈 Architecture

### Core Components

| Component | Purpose |
|-----------|---------|
| **ChatService** | Manages sessions, routing, context injection |
| **WebSearchService** | Live web search via Tavily or DuckDuckGo |
| **WhatsAppService** | Twilio send wrapper with message splitting |
| **FactService** | Stores and retrieves learned facts |
| **NewsService** | Fetches live news from Google News RSS |
| **EmailService** | Gmail fetch and AI triage |
| **Scheduler** | APScheduler jobs for Sage reminders, habit nudges, and briefings |
| **RemindersService** | Explicit Apple Reminders integration |
| **Webhook Server** | FastAPI server receiving Twilio webhooks |
| **Retriever** | RAG retrieval with embeddings |
| **OllamaChatProvider** | LLM interface to Ollama |
| **SQLiteRegistry** | Persists sessions, facts, reminders, WhatsApp sessions, and usage |
| **ChromaStore** | Vector database for embeddings |

### Data Storage

```
data/
├── sqlite/registry.db         # Sessions, facts, reminders, usage, metadata
├── chroma/                    # Vector embeddings
├── cache/                     # Temporary files
└── credentials/               # OAuth tokens (Gmail)
    ├── credentials.json        # Google Cloud client secret (you provide)
    └── personal_token.json     # Auto-generated after first Gmail auth
```

---

## 🚀 Performance

### Typical Response Times

| Query Type | Time | Notes |
|-----------|------|-------|
| Conversational | <100ms | No RAG overhead |
| Web search | 1-3s | Tavily or DuckDuckGo + LLM |
| News query | 2-3s | Web fetch + LLM |
| Document search | 1-2s | Embedding + retrieval + LLM |
| Follow-up (cached news) | 1-2s | Uses cached articles |

### Memory Usage

- **Base system**: ~300MB
- **With 1 model loaded**: ~800MB
- **With large documents**: +500MB per 100MB docs

---

## 🔧 Troubleshooting

### Ollama not connecting

```bash
# Check if Ollama is running
curl http://localhost:11434/api/version

# Start Ollama if not running
ollama serve
```

### No embeddings error

```bash
# Ensure embedding model is pulled
ollama pull nomic-embed-text

# Check it's available
ollama list
```

### Out of memory

- Use smaller model: `mistral:7b` instead of `llama2:13b`
- Reduce `CHUNK_SIZE` in `.env`
- Free up system RAM

### Gmail credentials not found

```bash
# Ensure credentials.json is in place
ls data/credentials/credentials.json

# On first run, a browser window opens for Google sign-in
sage email-personal
```

If the browser doesn't open, check that your Google Cloud project has the Gmail API enabled and the OAuth client is a Desktop app type.

### Chat feels slow

- Reduce `RETRIEVAL_TOP_K` in `.env` (default: 5)
- Use `/topk 3` in chat to retrieve fewer documents
- Switch to faster model in Ollama

---

## 📚 Settings Reference

### Environment Variables

```env
# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=llama3.2:3b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Document Chunking
CHUNK_SIZE=800              # Characters per chunk
CHUNK_OVERLAP=120           # Overlap between chunks

# Retrieval
RETRIEVAL_TOP_K=5           # Documents to retrieve
NEWS_MAX_RESULTS=5          # News articles to fetch
EMAIL_MAX_RESULTS=20        # Emails to fetch per triage

# Web Search
TAVILY_API_KEY=             # Get a free key at app.tavily.com (1,000 req/month)
WEB_SEARCH_MAX_RESULTS=5    # Max results per search
WEB_SEARCH_PROVIDER=tavily  # "tavily" | "duckduckgo" (fallback if key absent)

# Apple Reminders (explicit opt-in only)
REMINDERS_DEFAULT_LIST=Reminders  # Default list for /apple-reminder

# WhatsApp / Scheduler
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TWILIO_DAILY_MESSAGE_LIMIT=50
WEBHOOK_PORT=8000
WHATSAPP_ENABLED=true
SCHEDULER_ENABLED=true
MORNING_BRIEFING_TIME=08:00
HABIT_NUDGE_TIME=21:00
YOUR_WHATSAPP_NUMBER=whatsapp:+14155551234

# Personality
ASSISTANT_NAME=Sage        # Your assistant's name
APP_ENV=development

# Storage
DATA_DIR=./data             # Where to store data
```

---

## 🎯 Features Roadmap

### ✅ Completed
- [x] Chat with session history
- [x] Learned facts (personal/work)
- [x] Live news fetching
- [x] Selective RAG routing
- [x] Document management
- [x] Conversation context
- [x] Source citations
- [x] Apple Reminders integration
- [x] Sage-owned reminders with WhatsApp scheduling
- [x] Habit tracker with streaks and reminders
- [x] Proactive morning briefings via WhatsApp
- [x] WhatsApp/CLI usage tracking
- [x] Conversation analytics dashboard
- [x] Gmail email triage (personal inbox)
- [x] Web search (Tavily + DuckDuckGo fallback)
- [x] WhatsApp integration (text messages via Twilio)

### 🚀 Upcoming
- [ ] Voice notes over WhatsApp (Whisper transcription)
- [ ] Fact verification against documents
- [ ] Automatic fact extraction from responses
- [ ] Semantic fact linking & inference
- [ ] Chat history search
- [ ] Web API server mode
- [ ] Dashboard UI
- [ ] Batch processing
- [ ] Multi-user support

---

## 💡 Tips & Tricks

### Optimize for Your Use Case

**For research:**
```bash
sage config
# Increase RETRIEVAL_TOP_K to 10 in .env
sage chat --top-k 10
```

**For quick answers:**
```bash
# Use single question mode
sage ask "Quick answer?"
```

**For casual chat:**
```bash
# Session mode remembers everything
sage chat
sage --resume <id>
```

### Best Practices

- **Regular ingestion** — keep your documents up to date
- **Organized facts** — use `/remember-personal` and `/remember-work` to keep knowledge organized
- **Session management** — `/sessions` to find relevant past conversations
- **Topic switching** — start a new session for different topics
- **Fact cleanup** — use `/forget` to remove outdated facts

---

## 📝 License

[Add your license here]

---

## 🙋 Contributing

[Add contribution guidelines]

---

## 📧 Support

For issues, feature requests, or questions:
- Check the [Troubleshooting](#-troubleshooting) section
- Review existing settings in `.env`
- Ensure Ollama is running and models are pulled

---

**Made with ❤️ for your personal knowledge base.**
