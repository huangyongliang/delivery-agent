"""HTTP API, console, and webhook authentication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_settings, sample_request

from delivery_agent.main import create_app
from delivery_agent.models import DeliveryCreate

ROOT = Path(__file__).resolve().parents[1]


def test_demo_request_completes(tmp_path) -> None:
    payload = json.loads((ROOT / "examples" / "demo-request.json").read_text())
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/v1/deliveries?wait=true", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["artifact"]["image_confirmed"] is True
        assert body["artifact"]["image"].startswith("localhost:5000/delivery/sample-product:")
        assert body["plan"]["source"] == "mock"
        page = client.get("/")
        assert page.status_code == 200
        assert "交付台" in page.text
        health = client.get("/api/v1/health").json()
        assert health["status"] == "ok"
        assert health["llm_provider"] == "mock"
        assert health["gitlab_mode"] == "mock"
        assert "test-gitlab" not in json.dumps(health)


def test_background_delivery_finishes_before_the_client_returns(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/v1/deliveries", json=sample_request().model_dump())
        assert created.status_code == 201
        delivery_id = created.json()["id"]
        follow = client.get(f"/api/v1/deliveries/{delivery_id}")
        assert follow.status_code == 200
        assert follow.json()["status"] == "succeeded"
        listed = client.get("/api/v1/deliveries").json()
        assert listed[0]["id"] == delivery_id


def test_unknown_delivery_is_404(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/v1/deliveries/dlv_missing")
        assert response.status_code == 404


def test_invalid_request_is_rejected(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/deliveries?wait=true",
            json={
                "repo": "not a repo",
                "product_name": "sample-product",
                "target_environment": "staging",
            },
        )
        assert response.status_code == 422


def test_webhook_tokens_and_callbacks(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        store = app.state.runtime.store
        delivery = store.create(
            DeliveryCreate.model_validate(
                {
                    "repo": "demo/sample-product",
                    "ref": "main",
                    "product_name": "sample-product",
                    "target_environment": "staging",
                }
            )
        )
        assert store.mark_running(delivery.id)

        rejected = client.post(
            "/api/v1/webhooks/jenkins",
            headers={"X-Jenkins-Token": "nope"},
            json={"delivery_id": delivery.id, "result": "FAILURE"},
        )
        assert rejected.status_code == 401

        gitlab = client.post(
            "/api/v1/webhooks/gitlab",
            headers={"X-Gitlab-Token": "test-gitlab"},
            json={
                "object_kind": "pipeline",
                "object_attributes": {"id": 9, "ref": "main", "status": "failed"},
                "project": {"path_with_namespace": "demo/sample-product"},
            },
        )
        assert gitlab.status_code == 200
        assert delivery.id in gitlab.json()["delivery_ids"]
        assert "GitLab" in store.get(delivery.id).stages[2].message

        failed = client.post(
            "/api/v1/webhooks/jenkins",
            headers={"X-Jenkins-Token": "test-jenkins"},
            json={
                "delivery_id": delivery.id,
                "job": "delivery-pipeline",
                "build_number": "4",
                "result": "failure",
                "image": "localhost:5000/delivery/sample-product:deadbeef-staging",
                "log_excerpt": "tests failed",
            },
        )
        assert failed.status_code == 200
        stored = store.get(delivery.id)
        assert stored.status == "failed"
        assert stored.artifact.jenkins_build == 4
        assert stored.artifact.image.endswith(":deadbeef-staging")

        invalid = client.post(
            "/api/v1/webhooks/jenkins",
            headers={"X-Jenkins-Token": "test-jenkins"},
            json={"build_number": "nope"},
        )
        assert invalid.status_code == 422
        events = client.get("/api/v1/webhooks").json()
        assert len(events) >= 2


def test_webhook_rejected_when_token_is_not_configured(tmp_path) -> None:
    app = create_app(make_settings(tmp_path, gitlab_webhook_token="", jenkins_webhook_token=""))
    with TestClient(app) as client:
        gitlab = client.post("/api/v1/webhooks/gitlab", json={"object_kind": "push"})
        jenkins = client.post("/api/v1/webhooks/jenkins", json={"result": "SUCCESS"})
        assert gitlab.status_code == 401
        assert jenkins.status_code == 401


def test_invalid_mode_fails_fast(tmp_path) -> None:
    with pytest.raises(ValueError, match="GITLAB_MODE"):
        create_app(make_settings(tmp_path, gitlab_mode="sometimes"))
