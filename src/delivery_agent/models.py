"""API and persistence models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from delivery_agent.contracts import STAGE_KEYS, STAGE_TITLES

DeliveryStatus = Literal["pending", "running", "succeeded", "failed"]
StageStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]


class DeliveryCreate(BaseModel):
    repo: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$",
        description="GitLab 项目路径，例如 demo/sample-product",
        examples=["demo/sample-product"],
    )
    ref: str = Field(
        default="main",
        min_length=1,
        max_length=200,
        description="分支或标签",
        examples=["main"],
    )
    product_name: str = Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$",
        description="产品名称，用于镜像名",
        examples=["sample-product"],
    )
    target_environment: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description="目标环境，例如 dev、staging、prod",
        examples=["staging"],
    )
    instruction: str | None = Field(
        default=None,
        max_length=2000,
        description="可选的自然语言说明，供模型规划时参考",
    )

    @field_validator("repo", "ref", "product_name", "target_environment")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("instruction")
    @classmethod
    def blank_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PlanStep(BaseModel):
    key: str
    title: str
    detail: str


class DeliveryPlan(BaseModel):
    summary: str
    rationale: str
    steps: list[PlanStep]
    trigger_gitlab_pipeline: bool = True
    risks: list[str] = Field(default_factory=list)
    source: str = "deterministic"


class StageRecord(BaseModel):
    key: str
    title: str
    status: StageStatus = "pending"
    message: str = ""
    log: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class Artifact(BaseModel):
    image: str | None = None
    git_sha: str | None = None
    git_url: str | None = None
    jenkins_job: str | None = None
    jenkins_build: int | None = None
    jenkins_url: str | None = None
    gitlab_pipeline_id: int | None = None
    gitlab_pipeline_url: str | None = None
    image_confirmed: bool = False


class DeliveryEvent(BaseModel):
    source: str
    summary: str
    received_at: str


class Delivery(BaseModel):
    id: str
    repo: str
    ref: str
    product_name: str
    target_environment: str
    instruction: str | None = None
    status: DeliveryStatus = "pending"
    plan: DeliveryPlan | None = None
    stages: list[StageRecord]
    artifact: Artifact = Field(default_factory=Artifact)
    events: list[DeliveryEvent] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


class JenkinsCallback(BaseModel):
    delivery_id: str | None = None
    job: str | None = None
    build_number: int | None = None
    result: str | None = None
    image: str | None = None
    log_excerpt: str | None = None

    @field_validator("build_number", mode="before")
    @classmethod
    def coerce_build_number(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @field_validator("result")
    @classmethod
    def upper_result(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped.upper() if stripped else None


class GitLabWebhook(BaseModel):
    kind: str
    project_path: str | None = None
    ref: str | None = None
    sha: str | None = None
    status: str | None = None
    pipeline_id: int | None = None
    summary: str


def initial_stages() -> list[StageRecord]:
    return [StageRecord(key=key, title=STAGE_TITLES[key]) for key in STAGE_KEYS]
