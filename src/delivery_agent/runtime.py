"""Wire settings, storage, and clients into one runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from delivery_agent.config import Settings, validate_settings
from delivery_agent.integrations.docker import DockerPublisher, create_docker
from delivery_agent.integrations.gitlab import GitLabClient, create_gitlab
from delivery_agent.integrations.jenkins import JenkinsClient, create_jenkins
from delivery_agent.orchestration.pipeline import Orchestrator
from delivery_agent.providers.base import LLMProvider
from delivery_agent.providers.registry import create_provider
from delivery_agent.store import DeliveryStore

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    store: DeliveryStore
    http: httpx.AsyncClient
    provider: LLMProvider | None
    gitlab: GitLabClient
    jenkins: JenkinsClient
    docker: DockerPublisher
    orchestrator: Orchestrator

    @classmethod
    def build(cls, settings: Settings) -> Runtime:
        validate_settings(settings)
        http = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        store = DeliveryStore(settings.database_path)
        provider = create_provider(settings, client=http)
        gitlab = create_gitlab(settings, client=http)
        jenkins = create_jenkins(settings, client=http)
        docker = create_docker(settings)
        orchestrator = Orchestrator(
            settings=settings,
            store=store,
            provider=provider,
            gitlab=gitlab,
            jenkins=jenkins,
            docker=docker,
        )
        logger.info(
            "delivery agent configured provider=%s gitlab=%s jenkins=%s docker=%s strategy=%s",
            "none" if provider is None else provider.name,
            settings.gitlab_mode,
            settings.jenkins_mode,
            settings.docker_mode,
            settings.ship_strategy,
        )
        return cls(
            settings=settings,
            store=store,
            http=http,
            provider=provider,
            gitlab=gitlab,
            jenkins=jenkins,
            docker=docker,
            orchestrator=orchestrator,
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        self.store.close()
