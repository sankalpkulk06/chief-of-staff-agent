import json
from typing import Any, List, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.providers.ollama_embeddings import OllamaProviderError, TransientProviderError
from app.providers.tool_types import Tool, ToolCall, ToolChatResult


class GroqChatProvider:
    """Chat provider for Groq's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: int = 60,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._session = session or requests.Session()

    def generate(self, prompt: str) -> str:
        return self.chat(messages=[{"role": "user", "content": prompt}])

    def chat(self, messages: List[dict[str, Any]]) -> str:
        data = self._request({"model": self._model, "messages": messages, "temperature": 0.2})
        answer = self._message(data).get("content")
        if not isinstance(answer, str):
            raise OllamaProviderError("Groq chat API response message missing content")
        return answer.strip()

    def chat_tools(self, messages: List[dict[str, Any]], tools: List[Tool],
                   tool_choice: str = "auto") -> ToolChatResult:
        """OpenAI-style tool-calling. Returns normalized content + tool calls."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "tools": [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ],
        }
        if tool_choice:
            payload["tool_choice"] = tool_choice
        message = self._message(self._request(payload))
        content = message.get("content")
        calls: List[ToolCall] = []
        for tc in (message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (ValueError, TypeError):
                args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args or {}, id=tc.get("id", "")))
        return ToolChatResult(content=content if isinstance(content, str) else None, tool_calls=calls)

    @staticmethod
    def _message(data: dict) -> dict:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OllamaProviderError("Groq chat API response missing choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise OllamaProviderError("Groq chat API response choice missing message")
        return message

    def _request(self, payload: dict) -> dict:
        @retry(
            retry=retry_if_exception_type(TransientProviderError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _call() -> dict:
            if not self._api_key:
                raise OllamaProviderError("GROQ_API_KEY is required for Groq chat models")
            url = f"{self._base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            try:
                response = self._session.post(
                    url, json=payload, headers=headers, timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                raise TransientProviderError(f"Failed to connect to Groq chat API: {exc}") from exc

            if response.status_code >= 500:
                raise TransientProviderError(
                    f"Groq chat request failed with status {response.status_code}: {response.text}"
                )
            if response.status_code >= 400:
                raise OllamaProviderError(
                    f"Groq chat request failed with status {response.status_code}: {response.text}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise OllamaProviderError("Groq chat API returned invalid JSON") from exc

        return _call()
