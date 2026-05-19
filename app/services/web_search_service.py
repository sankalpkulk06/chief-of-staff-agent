from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse


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
        results: List[SearchResult] = []
        seen_urls: set[str] = set()
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            # The DDG package can intermittently return an empty list for one
            # backend while another backend has good results. Try a small set
            # before giving up so obvious searches do not vanish.
            for backend in (None, "lite", "html"):
                kwargs = {"max_results": self._max_results}
                if backend is not None:
                    kwargs["backend"] = backend
                with DDGS() as ddgs:
                    for r in ddgs.text(query, **kwargs):
                        url = r.get("href", "")
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        results.append(SearchResult(
                            title=r.get("title", ""),
                            url=url,
                            snippet=r.get("body", ""),
                        ))
                        if len(results) >= self._max_results:
                            return results
                if results:
                    return results
            return results or self._search_duckduckgo_lite(query, seen_urls)
        except Exception:
            return self._search_duckduckgo_lite(query, seen_urls)

    def _search_duckduckgo_lite(self, query: str, seen_urls: Optional[set[str]] = None) -> List[SearchResult]:
        try:
            import requests
            from bs4 import BeautifulSoup
        except Exception:
            return []

        seen_urls = seen_urls or set()
        try:
            url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results: List[SearchResult] = []
            for link in soup.select("a.result-link"):
                title = link.get_text(" ", strip=True)
                raw_url = link.get("href") or ""
                result_url = self._unwrap_duckduckgo_url(raw_url)
                if not title or not result_url or result_url in seen_urls:
                    continue
                seen_urls.add(result_url)
                results.append(SearchResult(title=title, url=result_url, snippet=""))
                if len(results) >= self._max_results:
                    break
            return results
        except Exception:
            return []

    @staticmethod
    def _unwrap_duckduckgo_url(raw_url: str) -> str:
        if not raw_url:
            return ""
        if raw_url.startswith("//"):
            raw_url = f"https:{raw_url}"
        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return unquote(uddg[0])
        return raw_url
