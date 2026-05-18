from typing import List, Optional

import requests

from app.providers.ollama_embeddings import OllamaProviderError


class HuggingFaceEmbeddingsProvider:
    """Embeddings provider backed by Hugging Face Inference API."""

    def __init__(
        self,
        api_key: str,
        model: str = "BAAI/bge-small-en-v1.5",
        base_url: str = "https://api-inference.huggingface.co",
        timeout_seconds: int = 30,
        query_prefix: str = "Represent this sentence for searching relevant passages: ",
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._query_prefix = query_prefix
        self._session = session or requests.Session()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        prefixed = f"{self._query_prefix}{text}" if self._query_prefix else text
        return self._embed([prefixed])[0]

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not self._api_key:
            raise OllamaProviderError("HUGGINGFACE_API_KEY is required for Hugging Face embeddings")
        if not texts:
            return []

        url = f"{self._base_url}/models/{self._model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {"inputs": texts, "options": {"wait_for_model": True}}
        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OllamaProviderError(f"Failed to connect to Hugging Face embeddings API: {exc}") from exc

        if response.status_code >= 400:
            raise OllamaProviderError(
                f"Hugging Face embeddings request failed with status {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaProviderError("Hugging Face embeddings API returned invalid JSON") from exc

        embeddings = self._normalize_response(data, expected_count=len(texts))
        return [[float(value) for value in embedding] for embedding in embeddings]

    def _normalize_response(self, data, expected_count: int) -> List[List[float]]:
        if not isinstance(data, list):
            raise OllamaProviderError("Hugging Face embeddings API response must be a list")
        if expected_count == 1 and data and all(isinstance(value, (int, float)) for value in data):
            return [data]
        if data and all(isinstance(row, list) and row and all(isinstance(value, (int, float)) for value in row) for row in data):
            if expected_count == 1 and len(data) != 1:
                return [self._mean_pool(data)]
            if len(data) != expected_count:
                raise OllamaProviderError(
                    f"Hugging Face embeddings API returned {len(data)} embeddings for {expected_count} inputs"
                )
            return data
        if data and all(isinstance(tokens, list) for tokens in data):
            pooled = [self._mean_pool(tokens) for tokens in data]
            if len(pooled) != expected_count:
                raise OllamaProviderError(
                    f"Hugging Face embeddings API returned {len(pooled)} embeddings for {expected_count} inputs"
                )
            return pooled
        raise OllamaProviderError("Hugging Face embeddings API response missing embedding vectors")

    @staticmethod
    def _mean_pool(tokens) -> List[float]:
        if not tokens or not all(isinstance(row, list) for row in tokens):
            raise OllamaProviderError("Hugging Face token embeddings response is invalid")
        width = len(tokens[0])
        if width == 0 or any(len(row) != width for row in tokens):
            raise OllamaProviderError("Hugging Face token embeddings have inconsistent dimensions")
        return [sum(float(row[i]) for row in tokens) / len(tokens) for i in range(width)]
