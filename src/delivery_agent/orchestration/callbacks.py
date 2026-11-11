"""Apply GitLab and Jenkins callbacks onto stored deliveries."""

from __future__ import annotations

from delivery_agent.models import Delivery, DeliveryEvent, GitLabWebhook, JenkinsCallback
from delivery_agent.store import DeliveryStore
from delivery_agent.util import clip_text, utcnow


def apply_jenkins_callback(
    store: DeliveryStore,
    callback: JenkinsCallback,
    raw: dict[str, object],
) -> dict[str, object]:
    build = "-" if callback.build_number is None else str(callback.build_number)
    summary = f"Jenkins {callback.job or '-'} #{build} {callback.result or 'UNKNOWN'}"
    stored = store.add_webhook("jenkins", summary, raw)
    if not callback.delivery_id:
        return {"stored": True, "delivery_id": None, "summary": summary}
    if store.get_optional(callback.delivery_id) is None:
        return {
            "stored": True,
            "delivery_id": None,
            "summary": summary,
            "warning": "unknown delivery",
        }

    def mutate(delivery: Delivery) -> None:
        _append_event(delivery, "jenkins", summary, str(stored["received_at"]))
        if callback.image:
            delivery.artifact.image = callback.image
        if callback.build_number is not None:
            delivery.artifact.jenkins_build = callback.build_number
        if callback.job:
            delivery.artifact.jenkins_job = callback.job
        stage = next((item for item in delivery.stages if item.key == "jenkins_build"), None)
        result = callback.result or ""
        if stage is None or not result:
            return
        if delivery.status == "running" and result != "SUCCESS":
            stage.status = "failed"
            stage.message = f"回调报告构建失败：{result}"
            if callback.log_excerpt:
                stage.log = clip_text(callback.log_excerpt)
            stage.finished_at = utcnow()
            delivery.status = "failed"
            delivery.error = stage.message
            for later in delivery.stages:
                if later.status == "pending":
                    later.status = "skipped"
                    later.finished_at = utcnow()
            return
        stage.message = f"收到 Jenkins 回调 {result}"
        if callback.log_excerpt:
            stage.log = clip_text(callback.log_excerpt)

    store.update(callback.delivery_id, mutate)
    return {"stored": True, "delivery_id": callback.delivery_id, "summary": summary}


def apply_gitlab_callback(
    store: DeliveryStore,
    event: GitLabWebhook,
    raw: dict[str, object],
) -> dict[str, object]:
    stored = store.add_webhook("gitlab", event.summary, raw)
    targets = _matching_deliveries(store, event)
    for delivery_id in targets:
        def mutate(delivery: Delivery, *, current_id: str = delivery_id) -> None:
            if delivery.id != current_id:
                return
            _append_event(delivery, "gitlab", event.summary, str(stored["received_at"]))
            stage = next((item for item in delivery.stages if item.key == "gitlab_pipeline"), None)
            if stage is None:
                return
            detail = event.status or event.kind
            stage.message = f"收到 GitLab 回调：{detail}"
            if event.pipeline_id is not None:
                delivery.artifact.gitlab_pipeline_id = event.pipeline_id
            if event.sha and not delivery.artifact.git_sha:
                delivery.artifact.git_sha = event.sha

        store.update(delivery_id, mutate)
    return {"stored": True, "delivery_ids": targets, "summary": event.summary}


def _matching_deliveries(store: DeliveryStore, event: GitLabWebhook) -> list[str]:
    if not event.project_path:
        return []
    matches: list[str] = []
    for delivery in store.list_deliveries(limit=100):
        if delivery.repo != event.project_path:
            continue
        if event.ref and delivery.ref != event.ref:
            continue
        if delivery.status not in {"pending", "running"}:
            continue
        matches.append(delivery.id)
    return matches


def _append_event(delivery: Delivery, source: str, summary: str, received_at: str) -> None:
    delivery.events.append(DeliveryEvent(source=source, summary=summary, received_at=received_at))
    delivery.events = delivery.events[-20:]
