"""Domain ports for the proxy module — zero framework imports.

Contract FROZEN @ v1 (proxy-completions TASK.md §3).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from gateway.keys.domain.entities import AuthzResult


@runtime_checkable
class KeyAuthenticator(Protocol):
    """Authenticate an API key and return tenant/key identity."""

    async def authenticate(self, raw_key: str) -> AuthzResult:
        """Validate the raw Bearer key value.

        Returns AuthzResult on success.
        Raises gateway.keys.domain.errors.InvalidApiKeyError on any failure
        (malformed / unknown / revoked / wrong secret — no distinguishing detail).
        """
        ...


@runtime_checkable
class ModelChecker(Protocol):
    """Check whether a model is active in the catalog."""

    async def is_active(self, model_id: str) -> bool:
        """Return True iff model exists and active=true in the catalog."""
        ...


@runtime_checkable
class CompletionUpstream(Protocol):
    """Forward completion requests to an upstream LLM provider."""

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        """Forward a non-streaming request.

        Returns (status_code, json_body).
        Raises UpstreamUnavailableError on 5xx / timeout / network error.
        """
        ...

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        """Yield raw SSE byte chunks from upstream — byte-identical pass-through.

        Raises UpstreamUnavailableError on 5xx / timeout / network error.
        """
        ...


@runtime_checkable
class UsageRecorder(Protocol):
    """Record a usage event after each completion attempt."""

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, object] | None,
        status: int,
    ) -> None:
        """Append a usage event.

        Called fire-and-forget; NoopUsageRecorder by default.
        Must not raise — failures are silently swallowed to avoid affecting the
        caller's response.
        """
        ...


__all__ = [
    "AuthzResult",
    "CompletionUpstream",
    "KeyAuthenticator",
    "ModelChecker",
    "UsageRecorder",
]
