"""GitLab API client with a live HTTP implementation and an offline mock."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from delivery_agent.config import Settings
from delivery_agent.errors import IntegrationError, ProjectNotFound
from delivery_agent.models import GitLabWebhook
from delivery_agent.urls import (
    build_url,
    gitlab_commit_path,
    gitlab_pipeline_collection_path,
    gitlab_pipeline_path,
    gitlab_project_path,
)


@dataclass(frozen=True)
class GitProject:
    id: int
    path_with_namespace: str
    http_url_to_repo: str
    web_url: str
    default_branch: str


@dataclass(frozen=True)
class GitCommit:
    sha: str
    title: str
    web_url: str


@dataclass(frozen=True)
class GitPipeline:
    id: int
    status: str
    ref: str
    sha: str
    web_url: str


class GitLabClient(ABC):
    @abstractmethod
    async def get_project(self, path: str) -> GitProject:
        """Look up a project by namespace path."""

    @abstractmethod
    async def get_commit(self, project_id: int, ref: str) -> GitCommit:
        """Resolve a branch, tag, or SHA to a commit."""

    @abstractmethod
    async def trigger_pipeline(
        self,
        project_id: int,
        ref: str,
        variables: dict[str, str],
    ) -> GitPipeline:
        """Create a pipeline for ref. Returning means GitLab accepted it."""

    @abstractmethod
    async def get_pipeline(self, project_id: int, pipeline_id: int) -> GitPipeline:
        """Read the current pipeline status."""


class MockGitLabClient(GitLabClient):
    """Deterministic GitLab stand-in.

    A path segment named ``missing`` is reported as not found so the demo and
    tests can exercise the failure path without a server.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._projects: dict[int, GitProject] = {}
        self._pipelines: dict[tuple[int, int], GitPipeline] = {}
        self._sequence = 5000

    async def get_project(self, path: str) -> GitProject:
        if any(part == "missing" for part in path.split("/")):
            raise ProjectNotFound(f"找不到 GitLab 项目：{path}")
        project_id = 100000 + int(hashlib.sha256(path.encode()).hexdigest()[:6], 16) % 800000
        project = GitProject(
            id=project_id,
            path_with_namespace=path,
            http_url_to_repo=f"{self.base_url}/{path}.git",
            web_url=f"{self.base_url}/{path}",
            default_branch="main",
        )
        self._projects[project.id] = project
        return project

    async def get_commit(self, project_id: int, ref: str) -> GitCommit:
        project_path = self._path_for(project_id)
        sha = hashlib.sha256(f"{project_path}@{ref}".encode()).hexdigest()[:40]
        return GitCommit(
            sha=sha,
            title=f"delivery snapshot for {ref}",
            web_url=f"{self.base_url}/{project_path}/-/commit/{sha}",
        )

    async def trigger_pipeline(
        self,
        project_id: int,
        ref: str,
        variables: dict[str, str],
    ) -> GitPipeline:
        commit = await self.get_commit(project_id, ref)
        self._sequence += 1
        pipeline = GitPipeline(
            id=self._sequence,
            status="created",
            ref=ref,
            sha=commit.sha,
            web_url=f"{self.base_url}/{self._path_for(project_id)}/-/pipelines/{self._sequence}",
        )
        self._pipelines[(project_id, pipeline.id)] = pipeline
        return pipeline

    async def get_pipeline(self, project_id: int, pipeline_id: int) -> GitPipeline:
        pipeline = self._pipelines.get((project_id, pipeline_id))
        if pipeline is None:
            raise IntegrationError(f"GitLab pipeline {pipeline_id} was not found")
        return pipeline

    def _path_for(self, project_id: int) -> str:
        project = self._projects.get(project_id)
        if project is None:
            return f"projects/{project_id}"
        return project.path_with_namespace


class LiveGitLabClient(GitLabClient):
    def __init__(self, *, base_url: str, token: str, client: httpx.AsyncClient) -> None:
        self.base_url = base_url
        self.token = token
        self._client = client

    async def get_project(self, path: str) -> GitProject:
        data = await self._request("GET", gitlab_project_path(path), not_found=path)
        return _project_from_json(data)

    async def get_commit(self, project_id: int, ref: str) -> GitCommit:
        data = await self._request("GET", gitlab_commit_path(project_id, ref))
        sha = str(data.get("id") or "")
        if len(sha) < 7:
            raise IntegrationError("GitLab commit response did not include a SHA")
        return GitCommit(
            sha=sha,
            title=str(data.get("title") or data.get("message") or ref),
            web_url=str(data.get("web_url") or ""),
        )

    async def trigger_pipeline(
        self,
        project_id: int,
        ref: str,
        variables: dict[str, str],
    ) -> GitPipeline:
        body = {
            "ref": ref,
            "variables": [{"key": key, "value": value} for key, value in variables.items()],
        }
        data = await self._request(
            "POST",
            gitlab_pipeline_collection_path(project_id),
            json_body=body,
        )
        return _pipeline_from_json(data, fallback_ref=ref)

    async def get_pipeline(self, project_id: int, pipeline_id: int) -> GitPipeline:
        data = await self._request("GET", gitlab_pipeline_path(project_id, pipeline_id))
        return _pipeline_from_json(data, fallback_ref="")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        not_found: str | None = None,
    ) -> dict[str, object]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        response = await self._client.request(
            method,
            build_url(self.base_url, path),
            headers=headers,
            json=json_body,
        )
        if response.status_code == 404:
            target = not_found or path
            raise ProjectNotFound(f"找不到 GitLab 资源：{target}")
        if response.status_code >= 400:
            raise IntegrationError(
                f"GitLab {method} {path} failed ({response.status_code}): {response.text[:400]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise IntegrationError("GitLab returned non-JSON content") from exc
        if not isinstance(data, dict):
            raise IntegrationError("GitLab returned unexpected JSON")
        return data


def create_gitlab(settings: Settings, client: httpx.AsyncClient | None = None) -> GitLabClient:
    if settings.gitlab_mode == "mock":
        return MockGitLabClient(settings.gitlab_base_url)
    if client is None:
        raise IntegrationError("live GitLab mode requires an HTTP client")
    return LiveGitLabClient(
        base_url=settings.gitlab_base_url,
        token=settings.gitlab_token,
        client=client,
    )


def parse_gitlab_webhook(payload: dict[str, object]) -> GitLabWebhook:
    kind = str(payload.get("object_kind") or payload.get("event_name") or "unknown")
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    raw_attributes = payload.get("object_attributes")
    attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
    project_path = _optional_str(project.get("path_with_namespace"))
    if kind == "push":
        ref = normalize_ref(_optional_str(payload.get("ref")))
        sha = _optional_str(payload.get("checkout_sha") or payload.get("after"))
        status = None
        pipeline_id = None
    else:
        ref = normalize_ref(_optional_str(attributes.get("ref")))
        sha = _optional_str(attributes.get("sha"))
        status = _optional_str(attributes.get("status"))
        pipeline_id = attributes.get("id") if isinstance(attributes.get("id"), int) else None
    summary_bits = [f"GitLab {kind}"]
    if project_path:
        summary_bits.append(project_path)
    if ref:
        summary_bits.append(ref)
    if status:
        summary_bits.append(status)
    if pipeline_id is not None:
        summary_bits.append(f"#{pipeline_id}")
    return GitLabWebhook(
        kind=kind,
        project_path=project_path,
        ref=ref,
        sha=sha,
        status=status,
        pipeline_id=pipeline_id,
        summary=" ".join(summary_bits),
    )


def normalize_ref(ref: str | None) -> str | None:
    if ref is None:
        return None
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _project_from_json(data: dict[str, object]) -> GitProject:
    try:
        return GitProject(
            id=int(data["id"]),
            path_with_namespace=str(data["path_with_namespace"]),
            http_url_to_repo=str(data.get("http_url_to_repo") or ""),
            web_url=str(data.get("web_url") or ""),
            default_branch=str(data.get("default_branch") or "main"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationError("GitLab project response is missing required fields") from exc


def _pipeline_from_json(data: dict[str, object], *, fallback_ref: str) -> GitPipeline:
    try:
        return GitPipeline(
            id=int(data["id"]),
            status=str(data.get("status") or "created"),
            ref=str(data.get("ref") or fallback_ref),
            sha=str(data.get("sha") or ""),
            web_url=str(data.get("web_url") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationError("GitLab pipeline response is missing required fields") from exc
