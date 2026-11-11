"""Small shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def clip_text(value: str | None, limit: int = 16000) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[-limit:]
