"""Errors raised by integrations and the orchestrator."""

from __future__ import annotations


class IntegrationError(Exception):
    """A GitLab, Jenkins, or Docker call failed."""


class ProjectNotFound(IntegrationError):
    """The requested GitLab project does not exist."""


class ProviderError(Exception):
    """An LLM provider failed or is misconfigured."""


class DeliveryNotFound(Exception):
    """No delivery exists for the given id."""

    def __init__(self, delivery_id: str) -> None:
        self.delivery_id = delivery_id
        super().__init__(delivery_id)


class DeliveryAborted(Exception):
    """A callback marked the delivery failed while it was still running."""
