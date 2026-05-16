# Phase 1 — Web Search

**Est. effort:** 3–4 days  
**Dependencies:** None — fully independent  
**Status:** Completed

---

## Goal

Register a `web_search` tool alongside the existing `fetch_news` tool so the LLM can call it automatically based on intent. Add a `/search` command as an explicit shortcut.

---

## New Files

- `app/services/web_search_service.py` *(moved to `services/` during refactor)*

## Modified Files

- `app/core/tools.py` — added `WebSearchTool` class + `web_search_service` param to `ToolRegistry`
- `app/core/chat_service.py` — injected `WebSearchService`; tracks `web_sources` from tool calls; `/search` command handler
- `app/core/qa_service.py` — added `web_sources: List[dict]` field to `QAResult`
- `app/cli/commands_ask.py` — added `create_web_search_service()` factory; wired into `create_chat_service()`
- `app/cli/commands_chat.py` — `/search` command display + web sources (🌐) in chat output
- `app/config/settings.py` — added `TAVILY_API_KEY`, `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_PROVIDER`
- `requirements.txt` — added `tavily-python`, `ddgs` *(duckduckgo-search was renamed to ddgs)*
- `tests/cli/test_chat_command.py` — added `get_web_search_service()` to stub

## Deviations from Plan

- **`app/services/` not `app/core/`** — file landed in `services/` after the external-integrations refactor; correct location per the new layout
- **`WEB_SEARCH_PATTERNS` not added** — the existing tool-calling loop already routes factual queries to `web_search` automatically; a separate pattern list was redundant
- **`tool_executor.py` unchanged** — tool execution is handled generically by `ToolRegistry.execute_tool()`; no per-tool case needed

---

## Tasks

### 1.1 — `WebSearchService` + `SearchResult` dataclass

**File:** `app/core/web_search_service.py`

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    published_date: str | None

class WebSearchService:
    def __init__(self, api_key: str | None = None, provider: str = "tavily", max_results: int = 5)
    def search(self, query: str) -> list[SearchResult]
    def format_for_context(self, results: list[SearchResult]) -> str
    def _search_tavily(self, query: str) -> list[SearchResult]
    def _search_duckduckgo(self, query: str) -> list[SearchResult]
```

- If `TAVILY_API_KEY` is set and `provider == "tavily"`, use Tavily.
- Otherwise fall back to DuckDuckGo (`duckduckgo_search.DDGS`).
- `format_for_context` returns numbered citation block:
  ```
  [1] Title — snippet (url)
  [2] ...
  ```

**AC:**
- [x] `search()` returns up to `max_results` results from Tavily when key is set
- [x] Falls back to DuckDuckGo when `TAVILY_API_KEY` is absent or provider is `"duckduckgo"`
- [x] `format_for_context()` produces numbered citation output

---

### 1.2 — Register `web_search` tool

**File:** `app/core/tools.py`

Add to the tool registry alongside `fetch_news`:

```python
{
    "name": "web_search",
    "description": "Search the web for current factual information, recent events, definitions, tutorials, or anything not in the user's documents.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"}
        },
        "required": ["query"]
    }
}
```

**File:** `app/core/tool_executor.py`

Add handler:
```python
elif tool_name == "web_search":
    results = web_search_service.search(tool_input["query"])
    return web_search_service.format_for_context(results)
```

**AC:**
- [x] `web_search` appears in the tools list sent to the LLM
- [x] LLM can invoke it; `ToolExecutor` executes it and returns formatted results

---

### 1.3 — Inject `WebSearchService` into `ChatService`

**File:** `app/core/chat_service.py`

- Add optional `web_search_service: WebSearchService | None = None` parameter to `__init__`.
- Pass it through to `ToolExecutor`.
- Add `WEB_SEARCH_PATTERNS` list (for factual/definitional queries) alongside existing `NEWS_PATTERNS`.
- Add `/search <query>` explicit command handler — bypasses routing, calls `web_search_service.search()` directly, formats citations, and prints.

Routing priority (from most to least specific):
1. `/search <query>` → always web search
2. `/news <topic>` → always NewsService
3. Matches `NEWS_PATTERNS` → LLM chooses `fetch_news` or `web_search`
4. Everything else → LLM decides (tools available)

**AC:**
- [x] `sage ask "What is LangGraph?"` returns a web-grounded answer with citations
- [x] `/search rust tutorial` works in both `sage chat` and `sage ask` modes
- [x] `/news AI` still uses NewsService as before

---

### 1.4 — Settings + env vars

**File:** `app/config/settings.py`

```python
TAVILY_API_KEY: str = ""
WEB_SEARCH_MAX_RESULTS: int = 5
WEB_SEARCH_PROVIDER: str = "tavily"  # "tavily" | "duckduckgo"
```

**File:** `.env.example`

```env
TAVILY_API_KEY=your_key_here
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROVIDER=tavily
```

**AC:**
- [x] App starts without `TAVILY_API_KEY` set (falls back to DuckDuckGo)
- [x] Setting `WEB_SEARCH_PROVIDER=duckduckgo` forces DuckDuckGo even if key exists

---

### 1.5 — Add dependencies

**File:** `setup.py` / `pyproject.toml`

```
tavily-python>=0.3
duckduckgo-search>=6.0
```

**AC:**
- [x] `pip install -e .` installs both packages
- [x] Both imports succeed in a fresh venv

---

## Citation Format

Responses must include a `web sources:` section when web search results are used:

```
According to [1], LangGraph is a framework for building stateful multi-agent workflows.

web sources:
- [1] LangGraph Docs — https://langchain-ai.github.io/langgraph/
- [2] LangGraph Tutorial — https://medium.com/...
```

---

## Acceptance Criteria (phase complete)

- [x] `WebSearchService.search()` returns results from Tavily or DuckDuckGo
- [x] `web_search` registered as callable tool in `ChatService`
- [x] LLM routes factual questions to web search vs. RAG vs. news correctly
- [x] Results cited with title + URL in response
- [x] Works in both `sage chat` and `sage ask` modes
- [x] Falls back to DuckDuckGo when Tavily key not set
- [x] `/search <query>` command works as explicit shortcut
