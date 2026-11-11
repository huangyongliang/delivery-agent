"""Jenkins client: trigger a parameterized job, poll it, and read the console log."""

from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from delivery_agent.config import Settings
from delivery_agent.errors import IntegrationError
from delivery_agent.urls import build_url, jenkins_job_path

_QUEUE_RE = re.compile(r"/queue/item/(\d+)")


@dataclass(frozen=True)
class JenkinsQueueItem:
    id: int
    build_number: int | None
    cancelled: bool
    url: str | None


@dataclass(frozen=True)
class JenkinsBuild:
    number: int
    result: str | None
    building: bool
    url: str
    duration_ms: int


class JenkinsClient(ABC):
    @abstractmethod
    async def trigger_job(self, job: str, parameters: dict[str, str]) -> JenkinsQueueItem:
        """Enqueue a parameterized build and return the queue item."""

    @abstractmethod
    async def get_queue_item(self, queue_id: int) -> JenkinsQueueItem:
        """Read a queue item, including the build number once Jenkins assigns one."""

    @abstractmethod
    async def get_build(self, job: str, number: int) -> JenkinsBuild:
        """Read build status."""

    @abstractmethod
    async def get_console(self, job: str, number: int) -> str:
        """Return the plain-text console log."""


class MockJenkinsClient(JenkinsClient):
    """In-memory Jenkins.

    ``PRODUCT_NAME=broken-product`` finishes as FAILURE. Every other build
    succeeds and prints the ``pushed image`` line the agent looks for.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._sequence = 100
        self._builds: dict[tuple[str, int], tuple[JenkinsBuild, str]] = {}
        self._queue: dict[int, tuple[str, int]] = {}

    async def trigger_job(self, job: str, parameters: dict[str, str]) -> JenkinsQueueItem:
        self._sequence += 1
        number = self._sequence
        failed = parameters.get("PRODUCT_NAME") == "broken-product"
        result = "FAILURE" if failed else "SUCCESS"
        image_repo = parameters.get("IMAGE_REPO", "localhost:5000/delivery/unknown")
        image_tag = parameters.get("IMAGE_TAG", "dev")
        image = f"{image_repo}:{image_tag}"
        product = parameters.get("PRODUCT_NAME", "unknown")
        lines = [
            "Started by delivery agent",
            f"Checking out {parameters.get('GIT_URL', '')} ref {parameters.get('GIT_REF', '')}",
            f"commit {parameters.get('GIT_SHA', '')}",
            f"tests passed for {product}" if not failed else f"tests failed for {product}",
            f"docker build -t {image} .",
        ]
        if failed:
            lines.append(f"docker push {image} aborted")
            lines.append(f"Finished: {result}")
        else:
            lines.append(f"docker push {image}")
            lines.append(f"pushed image {image}")
            lines.append(f"Finished: {result}")
        url = f"{self.base_url}/job/{job}/{number}/"
        build = JenkinsBuild(
            number=number,
            result=result,
            building=False,
            url=url,
            duration_ms=1200,
        )
        self._builds[(job, number)] = (build, "\n".join(lines) + "\n")
        self._queue[number] = (job, number)
        return JenkinsQueueItem(id=number, build_number=number, cancelled=False, url=url)

    async def get_queue_item(self, queue_id: int) -> JenkinsQueueItem:
        queued = self._queue.get(queue_id)
        if queued is None:
            raise IntegrationError(f"Jenkins queue item {queue_id} was not found")
        _job, number = queued
        return JenkinsQueueItem(
            id=queue_id,
            build_number=number,
            cancelled=False,
            url=f"{self.base_url}/queue/item/{queue_id}/",
        )

    async def get_build(self, job: str, number: int) -> JenkinsBuild:
        found = self._builds.get((job, number))
        if found is None:
            raise IntegrationError(f"Jenkins build {job} #{number} was not found")
        return found[0]

    async def get_console(self, job: str, number: int) -> str:
        found = self._builds.get((job, number))
        if found is None:
            raise IntegrationError(f"Jenkins console for {job} #{number} was not found")
        return found[1]


class LiveJenkinsClient(JenkinsClient):
    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        token: str,
        client: httpx.AsyncClient,
    ) -> None:
        self.base_url = base_url
        self._auth = httpx.BasicAuth(user, token) if user else None
        self._client = client

    async def trigger_job(self, job: str, parameters: dict[str, str]) -> JenkinsQueueItem:
        headers = await self._crumb_headers()
        path = f"{jenkins_job_path(job)}/buildWithParameters"
        response = await self._client.post(
            build_url(self.base_url, path),
            data=parameters,
            headers=headers,
            auth=self._auth,
            follow_redirects=False,
        )
        if response.status_code not in {200, 201, 202, 302, 303}:
            raise IntegrationError(
                f"Jenkins trigger failed ({response.status_code}): {response.text[:400]}"
            )
        location = response.headers.get("location", "")
        match = _QUEUE_RE.search(location)
        if match is None:
            detail = location or "(missing)"
            raise IntegrationError(f"Jenkins did not return a queue location: {detail}")
        queue_id = int(match.group(1))
        return JenkinsQueueItem(id=queue_id, build_number=None, cancelled=False, url=location)

    async def get_queue_item(self, queue_id: int) -> JenkinsQueueItem:
        data = await self._get_json(f"/queue/item/{queue_id}/api/json")
        executable = data.get("executable") if isinstance(data.get("executable"), dict) else {}
        number = executable.get("number")
        build_number = int(number) if isinstance(number, int) else None
        return JenkinsQueueItem(
            id=queue_id,
            build_number=build_number,
            cancelled=bool(data.get("cancelled")),
            url=str(executable.get("url") or data.get("url") or ""),
        )

    async def get_build(self, job: str, number: int) -> JenkinsBuild:
        path = f"{jenkins_job_path(job)}/{number}/api/json"
        data = await self._get_json(path)
        result = data.get("result")
        return JenkinsBuild(
            number=int(data.get("number") or number),
            result=str(result) if isinstance(result, str) else None,
            building=bool(data.get("building")),
            url=str(data.get("url") or ""),
            duration_ms=int(data.get("duration") or 0),
        )

    async def get_console(self, job: str, number: int) -> str:
        path = f"{jenkins_job_path(job)}/{number}/consoleText"
        response = await self._client.get(
            build_url(self.base_url, path),
            auth=self._auth,
        )
        if response.status_code >= 400:
            raise IntegrationError(
                f"Jenkins console fetch failed ({response.status_code}): {response.text[:400]}"
            )
        return response.text

    async def _crumb_headers(self) -> dict[str, str]:
        response = await self._client.get(
            build_url(self.base_url, "/crumbIssuer/api/json"),
            auth=self._auth,
        )
        if response.status_code == 404:
            return {}
        if response.status_code in {401, 403}:
            raise IntegrationError(
                "Jenkins rejected the crumb request; check JENKINS_USER and JENKINS_TOKEN"
            )
        if response.status_code >= 400:
            raise IntegrationError(
                f"Jenkins crumb request failed ({response.status_code}): {response.text[:400]}"
            )
        try:
            data = response.json()
            field = str(data["crumbRequestField"])
            crumb = str(data["crumb"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrationError("Jenkins crumb response is missing required fields") from exc
        return {field: crumb}

    async def _get_json(self, path: str) -> dict[str, object]:
        response = await self._client.get(build_url(self.base_url, path), auth=self._auth)
        if response.status_code >= 400:
            raise IntegrationError(
                f"Jenkins GET {path} failed ({response.status_code}): {response.text[:400]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise IntegrationError(
                "Jenkins returned non-JSON content; check the base URL and credentials"
            ) from exc
        if not isinstance(data, dict):
            raise IntegrationError("Jenkins returned unexpected JSON")
        return data


def create_jenkins(settings: Settings, client: httpx.AsyncClient | None = None) -> JenkinsClient:
    if settings.jenkins_mode == "mock":
        return MockJenkinsClient(settings.jenkins_base_url)
    if client is None:
        raise IntegrationError("live Jenkins mode requires an HTTP client")
    return LiveJenkinsClient(
        base_url=settings.jenkins_base_url,
        user=settings.jenkins_user,
        token=settings.jenkins_token,
        client=client,
    )


async def wait_for_build(
    client: JenkinsClient,
    *,
    job: str,
    queue_id: int,
    timeout: float,
    interval: float,
) -> tuple[JenkinsBuild, str]:
    """Poll until Jenkins assigns a build number and that build leaves the queue."""
    deadline = time.monotonic() + timeout
    number: int | None = None
    while time.monotonic() < deadline:
        item = await client.get_queue_item(queue_id)
        if item.cancelled:
            raise IntegrationError(f"Jenkins 队列项 {queue_id} 已取消")
        if item.build_number is not None:
            number = item.build_number
            break
        await asyncio.sleep(interval)
    if number is None:
        raise IntegrationError("等待 Jenkins 分配构建号超时")

    while time.monotonic() < deadline:
        build = await client.get_build(job, number)
        if not build.building and build.result:
            console = await client.get_console(job, number)
            return build, console
        await asyncio.sleep(interval)
    raise IntegrationError(f"等待 Jenkins 构建 {job} #{number} 结束超时")
