from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    published_date: Optional[str] = None


class WebSearchService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        provider: str = "tavily",
        max_results: int = 5,
    ):
        self._api_key = api_key
        self._max_results = max_results
        # Use Tavily only when explicitly requested AND a key is available
        if provider == "tavily" and api_key:
            self._provider = "tavily"
        else:
            self._provider = "duckduckgo"

    def search(self, query: str) -> List[SearchResult]:
        if self._provider == "tavily":
            return self._search_tavily(query)
        return self._search_duckduckgo(query)

    def format_for_context(self, results: List[SearchResult]) -> str:
        if not results:
            return "No web search results found."
        parts = []
        for i, r in enumerate(results, 1):
            date_str = f" ({r.published_date})" if r.published_date else ""
            parts.append(f"[{i}] {r.title}{date_str}\n    {r.snippet}\n    {r.url}")
        return "\n\n".join(parts)

    def format_citations(self, results: List[SearchResult]) -> str:
        if not results:
            return ""
        lines = ["web sources:"]
        for i, r in enumerate(results, 1):
            lines.append(f"- [{i}] {r.title} — {r.url}")
        return "\n".join(lines)

    def _search_tavily(self, query: str) -> List[SearchResult]:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._api_key)
            response = client.search(query=query, max_results=self._max_results)
            results = []
            for r in response.get("results", []):
                results.append(SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    published_date=r.get("published_date"),
                ))
            return results
        except Exception:
            return self._search_duckduckgo(query)

    def _search_duckduckgo(self, query: str) -> List[SearchResult]:
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=self._max_results):
                    results.append(SearchResult(
                        title=r.get("title", ""),
                        url=r.get("href", ""),
                        snippet=r.get("body", ""),
                    ))
            return results
        except Exception:
            return []
