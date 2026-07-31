from typing import Any, List, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.providers.ollama_embeddings import OllamaProviderError, TransientProviderError
from app.providers.tool_types import Tool, ToolCall, ToolChatResult


class GeminiChatProvider:
    """Chat provider for Google Gemini via the generateContent REST API."""

    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout_seconds: int = 60,
        max_retries: int = 3,
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._session = session or requests.Session()

    def generate(self, prompt: str) -> str:
        return self.chat(messages=[{"role": "user", "content": prompt}])

    def chat(self, messages: List[dict[str, Any]]) -> str:
        system_parts, contents = self._convert_messages(messages)
        payload: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.2}}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        data = self._request(payload)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise OllamaProviderError(f"Unexpected Gemini response shape: {data}") from exc

    def chat_tools(self, messages: List[dict[str, Any]], tools: List[Tool],
                   tool_choice: str = "auto") -> ToolChatResult:
        """Gemini function-calling. Returns normalized content + tool calls."""
        system_parts, contents = self._convert_messages(messages)
        payload: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": 0.2}}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        payload["tools"] = [{"functionDeclarations": [
            {"name": t.name, "description": t.description,
             "parameters": self._to_gemini_schema(t.parameters)}
            for t in tools
        ]}]
        mode = "ANY" if tool_choice == "required" else ("NONE" if tool_choice == "none" else "AUTO")
        payload["toolConfig"] = {"functionCallingConfig": {"mode": mode}}

        data = self._request(payload)
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            parts = []
        content_text = None
        calls: List[ToolCall] = []
        for p in parts if isinstance(parts, list) else []:
            if isinstance(p, dict) and "functionCall" in p:
                fc = p["functionCall"] or {}
                calls.append(ToolCall(name=fc.get("name", ""), arguments=dict(fc.get("args") or {})))
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                content_text = (content_text or "") + p["text"]
        return ToolChatResult(content=content_text.strip() if content_text else None, tool_calls=calls)

    def _request(self, payload: dict) -> dict:
        @retry(
            retry=retry_if_exception_type(TransientProviderError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _call() -> dict:
            if not self._api_key:
                raise OllamaProviderError("GEMINI_API_KEY is required for Gemini chat models")
            url = f"{self._BASE_URL}/{self._model}:generateContent?key={self._api_key}"
            try:
                response = self._session.post(url, json=payload, timeout=self._timeout_seconds)
            except requests.RequestException as exc:
                raise TransientProviderError(f"Failed to connect to Gemini API: {exc}") from exc

            if response.status_code >= 500:
                raise TransientProviderError(
                    f"Gemini request failed with status {response.status_code}: {response.text}"
                )
            if response.status_code >= 400:
                raise OllamaProviderError(
                    f"Gemini request failed with status {response.status_code}: {response.text}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise OllamaProviderError("Gemini API returned invalid JSON") from exc

        return _call()

    @staticmethod
    def _to_gemini_schema(schema: Any) -> Any:
        """Recursively upcase JSON-Schema ``type`` values to Gemini's OpenAPI enum
        (string→STRING, object→OBJECT, …) so one provider-agnostic Tool schema works for both."""
        if not isinstance(schema, dict):
            return schema
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            elif k == "properties" and isinstance(v, dict):
                out[k] = {pk: GeminiChatProvider._to_gemini_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = GeminiChatProvider._to_gemini_schema(v)
            else:
                out[k] = v
        return out

    @staticmethod
    def _convert_messages(
        messages: List[dict[str, Any]],
    ) -> tuple[list[dict], list[dict]]:
        """Split OpenAI-style messages into Gemini systemInstruction + contents."""
        system_parts: list[dict] = []
        contents: list[dict] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append({"text": content})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        # Gemini requires at least one user turn
        if not contents:
            contents.append({"role": "user", "parts": [{"text": "Hello"}]})

        return system_parts, contents
