"""Offline provider used by the local demo and tests."""

from __future__ import annotations

import json
import re

from delivery_agent.providers.base import ChatMessage, LLMProvider, LLMResponse

_CONTEXT_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_SKIP_RE = re.compile(r"跳过\s*gitlab|skip\s+gitlab", re.IGNORECASE)


class MockProvider(LLMProvider):
    """Build a Chinese delivery plan from the context block embedded in the prompt."""

    name = "mock"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        context = _load_context(user)
        product = str(context.get("product_name") or "product")
        repo = str(context.get("repo") or "group/project")
        ref = str(context.get("ref") or "main")
        environment = str(context.get("environment") or "staging")
        instruction = str(context.get("instruction") or "").strip()
        trigger = _SKIP_RE.search(instruction) is None
        risks: list[str] = []
        if environment == "prod":
            risks.append("生产环境会覆盖同一提交短 SHA 对应的镜像标签。")
        rationale = (
            "这是本地模拟模型生成的计划。执行步骤仍由交付代理固定编排，"
            "模型只提供说明，并可以建议是否触发 GitLab 流水线。"
        )
        if instruction:
            rationale += f" 操作者说明：{instruction}"
        payload = {
            "summary": f"将 {product}（{repo}@{ref}）交付到 {environment}",
            "rationale": rationale,
            "steps": [
                {
                    "key": "resolve_source",
                    "title": "确认仓库与提交",
                    "detail": f"查询 {repo} 的 {ref}，记录提交 SHA。",
                },
                {
                    "key": "plan",
                    "title": "整理交付说明",
                    "detail": "用操作者说明补充本次发布的目的与风险。",
                },
                {
                    "key": "gitlab_pipeline",
                    "title": "触发 GitLab 流水线",
                    "detail": "通过 API 创建流水线，把交付编号写进 CI 变量。",
                },
                {
                    "key": "jenkins_build",
                    "title": "运行 Jenkins 构建",
                    "detail": "传入仓库、版本和镜像坐标，等待构建结束并读取控制台。",
                },
                {
                    "key": "docker_publish",
                    "title": "发布 Docker 镜像",
                    "detail": "确认 Jenkins 已推送约定标签，或由代理在本地构建并推送。",
                },
                {
                    "key": "report",
                    "title": "汇总交付结果",
                    "detail": "返回镜像坐标、提交和构建编号。",
                },
            ],
            "trigger_gitlab_pipeline": trigger,
            "risks": risks,
        }
        return LLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            model="mock-delivery-planner",
            provider=self.name,
        )


def _load_context(user: str) -> dict[str, object]:
    match = _CONTEXT_RE.search(user)
    if match is None:
        return {}
    try:
        loaded = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded
