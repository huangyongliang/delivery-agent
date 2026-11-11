"""GitLab mock mode, live request shape, and webhook parsing."""

from __future__ import annotations

import json

import httpx
import pytest

from delivery_agent.errors import ProjectNotFound
from delivery_agent.integrations.gitlab import (
    LiveGitLabClient,
    MockGitLabClient,
    normalize_ref,
    parse_gitlab_webhook,
)
from delivery_agent.urls import build_url, gitlab_project_path


def test_build_url_keeps_prefix_and_encoding() -> None:
    url = build_url("https://gitlab.example.com/gitlab/", "/api/v4/projects/group%2Fapp")
    assert url.raw_path == b"/gitlab/api/v4/projects/group%2Fapp"
    assert gitlab_project_path("group/sample-product") == "/api/v4/projects/group%2Fsample-product"


def test_normalize_ref_strips_git_prefixes() -> None:
    assert normalize_ref("refs/heads/main") == "main"
    assert normalize_ref("refs/tags/v1.2.0") == "v1.2.0"
    assert normalize_ref("feature") == "feature"


async def test_mock_gitlab_resolves_project_commit_and_pipeline() -> None:
    client = MockGitLabClient("https://gitlab.example.com")
    project = await client.get_project("demo/sample-product")
    assert project.http_url_to_repo == "https://gitlab.example.com/demo/sample-product.git"
    commit = await client.get_commit(project.id, "main")
    again = await client.get_commit(project.id, "main")
    assert commit.sha == again.sha
    assert len(commit.sha) == 40
    pipeline = await client.trigger_pipeline(project.id, "main", {"DELIVERY_ID": "dlv_test"})
    assert pipeline.status == "created"
    assert (await client.get_pipeline(project.id, pipeline.id)).id == pipeline.id


async def test_mock_gitlab_missing_project() -> None:
    client = MockGitLabClient("https://gitlab.example.com")
    with pytest.raises(ProjectNotFound):
        await client.get_project("missing/sample-product")


def test_parse_pipeline_and_push_webhooks() -> None:
    pipeline = parse_gitlab_webhook(
        {
            "object_kind": "pipeline",
            "object_attributes": {"id": 9, "ref": "main", "status": "success", "sha": "abc"},
            "project": {"path_with_namespace": "demo/sample-product"},
        }
    )
    assert pipeline.project_path == "demo/sample-product"
    assert pipeline.status == "success"
    assert pipeline.pipeline_id == 9
    push = parse_gitlab_webhook(
        {
            "object_kind": "push",
            "ref": "refs/heads/release",
            "checkout_sha": "def",
            "project": {"path_with_namespace": "demo/sample-product"},
        }
    )
    assert push.ref == "release"
    assert push.sha == "def"


async def test_live_gitlab_client_encodes_path_and_sends_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["PRIVATE-TOKEN"] == "secret"
        assert request.url.raw_path == b"/api/v4/projects/group%2Fsample-product"
        return httpx.Response(
            200,
            json={
                "id": 7,
                "path_with_namespace": "group/sample-product",
                "http_url_to_repo": "https://gitlab.example.com/group/sample-product.git",
                "web_url": "https://gitlab.example.com/group/sample-product",
                "default_branch": "main",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveGitLabClient(
            base_url="https://gitlab.example.com",
            token="secret",
            client=http,
        )
        project = await client.get_project("group/sample-product")
    assert project.id == 7


async def test_live_gitlab_triggers_pipeline_with_variables() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.raw_path == b"/api/v4/projects/7/pipeline"
        body = json.loads(request.content.decode())
        assert body["ref"] == "main"
        assert {"key": "DELIVERY_ID", "value": "dlv_1"} in body["variables"]
        return httpx.Response(
            201,
            json={"id": 44, "status": "created", "ref": "main", "sha": "abc123456789", "web_url": "https://gitlab.example.com/p/44"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveGitLabClient(
            base_url="https://gitlab.example.com",
            token="secret",
            client=http,
        )
        pipeline = await client.trigger_pipeline(7, "main", {"DELIVERY_ID": "dlv_1"})
    assert pipeline.id == 44
    assert pipeline.status == "created"


async def test_live_gitlab_maps_404_to_project_not_found() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "404 Project Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveGitLabClient(
            base_url="https://gitlab.example.com",
            token="secret",
            client=http,
        )
        with pytest.raises(ProjectNotFound):
            await client.get_project("group/missing")
