# Phase 1 — Multi-Agent Orchestrator

Convert Sage from a single monolithic ChatService into an Agno-based multi-agent system
with model-agnostic LLM providers (Gemini, Groq, Ollama). This is the core of the Wipro assignment.

**Framework:** Agno (Python-native agents, teams, workflows, tools, memory, guardrails, and production serving)

---

## 1.1 Model-Agnostic Provider Layer

**Goal:** Replace `OllamaChatProvider` and `OllamaEmbeddingsProvider` with a unified interface
that works with Gemini, Groq, and Ollama. No agent should import a provider directly — all
go through the interface.

**Tasks:**
- [ ] Create `app/providers/base.py` — define `LLMProvider` and `EmbeddingsProvider` abstract base classes
  - `LLMProvider.chat(messages, tools=None) -> str | ToolCall`
  - `EmbeddingsProvider.embed(texts) -> list[list[float]]`
- [ ] Create `app/providers/gemini_provider.py` — wraps `google-generativeai` SDK
  - Support `GEMINI_API_KEY` from env
  - Map Agno/generic message format to Gemini's format
  - Support tool calling via Gemini function calling API
- [ ] Create `app/providers/groq_provider.py` — wraps `groq` SDK (OpenAI-compatible)
  - Support `GROQ_API_KEY` from env
  - Models: `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`, `gemma2-9b-it`
  - Tool calling via OpenAI-compatible tool format
- [ ] Update `app/providers/ollama_chat.py` to implement `LLMProvider` interface (keep as fallback)
- [ ] Create `app/providers/factory.py` — reads `LLM_PROVIDER` env var (`gemini`|`groq`|`ollama`)
  and returns the correct provider instance; same for embeddings (`EMBEDDINGS_PROVIDER`)
- [ ] Add to `app/config/settings.py`:
  - `llm_provider: str = "groq"` (default)
  - `gemini_api_key: str = ""`
  - `gemini_chat_model: str = "gemini-1.5-flash"`
  - `groq_api_key: str = ""`
  - `groq_chat_model: str = "llama-3.3-70b-versatile"`
  - `embeddings_provider: str = "ollama"` (keep local by default; swap to Gemini if needed)
- [ ] Add `agno`, `google-generativeai>=0.8`, and `groq>=0.9` to `requirements.txt`
- [ ] Update all code that imports `OllamaChatProvider` directly to use the factory

---

## 1.2 Define the Agent Roster

**Five agents** matching the assignment's suggested types:

| Agent | Role | LLM? |
|-------|------|-------|
| **Orchestrator (Planner)** | Receives user input, decomposes task, routes to specialized agents, assembles final reply | Yes |
| **RAG Agent** | Handles all document/knowledge retrieval (ChromaDB semantic search, URL ingestion) | Yes |
| **Action Agent (Executor)** | Executes side-effecting tools: add_todo, add_habit, log_habit, send email, add reminder | Yes |
| **Research Agent** | Web search (Tavily/DDG), news fetch, email triage — read-only external data | Yes |
| **Security/Guardrails Agent** | Validates all inbound user input and outbound LLM responses; enforces policy | Lightweight (rule + LLM) |

**Tasks:**
- [ ] Create `app/agents/` package with `__init__.py`
- [ ] Define `AgentMessage` dataclass in `app/agents/base.py` for Sage-specific run metadata:
  ```python
  @dataclass
  class AgentMessage:
      role: str           # "user" | "agent" | "tool"
      agent_name: str
      content: str
      tool_calls: list    # structured tool invocations
      metadata: dict      # latency, tokens, source citations
  ```
- [ ] Create Agno agent factory helpers in `app/agents/base.py` so each Sage agent is configured consistently
- [ ] Define `SageRunContext` / `AgentRunState` TypedDict for data passed through Agno workflows:
  - `messages: list[AgentMessage]`
  - `user_input: str`
  - `session_id: str`
  - `active_agent: str`
  - `tool_results: dict`
  - `final_reply: str`
  - `security_flags: list[str]`
  - `citations: list`
  - `error: str | None`

---

## 1.3 Implement the Security/Guardrails Agent

**This should be built first** — it gates every other agent. Required by the assignment.

**Tasks:**
- [ ] Create `app/agents/security_agent.py`
- [ ] Input validation (runs before Orchestrator):
  - Detect prompt injection patterns (ignore previous instructions, jailbreak phrases, role-override attempts)
  - Sanitize HTML/script injection in user text
  - Check for PII in input (email addresses, phone numbers, SSNs) and flag/redact
  - Enforce max input length (2000 chars)
- [ ] Output filtering (runs before final reply delivery):
  - Strip any accidental secrets/keys in LLM output (regex for `sk-`, `AIza`, bearer tokens)
  - Check output doesn't contain system prompt leakage
  - Enforce role constraints: if action agent tries to call a tool outside its allowed set, block it
- [ ] Implement a `SecurityPolicy` config (YAML or pydantic) that defines:
  - Allowed tools per agent
  - Blocked keyword patterns
  - Max output length
- [ ] Log all security events to a `security_events` SQLite table (never to external services)
- [ ] Add schema for `security_events` table to `app/storage/sql_schema.sql`

---

## 1.4 Implement the Orchestrator (Planner) Agent

**Tasks:**
- [ ] Create `app/agents/orchestrator.py`
- [ ] Implement the planner as an Agno `Agent` with structured output for step plans
- [ ] Orchestrator system prompt defines:
  - Its role as coordinator (not executor)
  - The list of available sub-agents and what each can do
  - When to run agents in sequence vs. in parallel
  - How to synthesize sub-agent results into a coherent final reply
- [ ] Task decomposition logic: for complex queries, orchestrator produces a plan as structured JSON:
  ```json
  { "steps": [{"agent": "rag_agent", "task": "find docs about X"}, ...] }
  ```
- [ ] Implement sequential execution (step by step, result feeds next)
- [ ] Implement parallel execution through Agno team/workflow coordination for independent steps
- [ ] Orchestrator assembles final reply from all sub-agent outputs
- [ ] Maintain backwards compatibility: simple conversational queries skip sub-agents entirely (fast path)

---

## 1.5 Implement the RAG Agent

**Tasks:**
- [ ] Create `app/agents/rag_agent.py`
- [ ] Wrap the behavior as an Agno `Agent` with RAG/search tools
- [ ] Wraps existing `Retriever`, `ChromaStore`, `PromptBuilder`, and `UrlIngestionService`
- [ ] Tools available to this agent: `search_documents`, `ingest_url`
- [ ] Returns structured result: `{ "answer": str, "citations": list, "chunks_used": int }`
- [ ] If no relevant chunks found, explicitly signals "no knowledge" so orchestrator can fall back to Research Agent
- [ ] Handles `top_k` dynamically based on query complexity

---

## 1.6 Implement the Action Agent (Executor)

**Tasks:**
- [ ] Create `app/agents/action_agent.py`
- [ ] Wrap the behavior as an Agno `Agent` with tightly scoped side-effecting tools
- [ ] Wraps existing `HabitService`, `FactService`, `RemindersService`, `todo_parser`
- [ ] Allowed tools: `add_todo`, `add_apple_reminder`, `add_habit`, `log_habit`, `get_habits`, `remember_fact`, `list_facts`
- [ ] All tool calls are logged before execution (audit trail in SQLite)
- [ ] On tool failure: structured error returned to orchestrator (no silent failure)
- [ ] Never executes tools not in its allowed set (Security Agent enforces this, Action Agent also self-checks)

---

## 1.7 Implement the Research Agent

**Tasks:**
- [ ] Create `app/agents/research_agent.py`
- [ ] Wrap the behavior as an Agno `Agent` with read-only research tools
- [ ] Wraps `WebSearchService`, `NewsService`, `EmailService`
- [ ] Allowed tools: `web_search`, `fetch_news`, `triage_email`
- [ ] Read-only: cannot modify any state
- [ ] Returns results with source URLs and timestamps
- [ ] Implements rate limiting: max 3 web search calls per user turn

---

## 1.8 Wire Everything Together with Agno

**Tasks:**
- [ ] Add `agno` and the needed Agno extras (`ollama`, `groq`, `google`, `sqlite`, `chromadb`, `qdrant`, `tavily` as needed) to `requirements.txt`
- [ ] Create `app/agents/workflow.py` — defines the Agno `Workflow` / `Team`:
  ```
  User input
    └─▶ SecurityAgent (input validation)
          ├─ BLOCKED ──▶ return rejection message
          └─ OK ──────▶ OrchestratorAgent
                              ├─▶ RAGAgent (conditional)
                              ├─▶ ResearchAgent (conditional)
                              ├─▶ ActionAgent (conditional)
                              └─▶ SecurityAgent (output validation)
                                        └─▶ final reply
  ```
- [ ] Implement conditional routing based on the orchestrator's structured plan
- [ ] Each Agno agent wraps one bounded specialist capability and its allowed tools
- [ ] Configure Agno storage/session persistence where it complements Sage's existing SQLite session tables
- [ ] Create `app/agents/runner.py` — exposes `run_agent_pipeline(user_input, session_id) -> AgentResult`
  This is the new single entry point that replaces the direct ChatService LLM loop

---

## 1.9 Integrate Agent Pipeline with Existing Surfaces

**Tasks:**
- [ ] Update `app/core/chat_service.py` to call `runner.run_agent_pipeline()` instead of its internal loop
  - Keep `ChatService` as the session/persistence manager; delegate all LLM work to the Agno workflow/team
- [ ] Ensure REST API (`app/api/sessions.py` chat endpoint) still works — no changes to API surface
- [ ] Ensure CLI chat still works
- [ ] Ensure WhatsApp webhook still works
- [ ] Update `app/core/tool_executor.py` — deprecate direct use; tools now invoked via agent tool schemas

---

## 1.10 Error Handling & Retries

**Tasks:**
- [ ] Add retry decorator (`tenacity` library) to all LLM provider calls: 3 retries, exponential backoff
- [ ] Add `tenacity>=8.2` to `requirements.txt`
- [ ] Each agent catches its own errors and returns a structured error state
- [ ] Orchestrator implements fallback: if RAG Agent fails, try Research Agent
- [ ] Timeout: each agent node has a 30-second timeout; orchestrator signals graceful degradation
- [ ] Add `LLM_TIMEOUT_SECONDS=30` to settings

---

## 1.11 Tests

**Tasks:**
- [ ] Add `tests/agents/` directory
- [ ] Unit test Security Agent: known injection strings should be blocked
- [ ] Unit test provider factory: mock API calls, verify correct provider is returned
- [ ] Integration test: full Agno workflow/team run with mocked LLM responses
- [ ] Verify existing tests still pass after ChatService refactor
