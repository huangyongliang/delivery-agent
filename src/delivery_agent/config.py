"""Runtime configuration loaded from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All deployment-specific values. Secrets stay in the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_host: str = "0.0.0.0"
    agent_port: int = Field(default=8080, validation_alias=AliasChoices("AGENT_PORT", "PORT"))
    public_base_url: str = "http://localhost:8080"
    database_path: str = "data/delivery-agent.db"
    log_level: str = "INFO"
    pipeline_timeout_seconds: float = 600.0
    http_timeout_seconds: float = 30.0

    llm_provider: str = "mock"
    llm_temperature: float = 0.2

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_json_mode: bool = True

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_version: str = "2023-06-01"
    anthropic_max_tokens: int = 1024

    http_llm_url: str = ""
    http_llm_api_key: str = ""
    http_llm_model: str = "generic"
    http_llm_style: str = "openai"

    gitlab_mode: str = "mock"
    gitlab_base_url: str = "https://gitlab.example.com"
    gitlab_token: str = ""
    gitlab_webhook_token: str = ""
    gitlab_trigger_pipeline: bool = True

    jenkins_mode: str = "mock"
    jenkins_base_url: str = "http://localhost:8081"
    jenkins_user: str = ""
    jenkins_token: str = ""
    jenkins_job: str = "delivery-pipeline"
    jenkins_webhook_token: str = ""
    jenkins_poll_interval_seconds: float = 2.0

    docker_mode: str = "mock"
    docker_registry: str = "localhost:5000"
    docker_namespace: str = "delivery"
    docker_context: str = "examples/sample-product"
    docker_dockerfile: str = "Dockerfile"
    ship_strategy: str = "jenkins"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_settings(settings: Settings) -> None:
    provider = settings.llm_provider.strip().lower()
    if provider not in {"mock", "openai", "anthropic", "http", "none", "off", "deterministic"}:
        raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    if settings.gitlab_mode not in {"mock", "live"}:
        raise ValueError(f"unsupported GITLAB_MODE: {settings.gitlab_mode}")
    if settings.jenkins_mode not in {"mock", "live"}:
        raise ValueError(f"unsupported JENKINS_MODE: {settings.jenkins_mode}")
    if settings.docker_mode not in {"mock", "local"}:
        raise ValueError(f"unsupported DOCKER_MODE: {settings.docker_mode}")
    if settings.ship_strategy not in {"jenkins", "agent"}:
        raise ValueError(f"unsupported SHIP_STRATEGY: {settings.ship_strategy}")
    if settings.http_llm_style not in {"openai", "prompt"}:
        raise ValueError(f"unsupported HTTP_LLM_STYLE: {settings.http_llm_style}")
    if settings.pipeline_timeout_seconds <= 0:
        raise ValueError("PIPELINE_TIMEOUT_SECONDS must be positive")
