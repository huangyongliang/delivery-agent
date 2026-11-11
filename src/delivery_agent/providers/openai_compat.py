"""OpenAI-compatible Chat Completions provider.

Works with OpenAI and with servers that implement the same API, including
vLLM, Ollama's OpenAI endpoint, and most hosted compatible gateways.
"""

from __future__ import annotations

import httpx

from delivery_agent.errors import ProviderError
from delivery_agent.providers.base import ChatMessage, LLMProvider, LLMResponse


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        json_mode: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.json_mode_enabled = json_mode
        self._client = client

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        if self._client is None:
            raise ProviderError("OpenAI-compatible provider has no HTTP client")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self.temperature,
        }
        if json_mode and self.json_mode_enabled:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"OpenAI-compatible API error {response.status_code}: {response.text[:500]}"
            )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("OpenAI-compatible API returned an unexpected body") from exc
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("OpenAI-compatible API returned an empty completion")
        model = data.get("model", self.model)
        return LLMResponse(text=text, model=str(model), provider=self.name)
