"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

from delivery_agent.config import Settings
from delivery_agent.models import DeliveryCreate


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": str(tmp_path / "delivery.db"),
        "llm_provider": "mock",
        "gitlab_mode": "mock",
        "jenkins_mode": "mock",
        "docker_mode": "mock",
        "ship_strategy": "jenkins",
        "gitlab_webhook_token": "test-gitlab",
        "jenkins_webhook_token": "test-jenkins",
        "gitlab_base_url": "https://gitlab.example.com",
        "jenkins_base_url": "http://jenkins.example.com",
        "jenkins_job": "delivery-pipeline",
        "docker_registry": "localhost:5000",
        "docker_namespace": "delivery",
        "public_base_url": "http://localhost:8080",
        "pipeline_timeout_seconds": 5,
        "jenkins_poll_interval_seconds": 0.01,
        "gitlab_trigger_pipeline": True,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def sample_request(**overrides: object) -> DeliveryCreate:
    values: dict[str, object] = {
        "repo": "demo/sample-product",
        "ref": "main",
        "product_name": "sample-product",
        "target_environment": "staging",
        "instruction": "把示例产品发布到预发环境，确认镜像坐标并给出结果说明。",
    }
    values.update(overrides)
    return DeliveryCreate.model_validate(values)
