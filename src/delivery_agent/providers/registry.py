"""Select an LLM provider from settings."""

from __future__ import annotations

import logging

import httpx

from delivery_agent.config import Settings
from delivery_agent.providers.anthropic import AnthropicProvider
from delivery_agent.providers.base import LLMProvider
from delivery_agent.providers.http_generic import HttpGenericProvider
from delivery_agent.providers.mock import MockProvider
from delivery_agent.providers.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)


def create_provider(
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> LLMProvider | None:
    name = settings.llm_provider.strip().lower()
    if name in {"none", "off", "deterministic"}:
        logger.info("LLM provider disabled; deliveries use the deterministic plan")
        return None
    if name == "mock":
        return MockProvider()
    if name == "openai":
        return OpenAICompatProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            json_mode=settings.openai_json_mode,
            client=client,
        )
    if name == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning(
                "ANTHROPIC_API_KEY is empty; planning will fall back to the deterministic plan"
            )
        return AnthropicProvider(
            base_url=settings.anthropic_base_url,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            version=settings.anthropic_version,
            max_tokens=settings.anthropic_max_tokens,
            temperature=settings.llm_temperature,
            client=client,
        )
    if name == "http":
        if not settings.http_llm_url:
            logger.warning(
                "HTTP_LLM_URL is empty; planning will fall back to the deterministic plan"
            )
        return HttpGenericProvider(
            url=settings.http_llm_url,
            api_key=settings.http_llm_api_key,
            model=settings.http_llm_model,
            style=settings.http_llm_style,
            temperature=settings.llm_temperature,
            client=client,
        )
    raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
