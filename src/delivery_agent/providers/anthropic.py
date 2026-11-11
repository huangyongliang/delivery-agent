"""Anthropic Messages API provider."""

from __future__ import annotations

import httpx

from delivery_agent.errors import ProviderError
from delivery_agent.providers.base import ChatMessage, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        version: str,
        max_tokens: int,
        temperature: float = 0.2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.version = version
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is empty")
        if self._client is None:
            raise ProviderError("Anthropic provider has no HTTP client")
        system_parts = [message.content for message in messages if message.role == "system"]
        dialogue = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role != "system"
        ]
        if not dialogue:
            raise ProviderError("Anthropic request has no user message")
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": dialogue,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
        }
        response = await self._client.post(
            f"{self.base_url}/v1/messages",
            json=payload,
            headers=headers,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise ProviderError(f"Anthropic API error {response.status_code}: {detail}")
        try:
            data = response.json()
            blocks = data["content"]
            text = "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Anthropic API returned an unexpected body") from exc
        if not text.strip():
            raise ProviderError("Anthropic API returned an empty completion")
        return LLMResponse(text=text, model=str(data.get("model", self.model)), provider=self.name)
