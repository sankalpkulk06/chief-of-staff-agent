import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.ingest_coordinator import IngestCoordinator
from app.storage.sqlite_registry import SQLiteRegistry

_URL_RE = re.compile(r"https?://[^\s<>\"']+")

_INGEST_TRIGGERS = re.compile(
    r"\b(remember|save|store|ingest|add to (knowledge base|rag|my docs))\b",
    re.IGNORECASE,
)


@dataclass
class ScrapedPage:
    url: str
    title: str
    content: str
    word_count: int
    scraped_at: datetime


@dataclass
class URLIngestionResult:
    success: bool
    url: str
    title: Optional[str]
    summary: Optional[str]
    chunks_added: int
    already_existed: bool
    error: Optional[str]


class URLIngestionService:
    def __init__(
        self,
        ingest_coordinator: IngestCoordinator,
        registry: SQLiteRegistry,
        chat_provider,
        timeout: int = 10,
        min_words: int = 100,
        max_words: int = 50000,
    ):
        self._coordinator = ingest_coordinator
        self._registry = registry
        self._chat_provider = chat_provider
        self._timeout = timeout
        self._min_words = min_words
        self._max_words = max_words

    def is_url(self, text: str) -> bool:
        stripped = text.strip().rstrip(".,)")
        return _URL_RE.fullmatch(stripped) is not None

    def extract_url(self, text: str) -> Optional[str]:
        match = _URL_RE.search(text)
        return match.group(0).rstrip(".,)") if match else None

    def is_ingest_intent(self, text: str) -> bool:
        """True if message is a bare URL or has an explicit save/remember trigger."""
        stripped = text.strip()
        if _URL_RE.fullmatch(stripped.rstrip(".,)")):
            return True
        if _INGEST_TRIGGERS.search(stripped) and _URL_RE.search(stripped):
            return True
        return False

    def already_ingested(self, url: str) -> bool:
        return self._registry.is_url_ingested(url)

    def list_url_sources(self) -> list[dict]:
        return self._registry.list_url_sources()

    def ingest(self, url: str) -> URLIngestionResult:
        if self.already_ingested(url):
            return URLIngestionResult(
                success=True,
                url=url,
                title=None,
                summary=None,
                chunks_added=0,
                already_existed=True,
                error=None,
            )

        try:
            page = self.scrape(url)
        except _ScrapeError as exc:
            return URLIngestionResult(
                success=False, url=url, title=None, summary=None,
                chunks_added=0, already_existed=False, error=exc.code,
            )

        if page.word_count < self._min_words:
            return URLIngestionResult(
                success=False, url=url, title=page.title, summary=None,
                chunks_added=0, already_existed=False, error="too_short",
            )

        summary = self._summarize(page.title, page.content)

        _, chunks_added = self._coordinator.ingest_text(
            content=page.content,
            title=page.title,
            source_url=url,
            extra_metadata={"ingested_at": page.scraped_at.isoformat()},
        )

        return URLIngestionResult(
            success=True,
            url=url,
            title=page.title,
            summary=summary,
            chunks_added=chunks_added,
            already_existed=False,
            error=None,
        )

    def scrape(self, url: str) -> ScrapedPage:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Sage personal assistant)"},
                timeout=self._timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise _ScrapeError("timeout")
        except httpx.HTTPStatusError as exc:
            raise _ScrapeError(f"http_{exc.response.status_code}")
        except Exception:
            raise _ScrapeError("unreachable")

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["nav", "footer", "header", "script", "style", "aside", "noscript"]):
            tag.decompose()

        title_el = soup.find("title")
        title = title_el.get_text(strip=True) if title_el else urlparse(url).netloc

        main = soup.find("article") or soup.find("main") or soup.find("body")
        content = main.get_text(separator="\n", strip=True) if main else ""
        words = content.split()
        if len(words) > self._max_words:
            content = " ".join(words[: self._max_words])
            words = content.split()

        return ScrapedPage(
            url=url,
            title=title,
            content=content,
            word_count=len(words),
            scraped_at=datetime.utcnow(),
        )

    def _summarize(self, title: str, content: str) -> str:
        first_500 = " ".join(content.split()[:500])
        prompt = (
            f"Summarize the following article in exactly two sentences for a "
            f"knowledge-base confirmation message. Be specific — no filler.\n\n"
            f"TITLE: {title}\n\nARTICLE:\n{first_500}"
        )
        try:
            return self._chat_provider.chat(messages=[
                {"role": "user", "content": prompt}
            ]).strip()
        except Exception:
            return f"Saved: {title}"

    def format_confirmation(self, result: URLIngestionResult, whatsapp: bool = False) -> str:
        if result.already_existed:
            return "Already saved that one! Ask me anything about it."

        if not result.success:
            return _ERROR_MESSAGES.get(result.error or "", _ERROR_MESSAGES["default"])

        domain = urlparse(result.url).netloc
        if whatsapp:
            return (
                f"📥 *Saved to your knowledge base!*\n"
                f"*Title:* {result.title}\n"
                f"*Source:* {domain}\n"
                f"*Summary:* {result.summary}\n\n"
                f"Ask me anything about it."
            )
        return (
            f"📥 Saved to your knowledge base!\n"
            f"Title: {result.title}\n"
            f"Source: {domain}\n"
            f"Summary: {result.summary}\n\n"
            f"You can now ask me questions about it."
        )


_ERROR_MESSAGES = {
    "timeout": "The page took too long to load. Try again or paste the article text directly.",
    "too_short": "That page doesn't have much readable content — it might be a login wall or redirect.",
    "unreachable": "Couldn't access that page — it may be behind a login or no longer exists.",
    "default": "Couldn't access that page — it may be behind a login or no longer exists.",
}

for _code in range(400, 600):
    _ERROR_MESSAGES[f"http_{_code}"] = _ERROR_MESSAGES["unreachable"]


class _ScrapeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
