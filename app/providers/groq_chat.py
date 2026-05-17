from typing import Any, List, Optional

import requests

from app.providers.ollama_embeddings import OllamaProviderError


class GroqChatProvider:
    """Chat provider for Groq's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: int = 60,
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()

    def generate(self, prompt: str) -> str:
        return self.chat(messages=[{"role": "user", "content": prompt}])

    def chat(self, messages: List[dict[str, Any]]) -> str:
        if not self._api_key:
            raise OllamaProviderError("GROQ_API_KEY is required for Groq chat models")

        payload = {"model": self._model, "messages": messages, "temperature": 0.2}
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OllamaProviderError(f"Failed to connect to Groq chat API: {exc}") from exc

        if response.status_code >= 400:
            raise OllamaProviderError(
                f"Groq chat request failed with status {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaProviderError("Groq chat API returned invalid JSON") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OllamaProviderError("Groq chat API response missing choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise OllamaProviderError("Groq chat API response choice missing message")

        answer = message.get("content")
        if not isinstance(answer, str):
            raise OllamaProviderError("Groq chat API response message missing content")

        return answer.strip()
