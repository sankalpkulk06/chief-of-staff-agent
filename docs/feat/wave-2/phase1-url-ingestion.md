# Phase 1 — URL & Article Ingestion

**Est. effort:** 3–4 days
**Dependencies:** Existing `IngestService`, `ChromaStore`, `SQLiteRegistry`, `ChatService`
**Status:** Complete

---

## Goal

Paste a URL in WhatsApp or CLI and Sage automatically scrapes, chunks, embeds, and stores it in ChromaDB — no explicit command. Confirms with a title + AI summary. Saved URLs are queryable via RAG and cited distinctly from local files.

---

## New Files

- `app/services/url_ingestion_service.py` — URL detection, scrape, ingest, dedup, list
- `tests/services/test_url_ingestion_service.py` — unit tests for detection, scraping (mocked), dedup

## Modified Files

- `app/storage/sql_schema.sql` — add `source_type`, `source_url`, `ingested_at` to `documents`
- `app/storage/sqlite_registry.py` — `is_url_ingested()`, `list_url_documents()`, idempotent ALTER guard
- `app/ingestion/ingest_service.py` — new entry point that takes pre-parsed text + URL metadata (reuse chunker/embedder)
- `app/core/chat_service.py` — URL detection runs before LLM/intent routing
- `app/retrieval/prompt_builder.py` — citation format distinguishes URL (🌐) vs. local (📄)
- `app/cli/app.py` — `/sources` command
- `app/config/settings.py` — `URL_INGESTION_ENABLED`, `URL_SCRAPE_TIMEOUT`, `URL_MIN_CONTENT_WORDS`, `URL_MAX_CONTENT_WORDS`
- `.env.example` — document new env vars
- `requirements.txt` — `beautifulsoup4>=4.12`, `httpx>=0.27`

---

## Tasks

### 1.1 — Schema migration: extend `documents` table

**File:** `app/storage/sql_schema.sql`

SQLite `ALTER TABLE` cannot use `IF NOT EXISTS` for columns. Add a guarded migration in `SQLiteRegistry`:

**File:** `app/storage/sqlite_registry.py`

```python
def initialize_schema(self) -> None:
    schema_path = Path(__file__).resolve().parent / "sql_schema.sql"
    self._connection.executescript(schema_path.read_text(encoding="utf-8"))
    self._migrate_documents_columns()
    self._connection.commit()

def _migrate_documents_columns(self) -> None:
    cols = {row[1] for row in self._connection.execute("PRAGMA table_info(documents)").fetchall()}
    if "source_type" not in cols:
        self._connection.execute("ALTER TABLE documents ADD COLUMN source_type TEXT DEFAULT 'local'")
    if "source_url" not in cols:
        self._connection.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
    if "ingested_at" not in cols:
        self._connection.execute("ALTER TABLE documents ADD COLUMN ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP")
```

**AC:**
- [x] On startup, existing `documents` rows get `source_type='local'` by default
- [x] Migration is idempotent (safe to run twice)
- [x] New columns visible via `PRAGMA table_info(documents)`

---

### 1.2 — `URLIngestionService`

**File:** `app/services/url_ingestion_service.py`

```python
URL_REGEX = re.compile(r"https?://[^\s<>\"]+")

@dataclass
class ScrapedPage:
    url: str
    title: str
    content: str
    word_count: int
    scraped_at: datetime

@dataclass
class IngestionResult:
    success: bool
    url: str
    title: str | None
    summary: str | None        # 2-sentence LLM summary
    chunks_added: int
    error: str | None          # populated on failure

class URLIngestionService:
    def __init__(
        self,
        registry: SQLiteRegistry,
        ingest_service: IngestService,
        chat_provider,            # for LLM summary
        timeout: int = 10,
        min_words: int = 100,
        max_words: int = 50000,
    ): ...

    def is_url(self, text: str) -> bool: ...
    def extract_url(self, text: str) -> str | None: ...
    def already_ingested(self, url: str) -> bool: ...
    def scrape(self, url: str) -> ScrapedPage: ...
    def ingest(self, url: str) -> IngestionResult: ...
    def list_url_sources(self) -> list[dict]: ...
    def _summarize(self, page: ScrapedPage) -> str: ...
```

**Scraping logic:**

```python
def scrape(self, url: str) -> ScrapedPage:
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (Sage)"}, timeout=self.timeout, follow_redirects=True)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["nav", "footer", "header", "script", "style", "aside", "noscript"]):
        tag.decompose()
    title_el = soup.find("title")
    title = title_el.text.strip() if title_el else url
    main = soup.find("article") or soup.find("main") or soup.find("body")
    content = main.get_text(separator="\n", strip=True) if main else ""
    words = content.split()
    if len(words) > self.max_words:
        content = " ".join(words[: self.max_words])
    return ScrapedPage(url=url, title=title, content=content, word_count=len(words), scraped_at=datetime.utcnow())
```

**Ingestion flow:**

```python
def ingest(self, url: str) -> IngestionResult:
    if self.already_ingested(url):
        return IngestionResult(success=False, url=url, title=None, summary=None, chunks_added=0, error="already_ingested")
    try:
        page = self.scrape(url)
    except httpx.TimeoutException:
        return IngestionResult(..., error="timeout")
    except httpx.HTTPStatusError as e:
        return IngestionResult(..., error=f"http_{e.response.status_code}")
    if page.word_count < self.min_words:
        return IngestionResult(..., error="too_short")

    parsed = ParsedDocument(
        content=page.content,
        metadata={"title": page.title, "source_type": "url", "source_url": url, "ingested_at": page.scraped_at.isoformat()},
        file_name=page.title,
        file_type="url",
    )
    document_id = self.ingest_service.ingest_parsed(parsed, source_path=url, source_type="url", source_url=url)
    summary = self._summarize(page)
    return IngestionResult(success=True, url=url, title=page.title, summary=summary, chunks_added=..., error=None)
```

**`_summarize` prompt (2 sentences, ~30 words):**

```
Summarize the following article in exactly two sentences for a knowledge-base
confirmation message. Be specific about the topic — no filler.

ARTICLE TITLE: {title}
ARTICLE START:
{first_500_words}
```

**AC:**
- [x] `is_url("https://x.com/y")` is `True`; `is_url("hi there")` is `False`
- [x] `extract_url("save this https://foo.com bar")` returns `"https://foo.com"`
- [x] `scrape()` returns cleaned text from `<article>`/`<main>` (nav/footer stripped)
- [x] `ingest()` writes one document row + N chunks + Chroma vectors
- [x] `already_ingested()` returns `True` after a successful ingest of the same URL
- [x] All error paths return `IngestionResult(success=False, error=...)` — never raise

---

### 1.3 — Extend `IngestService` with a parsed-text entry point

**File:** `app/ingestion/ingest_service.py`

Currently `IngestService` reads from disk. Add a method that accepts already-parsed content + URL-aware metadata, reusing the existing chunker/embedder/Chroma writer:

```python
def ingest_parsed(
    self,
    parsed: ParsedDocument,
    source_path: str,           # the URL
    source_type: str = "local", # "local" | "url"
    source_url: str | None = None,
) -> str:
    """Chunk + embed + persist. Returns the document_id."""
    document_id = compute_document_id(source_path)
    self.registry.upsert_document(document_id, parsed)
    self.registry.set_document_source(document_id, source_type=source_type, source_url=source_url)
    chunks = self.chunker.chunk(parsed.content, document_id=document_id)
    for chunk in chunks:
        chunk.metadata.update({"source_type": source_type, "source_url": source_url, "title": parsed.metadata.get("title")})
    embeddings = self.embedder.embed_many([c.text for c in chunks])
    self.chroma_store.upsert_chunks(chunks, embeddings)
    for chunk in chunks:
        self.registry.upsert_chunk(chunk)
    return document_id
```

Add `set_document_source(document_id, source_type, source_url)` to `SQLiteRegistry`.

**AC:**
- [x] Existing local-file ingestion still works unchanged (uses `source_type='local'` default)
- [x] URL ingestion populates `source_type='url'`, `source_url=<url>` on the document row
- [x] Each chunk stored in Chroma carries `source_type` + `source_url` + `title` metadata

---

### 1.4 — URL detection in `ChatService`

**File:** `app/core/chat_service.py`

In `handle_message` (or equivalent entry method), check for a URL **before** any other intent routing:

```python
def handle_message(self, message: str, session_id: str) -> str:
    if self.url_ingestion_service:
        url = self.url_ingestion_service.extract_url(message)
        if url:
            return self._handle_url(url, message)
    # ...existing routing
```

**`_handle_url` formatting:**

```python
def _handle_url(self, url: str, original_message: str) -> str:
    if self.url_ingestion_service.already_ingested(url):
        return "Already saved that one! Ask me anything about it."
    result = self.url_ingestion_service.ingest(url)
    if not result.success:
        return self._format_url_error(result.error)
    return (
        f"📥 Saved to your knowledge base!\n"
        f"Title: {result.title}\n"
        f"Summary: {result.summary}\n"
        f"You can now ask me questions about it."
    )

def _format_url_error(self, error: str) -> str:
    return {
        "timeout": "The page took too long to load. Try again or paste the article text directly.",
        "too_short": "That page doesn't have much readable content — it might be a login wall or redirect.",
    }.get(error, "Couldn't access that page — it may be behind a login or no longer exists.") if not error.startswith("http_") else \
        "Couldn't access that page — it may be behind a login or no longer exists."
```

Inject `url_ingestion_service` via `ChatService.__init__` (optional, like other services). Wire it up in the CLI factory (`commands_ask.py` / `commands_chat.py`) and the webhook bootstrap.

**AC:**
- [x] Bare URL in CLI chat triggers ingest + confirmation
- [x] Bare URL in WhatsApp webhook triggers ingest + Twilio reply
- [x] "remember this https://foo.com" triggers ingest (URL detected anywhere in the message)
- [x] If `URL_INGESTION_ENABLED=false`, message falls through to normal routing

---

### 1.5 — Citation format: distinguish URL vs. local

**File:** `app/retrieval/prompt_builder.py`

When formatting the `sources:` block for the LLM context and final reply, branch on chunk metadata:

```python
def format_source_line(idx: int, chunk_metadata: dict) -> str:
    title = chunk_metadata.get("title") or chunk_metadata.get("file_name", "untitled")
    if chunk_metadata.get("source_type") == "url":
        domain = urlparse(chunk_metadata["source_url"]).netloc
        ingested = chunk_metadata.get("ingested_at", "")[:10]  # YYYY-MM-DD
        return f"[{idx}] {title} — {domain}  🌐 (saved {ingested})"
    return f"[{idx}] {title}  📄 (local)"
```

**AC:**
- [x] URL sources render with 🌐, domain, and saved date
- [x] Local files render with 📄
- [x] Mixed result sets list both formats correctly

---

### 1.6 — `/sources` command

**File:** `app/cli/app.py`

Add a Typer subcommand (or extend the existing chat slash-command handler) that lists all ingested sources grouped by type:

```python
@app.command()
def sources():
    """List all ingested sources (URLs + local files)."""
    registry = create_registry()
    docs = registry.list_documents_with_source()
    url_docs = [d for d in docs if d["source_type"] == "url"]
    local_docs = [d for d in docs if d["source_type"] != "url"]
    # render with rich.Table
```

Also wire `"what have you saved?"` / `"/sources"` as a slash command inside `sage chat` (handled by `ChatService`).

**AC:**
- [x] `sage sources` prints all URL and local sources, numbered
- [x] `/sources` works inside `sage chat`
- [x] "what have you saved?" works in WhatsApp + CLI (via intent match in ChatService)

---

### 1.7 — Settings + env vars

**File:** `app/config/settings.py`

```python
URL_INGESTION_ENABLED: bool = True
URL_SCRAPE_TIMEOUT: int = 10
URL_MIN_CONTENT_WORDS: int = 100
URL_MAX_CONTENT_WORDS: int = 50000
```

**File:** `.env.example`

```env
URL_INGESTION_ENABLED=true
URL_SCRAPE_TIMEOUT=10
URL_MIN_CONTENT_WORDS=100
URL_MAX_CONTENT_WORDS=50000
```

**AC:**
- [x] App boots with defaults if env vars absent
- [x] `URL_INGESTION_ENABLED=false` disables URL detection in `ChatService`

---

### 1.8 — Dependencies

**File:** `requirements.txt`

```
httpx>=0.27
beautifulsoup4>=4.12
```

`httpx` may already be present (used in tests); ensure it's in production deps.

**AC:**
- [x] Fresh `pip install -e .` installs both packages
- [x] `from bs4 import BeautifulSoup` and `import httpx` succeed at runtime

---

## Acceptance Criteria (phase complete)

- [x] Bare URL in any input triggers automatic ingestion (CLI + WhatsApp)
- [x] Confirmation includes title + 2-sentence AI summary
- [x] Saved URLs queryable via RAG with 🌐 citation format
- [x] Duplicate URLs detected and skipped with friendly message
- [x] `/sources` lists all URL + local sources
- [x] Graceful error messages for 404, timeout, login walls, too-short pages
- [x] Migration is idempotent on existing `documents` rows
- [x] Unit tests cover detection, scraping (mocked HTTP), and dedup
