from pathlib import Path

import httpx

from app.ingestion.ingest_service import IngestService
from app.schemas.document import ParsedDocument
from app.services.url_ingestion_service import URLIngestionService
from app.storage.sqlite_registry import SQLiteRegistry


class _ChatProvider:
    def chat(self, messages):
        return "This is a focused summary. It names the article topic clearly."


class _Embeddings:
    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _VectorStore:
    def __init__(self):
        self.calls = []

    def upsert_chunks(self, chunks, embeddings):
        self.calls.append((chunks, embeddings))


class _Coordinator:
    def __init__(self, registry):
        self.registry = registry
        self.calls = []

    def ingest_text(self, content, title, source_url, extra_metadata=None):
        self.calls.append(
            {
                "content": content,
                "title": title,
                "source_url": source_url,
                "extra_metadata": extra_metadata or {},
            }
        )
        doc = ParsedDocument(
            source_path=Path("/url/test"),
            filename=title,
            extension=".url",
            checksum_sha256="b" * 64,
            parser_name="url_scraper",
            content=content,
            char_count=len(content),
            metadata={
                "source_type": "url",
                "source_url": source_url,
                "title": title,
                **(extra_metadata or {}),
            },
        )
        self.registry.upsert_document("doc-url", doc)
        self.registry.set_document_source("doc-url", source_type="url", source_url=source_url)
        return "doc-url", 2


def _response(url, text, status_code=200):
    request = httpx.Request("GET", url)
    return httpx.Response(status_code, text=text, request=request)


def _service(registry, coordinator=None, min_words=3):
    return URLIngestionService(
        ingest_coordinator=coordinator or _Coordinator(registry),
        registry=registry,
        chat_provider=_ChatProvider(),
        min_words=min_words,
    )


def test_url_detection_and_extraction(tmp_path):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        service = _service(registry)

        assert service.is_url("https://example.com/article")
        assert not service.is_url("save this https://example.com/article")
        assert service.extract_url("save this https://foo.com/bar, thanks") == "https://foo.com/bar"
    finally:
        registry.close()


def test_scrape_prefers_article_and_strips_page_chrome(tmp_path, monkeypatch):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    html = """
    <html>
      <head><title>Example Article</title></head>
      <body>
        <nav>navigation words</nav>
        <article><h1>Headline</h1><p>alpha beta gamma delta</p></article>
        <footer>footer words</footer>
      </body>
    </html>
    """

    def fake_get(url, **kwargs):
        return _response(url, html)

    monkeypatch.setattr("app.services.url_ingestion_service.httpx.get", fake_get)
    try:
        page = _service(registry).scrape("https://example.com/article")

        assert page.title == "Example Article"
        assert "Headline" in page.content
        assert "navigation words" not in page.content
        assert "footer words" not in page.content
        assert page.word_count == 5
    finally:
        registry.close()


def test_ingest_writes_document_and_dedups(tmp_path, monkeypatch):
    registry = SQLiteRegistry(tmp_path / "registry.db")
    coordinator = _Coordinator(registry)
    html = """
    <html><head><title>Saved Article</title></head>
    <body><main>one two three four five six seven eight nine ten</main></body></html>
    """

    def fake_get(url, **kwargs):
        return _response(url, html)

    monkeypatch.setattr("app.services.url_ingestion_service.httpx.get", fake_get)
    try:
        service = _service(registry, coordinator=coordinator)

        result = service.ingest("https://example.com/saved")
        duplicate = service.ingest("https://example.com/saved")

        assert result.success is True
        assert result.title == "Saved Article"
        assert result.chunks_added == 2
        assert registry.is_url_ingested("https://example.com/saved") is True
        assert service.list_url_sources()[0]["source_url"] == "https://example.com/saved"
        assert duplicate.already_existed is True
        assert len(coordinator.calls) == 1
    finally:
        registry.close()


def test_ingest_error_paths_return_result_not_exception(tmp_path, monkeypatch):
    registry = SQLiteRegistry(tmp_path / "registry.db")

    def timeout_get(url, **kwargs):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr("app.services.url_ingestion_service.httpx.get", timeout_get)
    try:
        result = _service(registry).ingest("https://example.com/slow")

        assert result.success is False
        assert result.error == "timeout"
    finally:
        registry.close()
