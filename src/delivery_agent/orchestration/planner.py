"""Turn a delivery request into a plan.

The model may explain the shipment and set ``trigger_gitlab_pipeline``.
It cannot add executable steps. If the model is disabled or fails, the
deterministic plan is used and the pipeline still runs.
"""

from __future__ import annotations

import json
import logging
import re

from delivery_agent.contracts import STAGE_KEYS, STAGE_TITLES
from delivery_agent.errors import ProviderError
from delivery_agent.integrations.gitlab import GitCommit, GitProject
from delivery_agent.models import Delivery, DeliveryPlan, PlanStep
from delivery_agent.providers.base import ChatMessage, LLMProvider

logger = logging.getLogger(__name__)

_SKIP_GITLAB_RE = re.compile(r"跳过\s*gitlab|skip\s+gitlab", re.IGNORECASE)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_SYSTEM_PROMPT = """你是交付代理里的规划器。根据给定上下文，输出一份 JSON 对象，不要输出其他文字。
JSON 字段：
- summary: 一句中文摘要
- rationale: 中文说明，解释为什么这样交付
- steps: 数组。每一项包含 key、title、detail。
  key 只能是 resolve_source、plan、gitlab_pipeline、jenkins_build、docker_publish、report
- trigger_gitlab_pipeline: 布尔值。只有操作者明确要求跳过 GitLab 时才设为 false
- risks: 字符串数组，没有风险时用空数组
不要发明新的执行步骤，不要输出 shell 命令。实际构建由平台固定流程完成。"""


class Planner:
    def __init__(self, provider: LLMProvider | None) -> None:
        self.provider = provider

    async def plan(
        self,
        delivery: Delivery,
        project: GitProject,
        commit: GitCommit,
    ) -> DeliveryPlan:
        fallback = deterministic_plan(delivery, project, commit)
        if self.provider is None:
            return fallback
        messages = [
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=_user_prompt(delivery, project, commit)),
        ]
        try:
            response = await self.provider.complete(messages, json_mode=True)
            plan = parse_plan(response.text)
        except (ProviderError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("model plan unavailable, using deterministic plan: %s", exc)
            fallback.rationale += f" 模型规划不可用，已改用确定性计划：{exc}"
            return fallback
        plan.source = self.provider.name
        return apply_operator_overrides(delivery, plan)


def deterministic_plan(delivery: Delivery, project: GitProject, commit: GitCommit) -> DeliveryPlan:
    short = commit.sha[:12]
    rationale = (
        "未使用外部模型。平台按固定阶段执行：解析仓库、按配置触发 GitLab 流水线、"
        "运行 Jenkins、确认 Docker 镜像并汇总结果。"
    )
    if delivery.instruction:
        rationale += f" 操作者说明：{delivery.instruction}"
    risks: list[str] = []
    if delivery.target_environment == "prod":
        risks.append("生产环境会覆盖同一提交短 SHA 对应的镜像标签。")
    plan = DeliveryPlan(
        summary=(
            f"将 {delivery.product_name}（{project.path_with_namespace}@{delivery.ref}，{short}）"
            f"交付到 {delivery.target_environment}"
        ),
        rationale=rationale,
        steps=[
            PlanStep(key=key, title=STAGE_TITLES[key], detail=_deterministic_detail(key, delivery))
            for key in STAGE_KEYS
        ],
        trigger_gitlab_pipeline=True,
        risks=risks,
        source="deterministic",
    )
    return apply_operator_overrides(delivery, plan)


def apply_operator_overrides(delivery: Delivery, plan: DeliveryPlan) -> DeliveryPlan:
    """Honor an explicit skip request even when the model forgets the flag."""
    if delivery.instruction and _SKIP_GITLAB_RE.search(delivery.instruction):
        plan.trigger_gitlab_pipeline = False
        note = "操作者要求跳过 GitLab 流水线。"
        if note not in plan.rationale:
            plan.rationale = f"{plan.rationale} {note}".strip()
    return plan


def parse_plan(text: str) -> DeliveryPlan:
    raw = text.strip()
    if raw.startswith("```"):
        raw = _FENCE_RE.sub("", raw).strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("plan JSON must be an object")
    steps: list[PlanStep] = []
    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ValueError("plan steps must be a list")
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in STAGE_KEYS:
            continue
        steps.append(
            PlanStep(
                key=key,
                title=_clip(str(item.get("title") or STAGE_TITLES[key]), 80),
                detail=_clip(str(item.get("detail") or ""), 500),
            )
        )
    if not steps:
        raise ValueError("plan did not include any known steps")
    flag = data.get("trigger_gitlab_pipeline", True)
    if not isinstance(flag, bool):
        raise ValueError("trigger_gitlab_pipeline must be a boolean")
    risks_raw = data.get("risks") or []
    if not isinstance(risks_raw, list):
        raise ValueError("risks must be a list")
    risks = [_clip(str(item), 200) for item in risks_raw[:5] if str(item).strip()]
    summary = _clip(str(data.get("summary") or ""), 300)
    rationale = _clip(str(data.get("rationale") or ""), 2000)
    if not summary:
        raise ValueError("plan summary is empty")
    return DeliveryPlan(
        summary=summary,
        rationale=rationale,
        steps=steps,
        trigger_gitlab_pipeline=flag,
        risks=risks,
        source="model",
    )


def _user_prompt(delivery: Delivery, project: GitProject, commit: GitCommit) -> str:
    context = {
        "repo": delivery.repo,
        "ref": delivery.ref,
        "product_name": delivery.product_name,
        "environment": delivery.target_environment,
        "instruction": delivery.instruction or "",
        "sha": commit.sha,
        "project_url": project.web_url,
        "default_branch": project.default_branch,
    }
    return (
        "请为下面的交付请求生成计划。\n"
        f"```json\n{json.dumps(context, ensure_ascii=False)}\n```"
    )


def _deterministic_detail(key: str, delivery: Delivery) -> str:
    details = {
        "resolve_source": f"读取 {delivery.repo} 的 {delivery.ref}。",
        "plan": "使用平台内置规则生成说明。",
        "gitlab_pipeline": "创建 GitLab 流水线，并写入交付编号。",
        "jenkins_build": "用仓库地址、版本和镜像坐标触发 Jenkins。",
        "docker_publish": "按配置从 Jenkins 日志确认镜像，或由代理推送。",
        "report": f"汇总 {delivery.product_name} 到 {delivery.target_environment} 的结果。",
    }
    return details[key]


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit]
