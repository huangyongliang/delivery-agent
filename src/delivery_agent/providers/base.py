"""LLM provider interface. Execution never depends on a specific vendor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

from delivery_agent.errors import ProviderError

__all__ = ["ChatMessage", "LLMProvider", "LLMResponse", "ProviderError"]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str


class LLMProvider(ABC):
    """One completion call. Implementations must be safe to construct without a network."""

    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Return the model's text. Raise ProviderError when the call cannot be made."""
