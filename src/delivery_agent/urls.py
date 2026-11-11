"""Build API URLs without double-encoding already escaped path segments."""

from __future__ import annotations

from urllib.parse import quote

import httpx


def build_url(base: str, path: str) -> httpx.URL:
    """Join a base URL with a path that may already contain percent-encoding."""
    if not path.startswith("/"):
        path = "/" + path
    base_url = httpx.URL(base.strip().rstrip("/"))
    raw = f"{base_url.path.rstrip('/')}{path}".encode("ascii")
    return base_url.copy_with(raw_path=raw)


def gitlab_project_path(project: str) -> str:
    return f"/api/v4/projects/{quote(project, safe='')}"


def gitlab_commit_path(project_id: int, ref: str) -> str:
    return f"/api/v4/projects/{project_id}/repository/commits/{quote(ref, safe='')}"


def gitlab_pipeline_collection_path(project_id: int) -> str:
    return f"/api/v4/projects/{project_id}/pipeline"


def gitlab_pipeline_path(project_id: int, pipeline_id: int) -> str:
    return f"/api/v4/projects/{project_id}/pipelines/{pipeline_id}"


def jenkins_job_path(job: str) -> str:
    parts = [part for part in job.split("/") if part]
    if not parts:
        raise ValueError("Jenkins job name is empty")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Jenkins job name is invalid")
    return "".join(f"/job/{quote(part, safe='')}" for part in parts)
