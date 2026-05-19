from app.services.web_search_service import WebSearchService


class _FakeDDGS:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text(self, query, **kwargs):
        self.calls.append(kwargs.get("backend"))
        if kwargs.get("backend") is None:
            return []
        return [
            {
                "title": "Python For Beginners",
                "href": "https://www.python.org/about/gettingstarted/",
                "body": "Start learning Python with official beginner resources.",
            }
        ]


def test_duckduckgo_search_tries_fallback_backend_when_default_is_empty(monkeypatch):
    _FakeDDGS.calls = []
    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)

    service = WebSearchService(provider="duckduckgo", max_results=3)
    results = service.search("Python tutorials for beginners")

    assert _FakeDDGS.calls == [None, "lite"]
    assert results[0].title == "Python For Beginners"
    assert results[0].url == "https://www.python.org/about/gettingstarted/"
