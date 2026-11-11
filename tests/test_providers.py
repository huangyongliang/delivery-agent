"""Model provider interface: mock, OpenAI-compatible, Anthropic, and generic HTTP."""

from __future__ import annotations

import json

import httpx
import pytest
from tests.conftest import make_settings

from delivery_agent.errors import ProviderError
from delivery_agent.providers.anthropic import AnthropicProvider
from delivery_agent.providers.base import ChatMessage
from delivery_agent.providers.http_generic import HttpGenericProvider
from delivery_agent.providers.mock import MockProvider
from delivery_agent.providers.openai_compat import OpenAICompatProvider
from delivery_agent.providers.registry import create_provider


def _messages() -> list[ChatMessage]:
    context = (
        "context\n```json\n"
        '{"product_name":"box","repo":"demo/box","ref":"main",'
        '"environment":"prod","instruction":""}\n```'
    )
    return [
        ChatMessage(role="system", content="plan"),
        ChatMessage(role="user", content=context),
    ]


async def test_mock_provider_returns_chinese_plan() -> None:
    response = await MockProvider().complete(_messages(), json_mode=True)
    payload = json.loads(response.text)
    assert response.provider == "mock"
    assert "box" in payload["summary"]
    assert payload["trigger_gitlab_pipeline"] is True
    assert payload["risks"]
    assert payload["steps"][0]["key"] == "resolve_source"


async def test_mock_provider_can_skip_gitlab() -> None:
    messages = [
        ChatMessage(
            role="user",
            content=(
                "```json\n"
                '{"product_name":"box","repo":"demo/box","ref":"main",'
                '"environment":"dev","instruction":"请跳过 GitLab 流水线"}\n```'
            ),
        )
    ]
    payload = json.loads((await MockProvider().complete(messages)).text)
    assert payload["trigger_gitlab_pipeline"] is False


async def test_openai_compat_request_shape() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"content": '{"summary":"ok"}'}}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatProvider(
            base_url="https://llm.example/v1",
            api_key="sk-test",
            model="gpt-test",
            client=client,
        )
        response = await provider.complete(_messages(), json_mode=True)

    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-test"
    assert body["response_format"] == {"type": "json_object"}
    assert response.text == '{"summary":"ok"}'
    assert response.provider == "openai"


async def test_openai_compat_omits_auth_header_without_a_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"model": "local", "choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatProvider(
            base_url="http://127.0.0.1:8000/v1",
            api_key="",
            model="local",
            json_mode=False,
            client=client,
        )
        response = await provider.complete([ChatMessage(role="user", content="hi")])
    assert response.text == "ok"


async def test_anthropic_maps_system_and_messages() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"summary":"ok"}'}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            api_key="ant-test",
            model="claude-test",
            version="2023-06-01",
            max_tokens=256,
            client=client,
        )
        response = await provider.complete(_messages(), json_mode=True)

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "ant-test"
    assert seen["version"] == "2023-06-01"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["system"] == "plan"
    assert body["messages"] == [{"role": "user", "content": _messages()[1].content}]
    assert response.provider == "anthropic"


async def test_anthropic_requires_api_key() -> None:
    provider = AnthropicProvider(
        base_url="https://api.anthropic.com",
        api_key="",
        model="claude-test",
        version="2023-06-01",
        max_tokens=32,
        client=None,
    )
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        await provider.complete(_messages())


async def test_http_prompt_style_reads_text_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["model"] == "generic"
        assert "prompt" in body
        assert request.headers["authorization"] == "Bearer http-key"
        return httpx.Response(200, json={"text": "hello"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = HttpGenericProvider(
            url="https://models.example/generate",
            api_key="http-key",
            model="generic",
            style="prompt",
            client=client,
        )
        response = await provider.complete(_messages())
    assert response.text == "hello"
    assert response.provider == "http"


async def test_http_provider_rejects_empty_url() -> None:
    provider = HttpGenericProvider(url="", api_key="", model="generic", style="openai", client=None)
    with pytest.raises(ProviderError, match="HTTP_LLM_URL"):
        await provider.complete(_messages())


def test_registry_selects_configured_provider(tmp_path) -> None:
    assert create_provider(make_settings(tmp_path, llm_provider="none")) is None
    assert create_provider(make_settings(tmp_path, llm_provider="mock")).name == "mock"
    assert create_provider(make_settings(tmp_path, llm_provider="openai")).name == "openai"
    assert create_provider(make_settings(tmp_path, llm_provider="anthropic")).name == "anthropic"
    assert create_provider(make_settings(tmp_path, llm_provider="http")).name == "http"
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        create_provider(make_settings(tmp_path, llm_provider="mystery"))
