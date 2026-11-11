"""Fixed delivery pipeline.

Stages always run in the same order. The plan supplies the operator-facing
explanation and may suppress the GitLab trigger. Jenkins and Docker publishing
are not chosen by the model.
"""

from __future__ import annotations

import logging
from typing import cast

from delivery_agent.config import Settings
from delivery_agent.contracts import (
    PARAM_AGENT_CALLBACK_URL,
    PARAM_DELIVERY_ID,
    PARAM_GIT_REF,
    PARAM_GIT_SHA,
    PARAM_GIT_URL,
    PARAM_IMAGE_REPO,
    PARAM_IMAGE_TAG,
    PARAM_PRODUCT_NAME,
    PARAM_TARGET_ENV,
)
from delivery_agent.errors import DeliveryAborted, IntegrationError
from delivery_agent.integrations.docker import DockerPublisher, ImageSpec
from delivery_agent.integrations.gitlab import GitLabClient
from delivery_agent.integrations.jenkins import JenkinsClient, wait_for_build
from delivery_agent.models import Artifact, Delivery, DeliveryPlan, StageRecord, StageStatus
from delivery_agent.orchestration.naming import image_reference
from delivery_agent.orchestration.planner import Planner
from delivery_agent.providers.base import LLMProvider
from delivery_agent.store import DeliveryStore
from delivery_agent.util import clip_text, utcnow

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        store: DeliveryStore,
        provider: LLMProvider | None,
        gitlab: GitLabClient,
        jenkins: JenkinsClient,
        docker: DockerPublisher,
    ) -> None:
        self.settings = settings
        self.store = store
        self.gitlab = gitlab
        self.jenkins = jenkins
        self.docker = docker
        self.planner = Planner(provider)

    async def execute(self, delivery_id: str) -> Delivery:
        if not self.store.mark_running(delivery_id):
            return self.store.get(delivery_id)
        try:
            await self._run(delivery_id)
        except DeliveryAborted:
            logger.info("delivery %s aborted by callback", delivery_id)
        except IntegrationError as exc:
            logger.warning("delivery %s failed: %s", delivery_id, exc)
            self._fail(delivery_id, exc)
        except Exception as exc:
            logger.exception("delivery %s failed", delivery_id)
            self._fail(delivery_id, exc)
        return self.store.get(delivery_id)

    async def _run(self, delivery_id: str) -> None:
        delivery = self._active(delivery_id)
        self._stage(delivery_id, "resolve_source", status="running", message="正在查询 GitLab 项目")
        project = await self.gitlab.get_project(delivery.repo)
        commit = await self.gitlab.get_commit(project.id, delivery.ref)
        if len(commit.sha) < 12:
            raise IntegrationError("Git commit SHA is too short")
        self.store.update(
            delivery_id,
            lambda current: _artifact(
                current,
                git_sha=commit.sha,
                git_url=project.http_url_to_repo,
            ),
        )
        self._stage(
            delivery_id,
            "resolve_source",
            status="succeeded",
            message=f"{project.path_with_namespace} @ {commit.sha[:12]}",
            log=f"{project.web_url}\n{commit.title}\n{commit.sha}",
        )

        self._check_active(delivery_id)
        self._stage(delivery_id, "plan", status="running", message="正在生成交付计划")
        delivery = self.store.get(delivery_id)
        plan = await self.planner.plan(delivery, project, commit)
        self.store.update(delivery_id, lambda current: _set_plan(current, plan))
        self._stage(
            delivery_id,
            "plan",
            status="succeeded",
            message=plan.summary,
            log=f"source={plan.source}\n{plan.rationale}",
        )

        repository, tag, reference = image_reference(
            self.settings.docker_registry,
            self.settings.docker_namespace,
            delivery.product_name,
            commit.sha,
            delivery.target_environment,
        )

        self._check_active(delivery_id)
        should_trigger = self.settings.gitlab_trigger_pipeline and plan.trigger_gitlab_pipeline
        if should_trigger:
            self._stage(
                delivery_id,
                "gitlab_pipeline",
                status="running",
                message="正在创建 GitLab 流水线",
            )
            pipeline = await self.gitlab.trigger_pipeline(
                project.id,
                delivery.ref,
                {
                    "DELIVERY_ID": delivery_id,
                    "PRODUCT_NAME": delivery.product_name,
                    "TARGET_ENV": delivery.target_environment,
                    "IMAGE": reference,
                },
            )
            self.store.update(
                delivery_id,
                lambda current: _artifact(
                    current,
                    gitlab_pipeline_id=pipeline.id,
                    gitlab_pipeline_url=pipeline.web_url,
                ),
            )
            self._stage(
                delivery_id,
                "gitlab_pipeline",
                status="succeeded",
                message=f"已触发流水线 #{pipeline.id}，当前状态 {pipeline.status}",
                log=pipeline.web_url,
            )
        else:
            reason = (
                "配置关闭了 GitLab 流水线触发"
                if not self.settings.gitlab_trigger_pipeline
                else "计划建议跳过 GitLab 流水线"
            )
            self._stage(delivery_id, "gitlab_pipeline", status="skipped", message=reason)

        self._check_active(delivery_id)
        self._stage(
            delivery_id,
            "jenkins_build",
            status="running",
            message=f"正在触发 {self.settings.jenkins_job}",
        )
        callback = self.settings.public_base_url.rstrip("/") + "/api/v1/webhooks/jenkins"
        parameters = {
            PARAM_PRODUCT_NAME: delivery.product_name,
            PARAM_GIT_URL: project.http_url_to_repo,
            PARAM_GIT_REF: delivery.ref,
            PARAM_GIT_SHA: commit.sha,
            PARAM_IMAGE_REPO: repository,
            PARAM_IMAGE_TAG: tag,
            PARAM_TARGET_ENV: delivery.target_environment,
            PARAM_DELIVERY_ID: delivery_id,
            PARAM_AGENT_CALLBACK_URL: callback,
        }
        queue = await self.jenkins.trigger_job(self.settings.jenkins_job, parameters)
        build, console = await wait_for_build(
            self.jenkins,
            job=self.settings.jenkins_job,
            queue_id=queue.id,
            timeout=self.settings.pipeline_timeout_seconds,
            interval=self.settings.jenkins_poll_interval_seconds,
        )
        self._check_active(delivery_id)
        self.store.update(
            delivery_id,
            lambda current: _artifact(
                current,
                jenkins_job=self.settings.jenkins_job,
                jenkins_build=build.number,
                jenkins_url=build.url,
            ),
        )
        console = clip_text(console)
        if build.result != "SUCCESS":
            self._stage(
                delivery_id,
                "jenkins_build",
                status="failed",
                message=f"构建 #{build.number} 结果 {build.result}",
                log=console,
            )
            raise IntegrationError(f"Jenkins 构建 #{build.number} 结果为 {build.result}")
        self._stage(
            delivery_id,
            "jenkins_build",
            status="succeeded",
            message=f"构建 #{build.number} 成功",
            log=console,
        )

        self._check_active(delivery_id)
        self._stage(delivery_id, "docker_publish", status="running", message="正在确认镜像发布")
        if self.settings.ship_strategy == "agent":
            published = await self.docker.publish(
                ImageSpec(
                    context_path=self.settings.docker_context,
                    dockerfile=self.settings.docker_dockerfile,
                    repository=repository,
                    tag=tag,
                    build_args={
                        "PRODUCT_NAME": delivery.product_name,
                        "TARGET_ENV": delivery.target_environment,
                    },
                )
            )
            reference = published.reference
            publish_log = clip_text(published.log)
            confirmed = published.pushed
        else:
            confirmed = reference in console
            publish_log = f"expected={reference}\nconfirmed={confirmed}"
            if not confirmed:
                self._stage(
                    delivery_id,
                    "docker_publish",
                    status="failed",
                    message="构建成功，但控制台没有确认镜像已推送",
                    log=publish_log + "\n" + console,
                )
                raise IntegrationError(f"未确认镜像已推送：{reference}")
        self.store.update(
            delivery_id,
            lambda current: _artifact(current, image=reference, image_confirmed=confirmed),
        )
        self._stage(
            delivery_id,
            "docker_publish",
            status="succeeded",
            message=reference,
            log=publish_log,
        )

        self._check_active(delivery_id)
        fresh = self.store.get(delivery_id)
        report = _report(fresh, plan, reference)

        def finish(current: Delivery) -> None:
            if current.status == "failed":
                return
            stage = _find_stage(current, "report")
            stage.status = "succeeded"
            stage.message = reference
            stage.log = clip_text(report)
            now = utcnow()
            stage.started_at = stage.started_at or now
            stage.finished_at = now
            current.status = "succeeded"
            current.error = None

        self.store.update(delivery_id, finish)

    def _active(self, delivery_id: str) -> Delivery:
        delivery = self.store.get(delivery_id)
        if delivery.status == "failed":
            raise DeliveryAborted(delivery_id)
        return delivery

    def _check_active(self, delivery_id: str) -> None:
        self._active(delivery_id)

    def _stage(
        self,
        delivery_id: str,
        key: str,
        *,
        status: str,
        message: str,
        log: str | None = None,
    ) -> None:
        def mutate(delivery: Delivery) -> None:
            stage = _find_stage(delivery, key)
            stage.status = cast(StageStatus, status)
            stage.message = message
            if log is not None:
                stage.log = clip_text(log)
            now = utcnow()
            if status == "running":
                stage.started_at = stage.started_at or now
            if status in {"succeeded", "failed", "skipped"}:
                stage.started_at = stage.started_at or now
                stage.finished_at = now

        self.store.update(delivery_id, mutate)

    def _fail(self, delivery_id: str, exc: Exception) -> None:
        message = str(exc).strip() or exc.__class__.__name__

        def mutate(delivery: Delivery) -> None:
            if delivery.status == "failed":
                delivery.error = delivery.error or message
                return
            for stage in delivery.stages:
                if stage.status == "running":
                    stage.status = "failed"
                    stage.message = message
                    stage.finished_at = _now()
            for stage in delivery.stages:
                if stage.status == "pending":
                    stage.status = "skipped"
                    stage.finished_at = _now()
            delivery.status = "failed"
            delivery.error = message

        self.store.update(delivery_id, mutate)


def _find_stage(delivery: Delivery, key: str) -> StageRecord:
    for stage in delivery.stages:
        if stage.key == key:
            return stage
    raise KeyError(key)


def _set_plan(delivery: Delivery, plan: DeliveryPlan) -> None:
    delivery.plan = plan


def _artifact(delivery: Delivery, **fields: object) -> None:
    data = delivery.artifact.model_dump()
    data.update(fields)
    delivery.artifact = Artifact.model_validate(data)


def _report(delivery: Delivery, plan: DeliveryPlan, reference: str) -> str:
    artifact = delivery.artifact
    lines = [
        f"交付成功。产品 {delivery.product_name} 已发布到 {delivery.target_environment}。",
        f"镜像：{reference}",
        f"提交：{artifact.git_sha or '-'}",
        f"Jenkins：{artifact.jenkins_job or '-'} #{artifact.jenkins_build or '-'}",
        f"GitLab 流水线：{artifact.gitlab_pipeline_id or '未触发'}",
        f"计划来源：{plan.source}",
        f"说明：{plan.summary}",
    ]
    if plan.risks:
        lines.append("风险：" + "；".join(plan.risks))
    return "\n".join(lines)


def _now() -> str:
    return utcnow()
