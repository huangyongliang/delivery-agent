"""Pipeline orchestration with and without a model."""

from __future__ import annotations

import re

from tests.conftest import make_settings, sample_request

from delivery_agent.errors import ProviderError
from delivery_agent.integrations.docker import MockDockerPublisher
from delivery_agent.integrations.gitlab import MockGitLabClient
from delivery_agent.integrations.jenkins import MockJenkinsClient
from delivery_agent.orchestration.naming import image_reference
from delivery_agent.orchestration.pipeline import Orchestrator
from delivery_agent.orchestration.planner import parse_plan
from delivery_agent.providers.base import ChatMessage, LLMProvider, LLMResponse
from delivery_agent.providers.mock import MockProvider
from delivery_agent.store import DeliveryStore

IMAGE_RE = re.compile(r"^localhost:5000/delivery/sample-product:[0-9a-f]{12}-staging$")


class FailingProvider(LLMProvider):
    name = "boom"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        raise ProviderError("model down")


def _orchestrator(tmp_path, provider, **overrides: object) -> tuple[Orchestrator, DeliveryStore]:
    settings = make_settings(tmp_path, **overrides)
    store = DeliveryStore(settings.database_path)
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        provider=provider,
        gitlab=MockGitLabClient(settings.gitlab_base_url),
        jenkins=MockJenkinsClient(settings.jenkins_base_url),
        docker=MockDockerPublisher(),
    )
    return orchestrator, store


def test_image_reference_normalizes_registry_and_product() -> None:
    repository, tag, reference = image_reference(
        "https://localhost:5000/",
        "Delivery",
        "Sample-Product",
        "abcdef1234567890",
        "staging",
    )
    assert repository == "localhost:5000/delivery/sample-product"
    assert tag == "abcdef123456-staging"
    assert reference == "localhost:5000/delivery/sample-product:abcdef123456-staging"


def test_parse_plan_accepts_fenced_json() -> None:
    plan = parse_plan(
        """```json
        {"summary":"发布","rationale":"因为","steps":[{"key":"report","title":"汇总","detail":"完成"}],"trigger_gitlab_pipeline":false,"risks":["注意"]}
        ```"""
    )
    assert plan.summary == "发布"
    assert plan.trigger_gitlab_pipeline is False
    assert plan.steps[0].key == "report"


async def test_mock_delivery_succeeds_end_to_end(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=MockProvider())
    created = store.create(sample_request())
    result = await orchestrator.execute(created.id)
    assert result.status == "succeeded"
    assert result.error is None
    assert result.plan is not None
    assert result.plan.source == "mock"
    assert "sample-product" in result.plan.summary
    assert result.artifact.image is not None
    assert IMAGE_RE.match(result.artifact.image)
    assert result.artifact.image_confirmed is True
    assert result.artifact.git_sha is not None
    assert len(result.artifact.git_sha) == 40
    assert result.artifact.jenkins_build is not None
    assert result.artifact.gitlab_pipeline_id is not None
    assert [stage.status for stage in result.stages] == ["succeeded"] * 6
    report = next(stage for stage in result.stages if stage.key == "report")
    assert "交付成功" in report.log
    assert result.artifact.image in report.log
    jenkins = next(stage for stage in result.stages if stage.key == "jenkins_build")
    assert "pushed image" in jenkins.log
    assert "https://gitlab.example.com/demo/sample-product.git" in jenkins.log


async def test_deterministic_pipeline_runs_without_a_model(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None)
    result = await orchestrator.execute(store.create(sample_request()).id)
    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.source == "deterministic"
    assert result.artifact.image_confirmed is True


async def test_failed_model_falls_back_to_deterministic_plan(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=FailingProvider())
    result = await orchestrator.execute(store.create(sample_request()).id)
    assert result.status == "succeeded"
    assert result.plan is not None
    assert result.plan.source == "deterministic"
    assert "模型规划不可用" in result.plan.rationale


async def test_operator_can_skip_gitlab_without_changing_the_other_stages(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None)
    created = store.create(sample_request(instruction="请跳过 GitLab，只走 Jenkins 和镜像发布"))
    result = await orchestrator.execute(created.id)
    assert result.status == "succeeded"
    gitlab = next(stage for stage in result.stages if stage.key == "gitlab_pipeline")
    assert gitlab.status == "skipped"
    assert result.artifact.gitlab_pipeline_id is None
    assert result.artifact.image_confirmed is True


async def test_config_can_disable_gitlab_trigger(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None, gitlab_trigger_pipeline=False)
    result = await orchestrator.execute(store.create(sample_request()).id)
    gitlab = next(stage for stage in result.stages if stage.key == "gitlab_pipeline")
    assert result.status == "succeeded"
    assert gitlab.status == "skipped"
    assert "配置关闭" in gitlab.message


async def test_missing_repository_fails_the_delivery(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None)
    created = store.create(sample_request(repo="missing/sample-product"))
    result = await orchestrator.execute(created.id)
    assert result.status == "failed"
    assert result.error is not None
    assert "找不到 GitLab 项目" in result.error
    resolve = next(stage for stage in result.stages if stage.key == "resolve_source")
    assert resolve.status == "failed"
    jenkins = next(stage for stage in result.stages if stage.key == "jenkins_build")
    assert jenkins.status == "skipped"


async def test_broken_product_fails_at_jenkins(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None)
    created = store.create(sample_request(product_name="broken-product"))
    result = await orchestrator.execute(created.id)
    assert result.status == "failed"
    jenkins = next(stage for stage in result.stages if stage.key == "jenkins_build")
    assert jenkins.status == "failed"
    assert "FAILURE" in jenkins.message
    docker = next(stage for stage in result.stages if stage.key == "docker_publish")
    assert docker.status == "skipped"


async def test_agent_ship_strategy_uses_docker_publisher(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None, ship_strategy="agent")
    result = await orchestrator.execute(store.create(sample_request()).id)
    assert result.status == "succeeded"
    assert result.artifact.image is not None
    docker = next(stage for stage in result.stages if stage.key == "docker_publish")
    assert "mock pushed image" in docker.log
    assert result.artifact.image_confirmed is True


async def test_second_execute_does_not_run_twice(tmp_path) -> None:
    orchestrator, store = _orchestrator(tmp_path, provider=None)
    created = store.create(sample_request())
    first = await orchestrator.execute(created.id)
    second = await orchestrator.execute(created.id)
    assert first.status == "succeeded"
    assert second.id == first.id
    assert second.artifact.jenkins_build == first.artifact.jenkins_build
