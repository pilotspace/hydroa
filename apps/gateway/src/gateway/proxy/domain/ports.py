"""Domain ports for the proxy module — zero framework imports.

Contract FROZEN @ v1 (proxy-completions TASK.md §3).
Additive extension @ model-mgmt TASK.md §3:
  - ModelAccess tri-state enum (ACTIVE | UNKNOWN | TENANT_DISABLED)
  - ModelChecker.check_for_tenant (new method — is_active UNCHANGED)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from gateway.keys.domain.entities import AuthzResult


class ModelAccess(Enum):
    """Tri-state result of a per-tenant catalog + override check.

    ACTIVE          — model exists, active=true in catalog, tenant has not disabled it.
    UNKNOWN         — model absent from catalog OR catalog active=false.
    TENANT_DISABLED — model is active in catalog but disabled by a tenant override row.
    """

    ACTIVE = "active"
    UNKNOWN = "unknown"
    TENANT_DISABLED = "tenant_disabled"


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
    """Check whether a model is active in the catalog.

    FROZEN method: is_active(model_id) — signature must not change (frozen fakes depend on it).
    ADDITIVE method: check_for_tenant(model_id, tenant_id) — model-mgmt §3 extension.
    """

    async def is_active(self, model_id: str) -> bool:
        """Return True iff model exists and active=true in the catalog."""
        ...

    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        """Return tri-state access for (model_id, tenant_id).

        ACTIVE          — catalog active=true AND no disable override.
        UNKNOWN         — not in catalog OR catalog active=false.
        TENANT_DISABLED — catalog active=true AND tenant override enabled=false.
        """
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


@runtime_checkable
class ResponseCache(Protocol):
    """Domain port for exact-match Redis response cache (response-caching task §3)."""

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        """Return cached body dict for cache_key, or None on miss/error."""
        ...

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        """Store body under cache_key with TTL. Fire-and-forget: errors logged, swallowed."""
        ...


__all__ = [
    "AuthzResult",
    "CompletionUpstream",
    "KeyAuthenticator",
    "ModelAccess",
    "ModelChecker",
    "ResponseCache",
    "UsageRecorder",
]
