import re
from typing import List, Optional, TYPE_CHECKING

import requests
from lxml import etree
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.providers.ollama_chat import OllamaChatProvider


class NewsArticle(BaseModel):
    """Represents a news article from RSS feed."""
    title: str
    source: str
    url: str
    published: str
    snippet: str = ""


class NewsService:
    """Service for fetching news from Google News RSS."""

    BASE_URL = "https://news.google.com/rss"
    SEARCH_URL_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    def __init__(self, max_results: int = 5):
        self._max_results = max_results

    def search_news(self, query: str, max_results: Optional[int] = None) -> List[NewsArticle]:
        """Search for news articles on a specific topic.

        Args:
            query: Search query (can be a topic or full sentence)
            max_results: Max articles to return (defaults to self._max_results)

        Returns:
            List of NewsArticle objects
        """
        max_results = max_results or self._max_results
        url = self.SEARCH_URL_TEMPLATE.format(query=query)
        return self._parse_feed(url, max_results)

    def get_top_news(self, max_results: Optional[int] = None) -> List[NewsArticle]:
        """Get top news from Google News homepage.

        Args:
            max_results: Max articles to return (defaults to self._max_results)

        Returns:
            List of NewsArticle objects
        """
        max_results = max_results or self._max_results
        url = f"{self.BASE_URL}?hl=en-US&gl=US&ceid=US:en"
        return self._parse_feed(url, max_results)

    def _parse_feed(self, url: str, max_results: int) -> List[NewsArticle]:
        """Parse Google News RSS feed.

        Args:
            url: RSS feed URL
            max_results: Max articles to extract

        Returns:
            List of NewsArticle objects
        """
        try:
            response = requests.get(url, headers={"User-Agent": self.USER_AGENT}, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error fetching news: {e}")
            return []

        try:
            root = etree.fromstring(response.content)
            items = root.findall(".//item")[:max_results]

            articles = []
            for item in items:
                title_elem = item.find("title")
                link_elem = item.find("link")
                pub_date_elem = item.find("pubDate")
                source_elem = item.find("source")
                desc_elem = item.find("description")

                if title_elem is None or link_elem is None:
                    continue

                title = title_elem.text or ""
                url_str = link_elem.text or ""
                published = pub_date_elem.text or "" if pub_date_elem is not None else ""
                source = source_elem.text or self._extract_source_from_title(title)
                snippet = self._clean_snippet(desc_elem.text) if desc_elem is not None else ""

                # Clean title: remove source suffix if present (Google News adds "- Source Name")
                title = re.sub(r"\s*-\s*[A-Za-z0-9\s]+$", "", title).strip()

                article = NewsArticle(
                    title=title,
                    source=source,
                    url=url_str,
                    published=published,
                    snippet=snippet,
                )
                articles.append(article)

            return articles

        except etree.XMLSyntaxError as e:
            print(f"Error parsing RSS feed: {e}")
            return []

    @staticmethod
    def _extract_source_from_title(title: str) -> str:
        """Extract source name from title (e.g., "Title - Source Name").

        Args:
            title: The article title

        Returns:
            Source name or "Unknown" if not found
        """
        match = re.search(r"-\s*([A-Za-z0-9\s]+)$", title)
        if match:
            return match.group(1).strip()
        return "Unknown"

    @staticmethod
    def _clean_snippet(html_snippet: str) -> str:
        """Remove HTML tags from snippet.

        Args:
            html_snippet: HTML snippet

        Returns:
            Clean text snippet
        """
        if not html_snippet:
            return ""
        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", "", html_snippet)
        # Decode HTML entities
        clean = (
            clean.replace("&quot;", '"')
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
        )
        return clean.strip()

    def generate_summary(
        self, articles: List[NewsArticle], chat_provider: "OllamaChatProvider"
    ) -> str:
        """Generate a consolidated summary of news articles under 200 words.

        Args:
            articles: List of NewsArticle objects to summarize
            chat_provider: OllamaChatProvider instance for LLM generation

        Returns:
            A concise summary of the articles under 200 words
        """
        if not articles:
            return "No articles to summarize."

        # Build article context for the LLM
        articles_text = "\n\n".join(
            f"- {article.title}: {article.snippet}" for article in articles
        )

        prompt = f"""Based on these news articles, provide a concise summary under 200 words that captures the key themes and important points:

{articles_text}

Summary:"""

        try:
            summary = chat_provider.generate(prompt)
            return summary
        except Exception as e:
            # Fallback to basic concatenation if LLM fails
            return f"Could not generate summary: {str(e)}"
