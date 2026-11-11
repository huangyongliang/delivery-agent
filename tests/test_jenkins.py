"""Jenkins mock mode, live request shape, and build polling."""

from __future__ import annotations

import httpx
import pytest

from delivery_agent.errors import IntegrationError
from delivery_agent.integrations.jenkins import LiveJenkinsClient, MockJenkinsClient, wait_for_build
from delivery_agent.urls import jenkins_job_path


def test_jenkins_job_path_supports_folders() -> None:
    assert jenkins_job_path("team/delivery-pipeline") == "/job/team/job/delivery-pipeline"
    with pytest.raises(ValueError):
        jenkins_job_path("team/../escape")


async def test_mock_jenkins_success_includes_pushed_image() -> None:
    client = MockJenkinsClient("http://jenkins.example.com")
    queued = await client.trigger_job(
        "delivery-pipeline",
        {
            "PRODUCT_NAME": "sample-product",
            "IMAGE_REPO": "localhost:5000/delivery/sample-product",
            "IMAGE_TAG": "abc-staging",
        },
    )
    build, console = await wait_for_build(
        client,
        job="delivery-pipeline",
        queue_id=queued.id,
        timeout=2,
        interval=0.01,
    )
    assert build.result == "SUCCESS"
    assert "pushed image localhost:5000/delivery/sample-product:abc-staging" in console


async def test_mock_jenkins_failure_product() -> None:
    client = MockJenkinsClient("http://jenkins.example.com")
    queued = await client.trigger_job(
        "delivery-pipeline",
        {
            "PRODUCT_NAME": "broken-product",
            "IMAGE_REPO": "localhost:5000/delivery/broken-product",
            "IMAGE_TAG": "abc-dev",
        },
    )
    build, console = await wait_for_build(
        client,
        job="delivery-pipeline",
        queue_id=queued.id,
        timeout=2,
        interval=0.01,
    )
    assert build.result == "FAILURE"
    assert "tests failed" in console


async def test_live_jenkins_uses_crumb_auth_and_queue_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.raw_path.decode()
        if path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "crumb-1"},
            )
        if path == "/job/team/job/delivery-pipeline/buildWithParameters":
            assert request.headers["jenkins-crumb"] == "crumb-1"
            assert b"PRODUCT_NAME=sample-product" in request.content
            return httpx.Response(201, headers={"Location": "http://jenkins.example.com/queue/item/15/"})
        if path == "/queue/item/15/api/json":
            return httpx.Response(200, json={"executable": {"number": 9, "url": "http://jenkins.example.com/job/9/"}})
        if path == "/job/team/job/delivery-pipeline/9/api/json":
            return httpx.Response(
                200,
                json={
                    "number": 9,
                    "building": False,
                    "result": "SUCCESS",
                    "url": "http://jenkins.example.com/job/9/",
                    "duration": 5,
                },
            )
        if path == "/job/team/job/delivery-pipeline/9/consoleText":
            return httpx.Response(200, text="pushed image example\n")
        return httpx.Response(404, text=path)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveJenkinsClient(
            base_url="http://jenkins.example.com",
            user="alice",
            token="token-1",
            client=http,
        )
        queued = await client.trigger_job(
            "team/delivery-pipeline",
            {"PRODUCT_NAME": "sample-product"},
        )
        build, console = await wait_for_build(
            client,
            job="team/delivery-pipeline",
            queue_id=queued.id,
            timeout=2,
            interval=0.01,
        )
    assert queued.id == 15
    assert build.number == 9
    assert build.result == "SUCCESS"
    assert "pushed image" in console


async def test_live_jenkins_treats_missing_crumb_issuer_as_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.raw_path == b"/crumbIssuer/api/json":
            return httpx.Response(404, text="disabled")
        return httpx.Response(201, headers={"Location": "http://jenkins.example.com/queue/item/3/"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveJenkinsClient(
            base_url="http://jenkins.example.com",
            user="",
            token="",
            client=http,
        )
        queued = await client.trigger_job("delivery-pipeline", {"PRODUCT_NAME": "sample-product"})
    assert queued.id == 3


async def test_live_jenkins_auth_failure_is_explicit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="no")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LiveJenkinsClient(
            base_url="http://jenkins.example.com",
            user="alice",
            token="bad",
            client=http,
        )
        with pytest.raises(IntegrationError, match="JENKINS_USER"):
            await client.trigger_job("delivery-pipeline", {"PRODUCT_NAME": "sample-product"})
