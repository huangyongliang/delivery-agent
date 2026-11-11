"""Generic HTTP adapter for models that are not worth a dedicated client yet.

``openai`` style posts a Chat Completions body to HTTP_LLM_URL (the full endpoint).
``prompt`` style posts ``{"model", "prompt"}`` and accepts several response shapes.
"""

from __future__ import annotations

import httpx

from delivery_agent.errors import ProviderError
from delivery_agent.providers.base import ChatMessage, LLMProvider, LLMResponse


class HttpGenericProvider(LLMProvider):
    name = "http"

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        style: str,
        temperature: float = 0.2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url.strip()
        self.api_key = api_key
        self.model = model
        self.style = style
        self.temperature = temperature
        self._client = client

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.url:
            raise ProviderError("HTTP_LLM_URL is empty")
        if self._client is None:
            raise ProviderError("HTTP provider has no HTTP client")
        if self.style == "openai":
            payload: dict[str, object] = {
                "model": self.model,
                "messages": [message.model_dump() for message in messages],
                "temperature": self.temperature,
            }
        elif self.style == "prompt":
            payload = {"model": self.model, "prompt": _render_prompt(messages)}
        else:
            raise ProviderError(f"unsupported HTTP_LLM_STYLE: {self.style}")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._client.post(self.url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise ProviderError(f"HTTP LLM error {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("HTTP LLM returned non-JSON content") from exc
        text = _extract_text(data)
        if text is None or not text.strip():
            raise ProviderError("HTTP LLM returned an empty completion")
        return LLMResponse(text=text, model=self.model, provider=self.name)


def _render_prompt(messages: list[ChatMessage]) -> str:
    return "\n\n".join(f"{message.role}:\n{message.content}" for message in messages)


def _extract_text(data: object) -> str | None:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return None
    for key in ("content", "text", "response", "output"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    return None
