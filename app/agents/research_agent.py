"""Research agent — web search and live news fetching."""
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.agents.prompts import load
from app.providers.ollama_chat import OllamaChatProvider
from app.services.news_service import NewsService
from app.services.news_service import NewsArticle
from app.services.web_search_service import SearchResult
from app.services.web_search_service import WebSearchService

# Strips LLM meta-language so the actual search term reaches the API.
# Applied repeatedly until no pattern matches.
# ORDER MATTERS: more specific patterns must come before general ones.
_META_PATTERNS = [
    # "with a quick search tell me..." / "with a search find..."
    re.compile(r"^with\s+(a\s+)?(quick\s+)?(search|look|scan)\s+", re.IGNORECASE),
    # "also get me the news on..." / "also tell me..." / "also find..."
    re.compile(r"^also\s+(get\s+me\s+|tell\s+me\s+|find\s+|fetch\s+)?", re.IGNORECASE),
    # "get me the news on..." / "get me the..." / "get me..."
    re.compile(r"^get\s+me\s+(the\s+)?(news\s+(on|about)\s+|latest\s+)?", re.IGNORECASE),
    # "the news on/about..." / "the latest..."  (leftover after stripping "get me")
    re.compile(r"^the\s+(news\s+(on|about)\s+|latest\s+news\s+(on|about)\s+)", re.IGNORECASE),
    # "please do a quick search for..." / "search the web for..." / "look up..." / "find..."
    re.compile(
        r"^(please\s+)?(do\s+a\s+)?(quick\s+)?"
        r"(search(\s+the\s+web)?(\s+for)?|look\s+up|find|research|google|fetch)\s+"
        r"(information\s+(about|on)\s+|info\s+(about|on)\s+|about\s+|on\s+)?",
        re.IGNORECASE,
    ),
    # "tell me about..." / "tell me what..." / "tell me the..." / "explain..." / "what is/does..."
    re.compile(
        r"^(tell\s+me\s+(about|what|how|the)\s+|explain\s+|what\s+(is|are|does|do)\s+)",
        re.IGNORECASE,
    ),
]


def _clean_query(task: str) -> str:
    """Strip orchestrator meta-language to get a bare search query."""
    q = task.strip()
    # Apply patterns iteratively until none match
    changed = True
    while changed:
        changed = False
        for pattern in _META_PATTERNS:
            new_q = pattern.sub("", q).strip()
            if new_q != q:
                q = new_q
                changed = True
    q = q.rstrip("?.!").strip()
    return q or task

_SUMMARIZE_SYSTEM = load("research_summarize")


def _field(name: str, value: Optional[str]) -> str:
    return f"{name}: {value.strip() if value else 'Not provided'}"


def _format_news_articles(articles: List[NewsArticle]) -> str:
    blocks = []
    for i, article in enumerate(articles, 1):
        blocks.append(
            "\n".join(
                [
                    f"Result {i}",
                    _field("Title", article.title),
                    _field("Source", article.source),
                    _field("URL", article.url),
                    _field("Published", article.published),
                    _field("Snippet", article.snippet),
                ]
            )
        )
    return "\n\n".join(blocks)


def _format_web_results(results: List[SearchResult]) -> str:
    blocks = []
    for i, result in enumerate(results, 1):
        blocks.append(
            "\n".join(
                [
                    f"Result {i}",
                    _field("Title", result.title),
                    _field("URL", result.url),
                    _field("Published", result.published_date),
                    _field("Snippet", result.snippet),
                ]
            )
        )
    return "\n\n".join(blocks)


class ResearchAgent:
    """Fetches live web / news data and returns a summarized answer."""

    def __init__(
        self,
        chat_provider: OllamaChatProvider,
        web_search_service: Optional[WebSearchService] = None,
        news_service: Optional[NewsService] = None,
    ):
        self._provider = chat_provider
        self._web_search = web_search_service
        self._news = news_service

    def execute(
        self,
        task: str,
        original_question: str,
        history: List[dict[str, Any]],
        previous_results: Optional[List[AgentResult]] = None,
        user_id: Optional[str] = None,
    ) -> AgentResult:
        # Determine whether this is a news or general web search query.
        task_lower = task.lower()

        # The orchestrator always prefixes tasks with "fetch_news:" or "web_search:".
        # Trust that prefix — no keyword heuristics.
        if task_lower.startswith("fetch_news:"):
            clean_task = task[len("fetch_news:"):].strip()
            if self._news:
                return self._fetch_news(clean_task)
            # news blocked (common on GCP) — fall through to web search
            clean_task = clean_task  # use same query
        elif task_lower.startswith("web_search:") or task_lower.startswith("web search:"):
            prefix_len = len("web_search:") if task_lower.startswith("web_search:") else len("web search:")
            clean_task = task[prefix_len:].strip()
        else:
            # No prefix — shouldn't happen with the new prompt, but handle gracefully
            clean_task = task

        if self._web_search:
            return self._web_search_query(clean_task)
        if self._news:
            return self._fetch_news(clean_task)

        return AgentResult(
            agent="research_agent",
            task=task,
            output="No web search or news service is configured.",
            success=False,
            error="no_service",
        )

    # ------------------------------------------------------------------

    def _fetch_news(self, task: str) -> AgentResult:
        try:
            query = _clean_query(task)
            articles = self._news.search_news(query)
            if not articles and self._web_search:
                # Google News RSS is blocked from GCP IPs — fall back to web search.
                return self._web_search_query(task)
            if not articles:
                return AgentResult(
                    agent="research_agent",
                    task=task,
                    output=f"No news found for '{query}'.",
                    success=True,
                )

            articles_text = _format_news_articles(articles[:6])

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": f"Task: {task}\n\nNews articles:\n{articles_text}\n\nSummary:"},
            ]
            summary = self._provider.chat(messages=messages)

            source_links = [{"title": a.title, "url": a.url, "source": a.source} for a in articles[:6]]
            return AgentResult(
                agent="research_agent",
                task=task,
                output=summary,
                success=True,
                citations=source_links,
                metadata={"source": "news", "article_count": len(articles)},
            )
        except Exception as exc:
            if self._web_search:
                return self._web_search_query(task)
            return AgentResult(
                agent="research_agent",
                task=task,
                output="",
                success=False,
                error=f"News fetch failed: {exc}",
            )

    def _web_search_query(self, task: str) -> AgentResult:
        try:
            query = _clean_query(task)
            results = self._web_search.search(query)
            if not results:
                return AgentResult(
                    agent="research_agent",
                    task=task,
                    output=f"No web results found for '{query}'.",
                    success=True,
                )

            formatted = _format_web_results(results)

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": f"Task: {task}\n\nSearch results:\n{formatted}\n\nSummary:"},
            ]
            summary = self._provider.chat(messages=messages)

            source_links = [{"title": r.title, "url": r.url} for r in results]
            return AgentResult(
                agent="research_agent",
                task=task,
                output=summary,
                success=True,
                citations=source_links,
                metadata={"source": "web", "result_count": len(results)},
            )
        except Exception as exc:
            return AgentResult(
                agent="research_agent",
                task=task,
                output="",
                success=False,
                error=f"Web search failed: {exc}",
            )
