# Sage — Feature PRD v3
### Agentic Orchestration with ReAct Loop

**Status:** Planned  
**Author:** Sankalp  
**Last Updated:** April 2026  
**Builds on:** PRD v1 (WhatsApp · Habits) · PRD v2 (URL Ingestion · News · Review)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [The Orchestrator Agent](#the-orchestrator-agent)
4. [Sub-Agents](#sub-agents)
   - [EmailAgent](#emailagent)
   - [CalendarAgent](#calendaragent)
   - [SearchAgent](#searchagent)
   - [MemoryAgent](#memoryagent)
   - [ProductivityAgent](#productivityagent)
5. [ReAct Loop Design](#react-loop-design)
6. [Live Step Streaming](#live-step-streaming)
7. [Tool Registry](#tool-registry)
8. [Example Task Walkthroughs](#example-task-walkthroughs)
9. [Cross-Feature Dependencies](#cross-feature-dependencies)
10. [Build Order](#build-order)
11. [Out of Scope](#out-of-scope)

---

## Overview

The goal of this PRD is to evolve Sage from a tool-calling chatbot into a **genuine agentic orchestrator** — one that can receive a multi-step goal, decompose it into subtasks, spawn specialized sub-agents, execute steps autonomously using a ReAct loop, and report back with results.

### The Motivating Example

```
you: Check my email, find anything urgent, and remind me tonight to reply.

Sage: 🤖 Starting task...

  [Thought]  I need to: 1) read emails, 2) classify urgency, 3) set a reminder
  [Act]      EmailAgent → fetch_unread_emails(limit=20)
  [Observe]  Found 20 emails. Scanning...
  [Act]      EmailAgent → classify_urgency(emails)
  [Observe]  3 urgent: recruiter follow-up, landlord, bank alert
  [Act]      ProductivityAgent → create_todo("Reply to urgent emails", due="9pm today")
  [Observe]  Todo created. Reminder scheduled for 9pm via WhatsApp.

  ✅ Done! Here's what I found:

  🚨 Urgent emails (3):
    • recruiter@anthropic.com — "Following up on your application"
    • landlord@property.com — "Rent payment confirmation needed"
    • alerts@chase.com — "Unusual activity on your account"

  ⏰ Reminder set for 9pm tonight to reply to these.
```

This is not a single tool call — it's a **plan → act → observe → repeat** loop across multiple agents, with live progress shown as it runs.

---

## Architecture

### System Diagram

```
User Message (WhatsApp / CLI)
          │
          ▼
  ┌───────────────────┐
  │  ChatService      │  ← detects if task is agentic or simple
  └───────────────────┘
          │
    agentic? ──yes──▶  ┌─────────────────────────┐
          │             │   OrchestratorAgent      │
    no    │             │   (ReAct loop)           │
          │             └────────────┬────────────┘
          ▼                          │ spawns
   Normal response            ┌──────▼──────┐
                               │ Sub-Agents  │
                               ├─────────────┤
                               │ EmailAgent  │──▶ Gmail API
                               │ CalendarAgent──▶ Google Calendar API
                               │ SearchAgent │──▶ Web Search
                               │ MemoryAgent │──▶ ChromaDB / SQLite
                               │ ProductivityAgent──▶ Todos / Habits
                               └─────────────┘
                                      │
                               ┌──────▼──────┐
                               │   Results   │
                               └──────┬──────┘
                                      │
                              streamed live to
                              WhatsApp / CLI
```

### Key Design Principles

- **Orchestrator is stateless per task** — each task run is self-contained with its own context window
- **Sub-agents are tool wrappers** — each exposes a clean set of callable tools to the orchestrator
- **ReAct loop is the execution engine** — Thought → Action → Observation, repeated until done or max steps hit
- **Everything streams live** — each step is sent to WhatsApp / printed to CLI as it happens
- **Orchestrator decides which agents to use** — it is never hardcoded which agents a task needs

---

## The Orchestrator Agent

### Responsibility

The `OrchestratorAgent` receives a natural language goal and executes it by:
1. Detecting that the task is agentic (multi-step or cross-domain)
2. Planning the steps using the LLM
3. Running the ReAct loop — choosing tools, calling sub-agents, observing results
4. Deciding when the task is complete
5. Formatting and returning the final result

### Agentic Task Detection

Not every message needs the orchestrator. `ChatService` first classifies the message:

```python
AGENTIC_SIGNALS = [
    "and then", "after that", "also", "and remind",
    "find and", "check and", "summarize and save",
    "research and", "draft a", "schedule a"
]

def is_agentic(message: str) -> bool:
    # Heuristic: multi-step connectors OR LLM classification for ambiguous cases
    if any(signal in message.lower() for signal in AGENTIC_SIGNALS):
        return True
    return llm_classify_agentic(message)  # fast single-token classification
```

Simple questions ("what's the weather?") go to normal `ChatService`. Multi-step goals go to `OrchestratorAgent`.

### OrchestratorAgent API

```python
class OrchestratorAgent:
    def __init__(self, tools: ToolRegistry, stream_callback: Callable):
        self.tools = tools
        self.stream = stream_callback      # sends live updates to WhatsApp/CLI

    def run(self, goal: str) -> AgentResult:
        # Runs the full ReAct loop for the given goal
        # Returns final result + full trace

    def plan(self, goal: str) -> list[str]:
        # Optional: generate an explicit plan before the loop starts
        # "To do this I will: 1) fetch emails 2) classify 3) set reminder"
```

### System Prompt

```
You are Sage, a personal AI agent with access to the user's email, calendar,
web search, memory, and productivity tools.

You operate in a ReAct loop:
- THOUGHT: reason about what to do next
- ACTION: call exactly one tool
- OBSERVATION: read the tool result
- Repeat until the task is complete, then respond with FINAL ANSWER.

Rules:
- Always show your THOUGHT before each ACTION
- Never call more than one tool at a time
- If a tool fails, reason about an alternative approach
- Stop after 10 steps maximum — summarize progress and ask the user if needed
- Be concise in thoughts — one or two sentences max
```

---

## Sub-Agents

Each sub-agent is a class that exposes a set of **tools** to the orchestrator. The orchestrator calls these tools by name — it never instantiates sub-agents directly.

---

### EmailAgent

**Wraps:** Gmail API (OAuth 2.0, free tier)

**Tools exposed:**

```python
fetch_unread_emails(limit: int = 20) -> list[Email]
# Returns: sender, subject, snippet, timestamp, thread_id

fetch_email_body(thread_id: str) -> str
# Returns: full email body text

classify_urgency(emails: list[Email]) -> list[UrgentEmail]
# LLM call: classifies each email as urgent / normal / can ignore
# Returns: emails with urgency label + one-line reason

search_emails(query: str) -> list[Email]
# Gmail search syntax: "from:recruiter", "subject:interview", etc.

draft_reply(thread_id: str, instruction: str) -> str
# LLM generates a draft reply — does NOT send, returns text for user approval
```

**Email schema:**
```python
@dataclass
class Email:
    thread_id: str
    sender: str
    subject: str
    snippet: str
    timestamp: datetime
    urgency: str | None       # set after classify_urgency
    urgency_reason: str | None
```

**Urgency classification prompt:**
```
Classify each email as: URGENT, NORMAL, or IGNORE.

URGENT = requires action within 24h: job applications, payment deadlines,
         security alerts, time-sensitive requests from real people.
NORMAL = worth reading but not time-sensitive.
IGNORE = newsletters, promotions, automated notifications.

Respond in JSON: [{"thread_id": "...", "urgency": "URGENT", "reason": "..."}]
```

**Gmail OAuth setup:**
- One-time setup: `sage setup gmail` → opens browser for OAuth consent
- Stores token in `~/.sage/gmail_token.json`
- Scopes: `gmail.readonly` + `gmail.compose` (for drafts)
- Never sends email autonomously — always shows draft for user approval first

---

### CalendarAgent

**Wraps:** Google Calendar API (OAuth 2.0, free tier)

**Tools exposed:**

```python
get_events(date: str | None = "today") -> list[CalendarEvent]
# Returns events for given date or date range

get_free_slots(date: str, duration_minutes: int) -> list[TimeSlot]
# Returns available time slots of given duration

create_event(title: str, date: str, time: str, duration_minutes: int) -> CalendarEvent
# Creates a calendar event — requires user confirmation before calling

find_next_occurrence(event_name: str) -> CalendarEvent | None
# "When is my next interview?" → searches upcoming events

get_week_overview() -> list[CalendarEvent]
# Returns all events for the next 7 days
```

**CalendarEvent schema:**
```python
@dataclass
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    location: str | None
    description: str | None
    attendees: list[str]
```

**Confirmation gate:**

`create_event` always triggers a confirmation before executing:
```
Sage: I'd like to create this event:
      📅 "Reply to urgent emails" — Tonight at 9:00 PM (30 min)
      Confirm? (yes / no)
```
The orchestrator pauses the ReAct loop and waits for user reply before proceeding.

---

### SearchAgent

**Wraps:** Tavily API / DuckDuckGo fallback (from PRD v1)

**Tools exposed:**

```python
web_search(query: str, max_results: int = 5) -> list[SearchResult]
# Returns title, snippet, URL, published date

fetch_page(url: str) -> str
# Full page text — used when snippet isn't enough
# Reuses URLIngestionService scraper

search_and_summarize(query: str) -> str
# Searches + passes results to LLM for a synthesized answer
# Used when orchestrator needs a clean answer, not raw results
```

**Difference from existing web search:**

The standalone `/search` command returns results to the user directly. `SearchAgent` returns results **to the orchestrator** as observations in the ReAct loop — the orchestrator decides what to do with them next.

---

### MemoryAgent

**Wraps:** ChromaDB (RAG) + SQLite (facts, sessions)

**Tools exposed:**

```python
search_knowledge_base(query: str, limit: int = 5) -> list[KnowledgeChunk]
# Semantic search over ingested documents and URLs

save_fact(content: str, category: str = "general") -> Fact
# Saves a fact to SQLite facts table

get_facts(category: str | None = None) -> list[Fact]
# Retrieves stored facts, optionally filtered by category

ingest_url(url: str) -> IngestionResult
# Reuses URLIngestionService — saves page to RAG
# Used when orchestrator decides to "remember" something mid-task

summarize_and_save(content: str, title: str) -> str
# Summarizes content via LLM and saves to knowledge base
```

**Example usage in ReAct loop:**
```
[Thought]  The user asked me to research LangGraph and save it.
           I should search first, then ingest the best sources.
[Action]   SearchAgent.web_search("LangGraph tutorial 2026")
[Observe]  5 results returned. Top result: langchain-ai.github.io/langgraph
[Action]   MemoryAgent.ingest_url("https://langchain-ai.github.io/langgraph/")
[Observe]  Ingested. 3,200 words saved to knowledge base.
[Action]   MemoryAgent.summarize_and_save(content, title="LangGraph Notes")
[Observe]  Summary saved.
[Final]    Done! Saved LangGraph docs to your knowledge base...
```

---

### ProductivityAgent

**Wraps:** SQLite todos + habits + APScheduler (from PRD v1)

**Tools exposed:**

```python
create_todo(title: str, due: str | None = None, notes: str = "") -> Todo
# Creates a todo — optionally with a due date/time

get_todos(filter: str = "open") -> list[Todo]
# filter: "open" | "today" | "overdue" | "all"

complete_todo(todo_id: str) -> bool

schedule_reminder(message: str, when: str) -> ScheduledJob
# Schedules a WhatsApp message at a given time
# "tonight at 9pm", "tomorrow morning", "in 2 hours"
# Parses natural language time via dateparser library

log_habit(name: str, status: str = "done") -> HabitLog

get_habit_summary() -> list[HabitSummary]
```

**Natural language time parsing:**

`schedule_reminder` uses `dateparser` to convert natural language to a datetime:
```python
import dateparser
when_dt = dateparser.parse("tonight at 9pm")  # → datetime(2026, 4, 30, 21, 0)
scheduler.add_job(send_whatsapp_reminder, 'date', run_date=when_dt, args=[message])
```

---

## ReAct Loop Design

### Loop Structure

```python
class OrchestratorAgent:
    MAX_STEPS = 10

    def run(self, goal: str) -> AgentResult:
        messages = [
            {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
            {"role": "user", "content": goal}
        ]
        steps = []

        for step_num in range(self.MAX_STEPS):
            # 1. LLM decides next action
            response = llm.chat(messages)
            parsed = self.parse_react_response(response)

            # 2. Stream the thought to user
            self.stream(f"[Thought] {parsed.thought}")

            # 3. Check for final answer
            if parsed.is_final:
                self.stream(f"\n✅ Done!\n{parsed.final_answer}")
                return AgentResult(success=True, answer=parsed.final_answer, steps=steps)

            # 4. Execute the tool call
            self.stream(f"[Act]     {parsed.tool_name}({parsed.tool_args})")
            observation = self.tools.call(parsed.tool_name, parsed.tool_args)
            self.stream(f"[Observe] {self.truncate(observation, 200)}")

            # 5. Add to message history
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            steps.append(AgentStep(thought=parsed.thought, tool=parsed.tool_name,
                                   args=parsed.tool_args, observation=observation))

        # Max steps hit — graceful degradation
        self.stream("⚠️ Reached max steps. Here's what I completed so far...")
        return AgentResult(success=False, partial_steps=steps)
```

### LLM Response Format

The orchestrator is prompted to respond in a strict format:

```
THOUGHT: I need to fetch the user's unread emails first.
ACTION: fetch_unread_emails(limit=20)
```

or when done:

```
THOUGHT: I have all the information needed to answer.
FINAL ANSWER: Here's what I found in your emails...
```

Parsed with a simple regex — no JSON required, more reliable with smaller local models.

### Error Recovery

```python
def handle_tool_error(self, tool_name: str, error: Exception) -> str:
    # Pass error back into the loop as an observation
    # Let LLM decide whether to retry, use a different tool, or give up
    return f"Tool '{tool_name}' failed with error: {str(error)}. Try a different approach."
```

The LLM observes the error and reasons about an alternative — often it can recover without user intervention.

### Confirmation Gate (for destructive actions)

Some tools require explicit user confirmation before executing (`create_event`, `create_todo` with WhatsApp reminders). The loop pauses:

```python
CONFIRMATION_REQUIRED = ["create_event", "draft_reply", "schedule_reminder"]

if parsed.tool_name in CONFIRMATION_REQUIRED:
    self.stream(f"\n⚠️ Confirmation needed:\n{format_confirmation(parsed)}\nReply yes/no")
    self.pause_and_wait_for_reply()   # blocks until WhatsApp/CLI reply received
```

---

## Live Step Streaming

### WhatsApp Streaming

Each step is sent as a separate WhatsApp message as it happens — not batched at the end:

```python
def whatsapp_stream_callback(update: str):
    # Sent immediately as each step completes
    whatsapp_service.send_message(YOUR_NUMBER, update)
```

**Message cadence for the motivating example:**
```
Message 1: 🤖 Starting task: "Check my email, find anything urgent..."
Message 2: [Thought] I need to fetch unread emails first, then classify urgency.
Message 3: [Act] Fetching last 20 emails from Gmail...
Message 4: [Observe] Found 20 emails. Classifying urgency...
Message 5: [Act] Classifying 20 emails for urgency...
Message 6: [Observe] 3 urgent emails found.
Message 7: [Act] Creating reminder todo for 9pm...
Message 8: ✅ Done! 🚨 Urgent emails (3): ...
```

**Throttling:** minimum 1 second between messages to avoid Twilio rate limits.

### CLI Streaming

In the terminal, steps stream inline with color coding:

```
🤖 Starting task...

  [Thought]  I need to: 1) read emails 2) classify urgency 3) set reminder
  [Act]      fetch_unread_emails(limit=20)
  [Observe]  Found 20 emails. Scanning for urgency...
  [Act]      classify_urgency(emails=[...])
  [Observe]  3 urgent: recruiter follow-up, landlord, bank alert
  [Act]      create_todo(title="Reply to urgent emails", due="9pm today")
  [Observe]  Todo created ✓

✅ Done! Here's what I found...
```

### Verbosity Control

```
you: check my email quietly
→ streams nothing, returns only final answer

you: check my email
→ streams all steps (default)

you: check my email and show me your thinking
→ streams steps + full thought text
```

Detected from message phrasing — "quietly", "just tell me", "show your thinking".

---

## Tool Registry

All sub-agent tools are registered in a central `ToolRegistry`. The orchestrator calls tools by name — it never imports sub-agents directly.

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, name: str, fn: Callable, description: str, params: dict):
        self._tools[name] = ToolDefinition(name, fn, description, params)

    def call(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}"
        result = tool.fn(**args)
        return str(result)

    def as_prompt_list(self) -> str:
        # Formats all tools as a list for the orchestrator system prompt
        # "fetch_unread_emails(limit: int) — Fetch unread emails from Gmail"
```

**Tool registration at startup:**

```python
registry = ToolRegistry()

# EmailAgent tools
registry.register("fetch_unread_emails", email_agent.fetch_unread, ...)
registry.register("classify_urgency", email_agent.classify_urgency, ...)
registry.register("search_emails", email_agent.search, ...)
registry.register("draft_reply", email_agent.draft_reply, ...)

# CalendarAgent tools
registry.register("get_events", calendar_agent.get_events, ...)
registry.register("get_free_slots", calendar_agent.get_free_slots, ...)
registry.register("create_event", calendar_agent.create_event, ...)

# SearchAgent tools
registry.register("web_search", search_agent.search, ...)
registry.register("search_and_summarize", search_agent.search_and_summarize, ...)

# MemoryAgent tools
registry.register("search_knowledge_base", memory_agent.search, ...)
registry.register("save_fact", memory_agent.save_fact, ...)
registry.register("ingest_url", memory_agent.ingest_url, ...)

# ProductivityAgent tools
registry.register("create_todo", productivity_agent.create_todo, ...)
registry.register("schedule_reminder", productivity_agent.schedule_reminder, ...)
registry.register("get_todos", productivity_agent.get_todos, ...)
```

The orchestrator sees all tools as a flat list — it doesn't know or care which sub-agent owns them.

---

## Example Task Walkthroughs

### Task 1 — The Motivating Example
**Input:** "Check my email, find anything urgent, and remind me tonight to reply."

```
[Thought]  Plan: 1) fetch emails 2) classify urgency 3) schedule reminder
[Act]      fetch_unread_emails(limit=20)
[Observe]  20 emails fetched
[Act]      classify_urgency(emails)
[Observe]  3 urgent: recruiter, landlord, bank
[Act]      schedule_reminder("Reply to 3 urgent emails", "tonight at 9pm")
[Observe]  Reminder scheduled for 9:00 PM
[Final]    ✅ Done! 3 urgent emails found. Reminder set for 9pm.
```

---

### Task 2 — Research and Save
**Input:** "Research how RAG works, save the best sources, and create a todo to review them this weekend."

```
[Thought]  Need to search, ingest top sources, then create a todo
[Act]      web_search("how RAG retrieval augmented generation works 2026")
[Observe]  5 results. Top: docs.llamaindex.ai, arxiv.org/RAG-survey
[Act]      ingest_url("https://docs.llamaindex.ai/...")
[Observe]  Ingested. 2,800 words saved.
[Act]      ingest_url("https://arxiv.org/abs/2005.11401")
[Observe]  Ingested. Paper saved.
[Act]      create_todo("Review RAG sources", due="this Saturday")
[Observe]  Todo created for Saturday
[Final]    ✅ Saved 2 sources on RAG. Todo set for Saturday.
```

---

### Task 3 — Calendar + Email Coordination
**Input:** "Find the recruiter email from Anthropic and schedule interview prep for tomorrow morning."

```
[Thought]  Need to find the email first, then create a calendar event
[Act]      search_emails("from:anthropic recruiter")
[Observe]  Found 1 email: "Following up on your application" — hr@anthropic.com
[Act]      fetch_email_body(thread_id="abc123")
[Observe]  Email asks to schedule a call next week
[Act]      get_free_slots(date="tomorrow", duration_minutes=60)
[Observe]  Free slots: 9am-10am, 11am-12pm, 2pm-3pm
[Act]      create_event("Interview Prep — Anthropic", date="tomorrow", time="9am", duration=60)
[Observe]  ⚠️ Confirmation needed — waiting for user...

  Sage: I'd like to create:
        📅 "Interview Prep — Anthropic" — Tomorrow at 9:00 AM (60 min)
        Confirm? (yes/no)

  you: yes

[Observe]  Event created ✓
[Final]    ✅ Found the Anthropic recruiter email and blocked 9am tomorrow for prep.
```

---

## Cross-Feature Dependencies

| Component | Depends On |
|-----------|-----------|
| OrchestratorAgent | All sub-agents, ToolRegistry, existing ChatService |
| EmailAgent | Gmail API OAuth setup, `google-auth` + `google-api-python-client` |
| CalendarAgent | Google Calendar API OAuth (same credentials as Gmail) |
| SearchAgent | Existing WebSearchService (PRD v1) |
| MemoryAgent | Existing ChromaDB, SQLite, URLIngestionService (PRD v2) |
| ProductivityAgent | Existing TodoService, HabitService, APScheduler (PRD v1) |
| Live streaming | Existing WhatsAppService (PRD v1) |
| Confirmation gate | WhatsAppService reply handling |

---

## Build Order

### Phase 1 — Tool Registry + Orchestrator Shell *(~3 days)*
Build the infrastructure before any real tools:
1. `ToolRegistry` class with register/call interface
2. `OrchestratorAgent` with ReAct loop (no real tools yet — use mock tools)
3. Streaming callback wired to CLI output
4. Agentic task detection in `ChatService`
5. Validate the loop works end-to-end with mocks

### Phase 2 — ProductivityAgent + SearchAgent *(~2 days)*
Wrap existing functionality — lowest effort, no new APIs:
1. Register all existing todo/habit/reminder tools in the registry
2. Register existing web search as `SearchAgent` tools
3. Register existing `URLIngestionService` as `MemoryAgent` tools
4. Test multi-step tasks using only these tools

### Phase 3 — Gmail OAuth + EmailAgent *(~4 days)*
1. `sage setup gmail` OAuth flow (one-time)
2. `EmailAgent` with `fetch_unread_emails`, `classify_urgency`, `search_emails`
3. `draft_reply` tool (LLM-generated, shown for approval — never auto-sent)
4. Register all tools in registry
5. Test Task 1 end-to-end

### Phase 4 — CalendarAgent *(~3 days)*
1. Google Calendar API setup (reuses Gmail OAuth credentials)
2. `CalendarAgent` with `get_events`, `get_free_slots`, `create_event`
3. Confirmation gate for `create_event`
4. Test Task 3 end-to-end

### Phase 5 — WhatsApp Streaming + Polish *(~2 days)*
1. Wire streaming callback to `WhatsAppService`
2. Message throttling (1s between updates)
3. Verbosity control ("quietly", "show your thinking")
4. Max steps graceful degradation
5. End-to-end test of all 3 example tasks via WhatsApp

**Total estimated effort:** 3-4 weeks part-time

---

## Out of Scope

- **Auto-sending emails** — `draft_reply` always requires user approval. Sage never sends email autonomously.
- **Auto-creating calendar events** — always gated behind confirmation.
- **Parallel agent execution** — all steps are sequential in the ReAct loop. Parallelism is a future enhancement.
- **Persistent agent memory across tasks** — each task run starts fresh. Cross-task memory lives in SQLite facts/sessions.
- **Custom agent creation by user** — fixed set of sub-agents for now.
- **LangGraph or LangChain** — deliberately avoided. The ReAct loop is implemented from scratch to keep the architecture transparent and dependency-light. This is also a better learning exercise and more impressive to explain in interviews.
- **Streaming tokens mid-response** — steps stream between tool calls, not within LLM generation.