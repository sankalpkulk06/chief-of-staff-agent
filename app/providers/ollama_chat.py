from typing import Any, List, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.providers.ollama_embeddings import OllamaProviderError, TransientProviderError


class OllamaChatProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 60,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._session = session or requests.Session()

    def generate(self, prompt: str) -> str:
        return self._generate_with_retry(prompt)

    def chat(self, messages: List[dict[str, Any]]) -> str:
        """Generate a response using /api/chat with message history support."""
        return self._chat_with_retry(messages)

    def _generate_with_retry(self, prompt: str) -> str:
        @retry(
            retry=retry_if_exception_type(TransientProviderError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _call() -> str:
            payload = {"model": self._model, "prompt": prompt, "stream": False}
            url = f"{self._base_url}/api/generate"
            try:
                response = self._session.post(url, json=payload, timeout=self._timeout_seconds)
            except requests.RequestException as exc:
                raise TransientProviderError(f"Failed to connect to Ollama chat API: {exc}") from exc

            if response.status_code >= 500:
                raise TransientProviderError(
                    f"Ollama chat request failed with status {response.status_code}: {response.text}"
                )
            if response.status_code >= 400:
                raise OllamaProviderError(
                    f"Ollama chat request failed with status {response.status_code}: {response.text}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise OllamaProviderError("Ollama chat API returned invalid JSON") from exc

            answer = data.get("response")
            if not isinstance(answer, str):
                raise OllamaProviderError("Ollama chat API response missing 'response' text")

            return answer.strip()

        return _call()

    def _chat_with_retry(self, messages: List[dict[str, Any]]) -> str:
        @retry(
            retry=retry_if_exception_type(TransientProviderError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _call() -> str:
            payload = {"model": self._model, "messages": messages, "stream": False}
            url = f"{self._base_url}/api/chat"
            try:
                response = self._session.post(url, json=payload, timeout=self._timeout_seconds)
            except requests.RequestException as exc:
                raise TransientProviderError(f"Failed to connect to Ollama chat API: {exc}") from exc

            if response.status_code >= 500:
                raise TransientProviderError(
                    f"Ollama chat request failed with status {response.status_code}: {response.text}"
                )
            if response.status_code >= 400:
                raise OllamaProviderError(
                    f"Ollama chat request failed with status {response.status_code}: {response.text}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise OllamaProviderError("Ollama chat API returned invalid JSON") from exc

            message = data.get("message")
            if not isinstance(message, dict):
                raise OllamaProviderError("Ollama chat API response missing 'message' dict")

            answer = message.get("content")
            if not isinstance(answer, str):
                raise OllamaProviderError("Ollama chat API response message missing 'content' text")

            return answer.strip()

        return _call()

