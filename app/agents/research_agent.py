"""Research agent — web search and live news fetching."""
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.providers.ollama_chat import OllamaChatProvider
from app.services.news_service import NewsService
from app.services.web_search_service import WebSearchService

_SUMMARIZE_SYSTEM = """\
You are a research assistant for a personal AI called Sage. \
Summarize the following search results clearly and concisely. \
Cite sources by title. Only include what is actually in the results."""


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
    ) -> AgentResult:
        # Determine whether this is a news or general web search query.
        task_lower = task.lower()
        is_news = any(w in task_lower for w in ("news", "latest", "recent", "today", "happened"))

        if is_news and self._news:
            return self._fetch_news(task)
        if self._web_search:
            return self._web_search_query(task)
        if self._news:
            return self._fetch_news(task)

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
            articles = self._news.search_news(task) or self._news.get_top_news()
            if not articles:
                return AgentResult(
                    agent="research_agent",
                    task=task,
                    output=f"No news found for '{task}'.",
                    success=True,
                )

            # Build a text block for the LLM to summarize.
            lines = []
            for i, a in enumerate(articles[:6], 1):
                lines.append(f"[{i}] {a.title} ({a.source})\n{a.snippet or a.url}")
            articles_text = "\n\n".join(lines)

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
            return AgentResult(
                agent="research_agent",
                task=task,
                output="",
                success=False,
                error=f"News fetch failed: {exc}",
            )

    def _web_search_query(self, task: str) -> AgentResult:
        try:
            results = self._web_search.search(task)
            if not results:
                return AgentResult(
                    agent="research_agent",
                    task=task,
                    output=f"No web results found for '{task}'.",
                    success=True,
                )

            formatted = self._web_search.format_for_context(results)

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
