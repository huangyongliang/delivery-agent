"""Docker image build and push. Mock mode records the tag without a daemon."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass

from delivery_agent.config import Settings
from delivery_agent.errors import IntegrationError


@dataclass(frozen=True)
class ImageSpec:
    context_path: str
    dockerfile: str
    repository: str
    tag: str
    build_args: dict[str, str]


@dataclass(frozen=True)
class PublishedImage:
    reference: str
    digest: str | None
    log: str
    pushed: bool


class DockerPublisher(ABC):
    @abstractmethod
    async def publish(self, spec: ImageSpec) -> PublishedImage:
        """Build and push one image. The reference includes the tag."""


class MockDockerPublisher(DockerPublisher):
    async def publish(self, spec: ImageSpec) -> PublishedImage:
        reference = f"{spec.repository}:{spec.tag}"
        if spec.repository.endswith("/broken-image"):
            raise IntegrationError(f"docker push failed for {reference}")
        digest = "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
        log = (
            f"mock docker build -f {spec.dockerfile} -t {reference} {spec.context_path}\n"
            f"mock pushed image {reference}\n"
            f"digest {digest}\n"
        )
        return PublishedImage(reference=reference, digest=digest, log=log, pushed=True)


class LocalDockerPublisher(DockerPublisher):
    """Shell out to the Docker CLI. Requires a reachable daemon."""

    async def publish(self, spec: ImageSpec) -> PublishedImage:
        if shutil.which("docker") is None:
            raise IntegrationError("docker CLI was not found on PATH")
        reference = f"{spec.repository}:{spec.tag}"
        build_cmd = [
            "docker",
            "build",
            "-f",
            spec.dockerfile,
            "-t",
            reference,
        ]
        for key, value in spec.build_args.items():
            build_cmd.extend(["--build-arg", f"{key}={value}"])
        build_cmd.append(spec.context_path)
        build_log = await _run(build_cmd)
        push_log = await _run(["docker", "push", reference])
        digest = _digest_from_push(push_log)
        return PublishedImage(
            reference=reference,
            digest=digest,
            log=build_log + "\n" + push_log,
            pushed=True,
        )


def create_docker(settings: Settings) -> DockerPublisher:
    if settings.docker_mode == "mock":
        return MockDockerPublisher()
    return LocalDockerPublisher()


async def _run(args: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    text = stdout.decode(errors="replace")
    if process.returncode != 0:
        rendered = " ".join(args)
        raise IntegrationError(f"command failed ({process.returncode}): {rendered}\n{text[-2000:]}")
    return text


def _digest_from_push(log: str) -> str | None:
    for line in log.splitlines():
        if "digest:" in line:
            return line.split("digest:", 1)[1].strip().split()[0]
    return None
