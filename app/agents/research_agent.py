"""Research agent — web search and live news fetching."""
import re
from typing import Any, List, Optional

from app.agents.base import AgentResult
from app.providers.ollama_chat import OllamaChatProvider
from app.services.news_service import NewsService
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
        user_id: Optional[str] = None,
    ) -> AgentResult:
        # Determine whether this is a news or general web search query.
        task_lower = task.lower()

        # Honour explicit routing prefix set by the orchestrator's rule-based planner.
        if task_lower.startswith("web search:"):
            clean_task = task[len("web search:"):].strip()
            return self._web_search_query(clean_task) if self._web_search else AgentResult(
                agent="research_agent", task=task, output="Web search is not configured.", success=False, error="no_web_search"
            )
        if task_lower.startswith("news:"):
            clean_task = task[len("news:"):].strip()
            return self._fetch_news(clean_task) if self._news else AgentResult(
                agent="research_agent", task=task, output="News service is not configured.", success=False, error="no_news"
            )

        # Heuristic routing when no prefix is present.
        is_news = any(w in task_lower for w in ("news", "latest", "recent", "today", "happened", "headlines"))
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
