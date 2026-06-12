"""Domain ports for the proxy module — zero framework imports.

Contract FROZEN @ v1 (proxy-completions TASK.md §3).
Additive extension @ model-mgmt TASK.md §3:
  - ModelAccess tri-state enum (ACTIVE | UNKNOWN | TENANT_DISABLED)
  - ModelChecker.check_for_tenant (new method — is_active UNCHANGED)
Additive extension @ guardrails-core TASK.md §3:
  - GuardrailEvaluator Protocol (evaluate_pre + evaluate_post)
Additive extension @ model-fallbacks TASK.md §3:
  - ModelHealthGate Protocol (is_available / record_failure / record_success)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Protocol, TypedDict, runtime_checkable

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.domain.entities import GuardrailResult


class UsageRecordExtras(TypedDict, total=False):
    """Optional extension kwargs for UsageRecorder.record() — typed capability seam.

    The v1 UsageRecorder Protocol signature is frozen; later tasks extended the
    production recorder with additive kwargs. A recorder DECLARES which extras
    it accepts via a class attribute `supported_extras: frozenset[str]` (absent
    on v1-Protocol test fakes → treated as empty → only base kwargs are passed).
    Callers filter this TypedDict against that declaration instead of runtime
    signature introspection.

    Fields:
      team_id           — per-team spend attribution (team-governance)
      cached            — cache-hit marker, forces cost_usd=0 (response-caching)
      guardrail_blocked — raw marker on guardrail block (guardrails-core)
      blocked_by        — guardrail name that triggered the block (guardrails-core)
      pii_masked        — raw marker when pre-call PII masking fired (guardrails-core)
    """

    team_id: uuid.UUID
    cached: bool
    guardrail_blocked: bool
    blocked_by: str
    pii_masked: bool


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


@runtime_checkable
class GuardrailEvaluator(Protocol):
    """Evaluate guardrails on a request (pre-call) and response (post-call).

    New port for guardrails-core (TASK.md §3 CONTRACT).
    Additive — default-None injection into CompletionUseCase is backward-compatible
    with all frozen test fakes that do not implement this protocol.
    """

    async def evaluate_pre(
        self,
        messages: list[dict[str, Any]],
        guardrail_configs: dict[str, Any],
    ) -> GuardrailResult:
        """Evaluate pre-call guardrails (prompt injection + PII mask).

        Returns GuardrailResult. If blocked=True, the caller must raise
        ProblemError(400, "ERR_GUARDRAIL_BLOCKED", ...) and fire a usage record.
        If masked_messages is not None, the caller must substitute the body messages
        before forwarding to upstream.
        Fail-CLOSED: if any active guardrail is in BLOCK mode and an exception is
        raised, returns GuardrailResult(blocked=True, ...) — never lets it through.
        Fail-OPEN: if ALL active guardrails are in MASK/AUDIT mode, logs and returns
        GuardrailResult(blocked=False, events=[GuardrailEvent(action="error", ...)]).
        """
        ...

    async def evaluate_post(
        self,
        response_body: dict[str, Any],
        guardrail_configs: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply post-call PII masking to the upstream response body.

        Returns the (possibly modified) response body.
        Only applies pii_mask in MASK mode to choices[*].message.content.
        On error: logs + returns original body (always fail-OPEN — post-call is MASK/AUDIT only).
        """
        ...


@runtime_checkable
class ModelHealthGate(Protocol):
    """Health gate for per-candidate availability checks.

    Additive protocol for model-fallbacks task (TASK.md §3).
    None (default wiring) = all candidates available; zero gate interaction.
    The cooldown-circuit task implements the Redis-backed gate.

    Gate calls MUST never raise out of the router (gate is fail-open by contract).
    """

    async def is_available(self, model_id: str) -> bool:
        """Return True iff the model should be attempted. False = skip (cooled)."""
        ...

    async def record_failure(self, model_id: str) -> None:
        """Record that this candidate raised UpstreamUnavailableError."""
        ...

    async def record_success(self, model_id: str) -> None:
        """Record that this candidate returned any (status, body) — including 4xx."""
        ...


__all__ = [
    "AuthzResult",
    "CompletionUpstream",
    "GuardrailEvaluator",
    "KeyAuthenticator",
    "ModelAccess",
    "ModelChecker",
    "ModelHealthGate",
    "ResponseCache",
    "UsageRecordExtras",
    "UsageRecorder",
]
