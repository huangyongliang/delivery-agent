"""HTTP API and the operator console."""

from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from delivery_agent import __version__
from delivery_agent.config import Settings
from delivery_agent.errors import DeliveryNotFound
from delivery_agent.integrations.gitlab import parse_gitlab_webhook
from delivery_agent.models import Delivery, DeliveryCreate, JenkinsCallback
from delivery_agent.orchestration.callbacks import apply_gitlab_callback, apply_jenkins_callback
from delivery_agent.runtime import Runtime

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"


def register_routes(app: FastAPI) -> None:
    @app.get("/")
    def index() -> FileResponse:
        page = WEB_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="console is not installed")
        return FileResponse(page)

    @app.get("/api/v1/health")
    def health(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        settings = runtime.settings
        return {
            "status": "ok",
            "version": __version__,
            "llm_provider": "none" if runtime.provider is None else runtime.provider.name,
            "llm_model": _model_name(settings),
            "gitlab_mode": settings.gitlab_mode,
            "jenkins_mode": settings.jenkins_mode,
            "docker_mode": settings.docker_mode,
            "ship_strategy": settings.ship_strategy,
            "jenkins_job": settings.jenkins_job,
            "registry": f"{settings.docker_registry}/{settings.docker_namespace}",
        }

    @app.post("/api/v1/deliveries", response_model=Delivery, status_code=201)
    async def create_delivery(
        body: DeliveryCreate,
        request: Request,
        background: BackgroundTasks,
        wait: bool = False,
    ) -> Delivery:
        runtime = _runtime(request)
        delivery = runtime.store.create(body)
        if wait:
            return await runtime.orchestrator.execute(delivery.id)
        background.add_task(runtime.orchestrator.execute, delivery.id)
        return delivery

    @app.get("/api/v1/deliveries", response_model=list[Delivery])
    def list_deliveries(request: Request, limit: int = 50) -> list[Delivery]:
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        return _runtime(request).store.list_deliveries(limit)

    @app.get("/api/v1/deliveries/{delivery_id}", response_model=Delivery)
    def get_delivery(delivery_id: str, request: Request) -> Delivery:
        try:
            return _runtime(request).store.get(delivery_id)
        except DeliveryNotFound as exc:
            raise HTTPException(status_code=404, detail="delivery not found") from exc

    @app.post("/api/v1/webhooks/gitlab")
    async def gitlab_webhook(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        token = request.headers.get("x-gitlab-token")
        if not _tokens_match(token, runtime.settings.gitlab_webhook_token):
            raise HTTPException(status_code=401, detail="invalid webhook token")
        payload = await _json_object(request)
        event = parse_gitlab_webhook(payload)
        logger.info("gitlab webhook %s", event.summary)
        return apply_gitlab_callback(runtime.store, event, payload)

    @app.post("/api/v1/webhooks/jenkins")
    async def jenkins_webhook(request: Request) -> dict[str, object]:
        runtime = _runtime(request)
        if not _tokens_match(
            request.headers.get("x-jenkins-token"),
            runtime.settings.jenkins_webhook_token,
        ):
            raise HTTPException(status_code=401, detail="invalid webhook token")
        payload = await _json_object(request)
        try:
            callback = JenkinsCallback.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid Jenkins callback") from exc
        logger.info("jenkins webhook delivery=%s result=%s", callback.delivery_id, callback.result)
        return apply_jenkins_callback(runtime.store, callback, payload)

    @app.get("/api/v1/webhooks")
    def list_webhooks(request: Request, limit: int = 50) -> list[dict[str, object]]:
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        return _runtime(request).store.list_webhooks(limit)


def _runtime(request: Request) -> Runtime:
    runtime = request.app.state.runtime
    if not isinstance(runtime, Runtime):
        raise HTTPException(status_code=500, detail="runtime is not configured")
    return runtime


async def _json_object(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="webhook body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="webhook body must be a JSON object")
    return payload


def _tokens_match(provided: str | None, expected: str) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _model_name(settings: Settings) -> str:
    provider = settings.llm_provider.strip().lower()
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    if provider == "http":
        return settings.http_llm_model
    if provider == "mock":
        return "mock-delivery-planner"
    return "deterministic"
