from typing import List, Optional

import requests


class OllamaProviderError(Exception):
    """Raised when an Ollama provider request fails."""


class OllamaEmbeddingsProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
        session: Optional[requests.Session] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        payload = {"model": self._model, "prompt": text}
        url = f"{self._base_url}/api/embeddings"
        try:
            response = self._session.post(url, json=payload, timeout=self._timeout_seconds)
        except requests.RequestException as exc:
            raise OllamaProviderError(f"Failed to connect to Ollama embeddings API: {exc}") from exc

        if response.status_code >= 400:
            raise OllamaProviderError(
                f"Ollama embeddings request failed with status {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaProviderError("Ollama embeddings API returned invalid JSON") from exc

        embedding = data.get("embedding")
        if not isinstance(embedding, list):
            raise OllamaProviderError("Ollama embeddings API response missing 'embedding' list")

        return [float(value) for value in embedding]

