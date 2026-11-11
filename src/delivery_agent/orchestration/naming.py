"""Image coordinates shared by the agent and the Jenkins build parameters."""

from __future__ import annotations

import re

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def image_repository(registry: str, namespace: str, product: str) -> str:
    cleaned = _SCHEME_RE.sub("", registry.strip()).rstrip("/").lower()
    scope = namespace.strip().strip("/").lower()
    name = product.strip().lower()
    if not cleaned or not scope or not name:
        raise ValueError("image coordinates are incomplete")
    if any(part in {".", ".."} for part in (*cleaned.split("/"), *scope.split("/"), name)):
        raise ValueError("image coordinates are invalid")
    return f"{cleaned}/{scope}/{name}"


def image_tag(sha: str, environment: str) -> str:
    if len(sha) < 12:
        raise ValueError("commit SHA is too short for an image tag")
    return f"{sha[:12]}-{environment}"


def image_reference(
    registry: str,
    namespace: str,
    product: str,
    sha: str,
    environment: str,
) -> tuple[str, str, str]:
    repository = image_repository(registry, namespace, product)
    tag = image_tag(sha, environment)
    return repository, tag, f"{repository}:{tag}"
