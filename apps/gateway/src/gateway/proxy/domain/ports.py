"""Domain ports for the proxy module — zero framework imports.

Contract FROZEN @ v1 (proxy-completions TASK.md §3).
Additive extension @ model-mgmt TASK.md §3:
  - ModelAccess tri-state enum (ACTIVE | UNKNOWN | TENANT_DISABLED)
  - ModelChecker.check_for_tenant (new method — is_active UNCHANGED)
Additive extension @ guardrails-core TASK.md §3:
  - GuardrailEvaluator Protocol (evaluate_pre + evaluate_post)
Additive extension @ model-fallbacks TASK.md §3:
  - ModelHealthGate Protocol (is_available / record_failure / record_success)
Additive extension @ provider-seam TASK.md §3:
  - UpstreamProvider Protocol (post_json / post_multipart / stream_bytes)
    Three-method surface for non-chat modalities (embedding/image/audio_stt/audio_tts).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, TypedDict, runtime_checkable

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.domain.entities import GuardrailResult
from gateway.proxy.domain.provider_credentials import ProviderCredential


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
      usage_source      — provenance of the billed usage: "frame" (real terminal
                          usage frame) | "stream_fallback" (stream omitted/partialed
                          the frame) (stream-usage-completeness)
    """

    team_id: uuid.UUID
    cached: bool
    guardrail_blocked: bool
    blocked_by: str
    pii_masked: bool
    pricing_unit: str
    quantity: Decimal
    usage_source: str


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
class VectorCache(Protocol):
    """Domain port for the embedding-similarity ("vector") response cache (semantic-cache task §3).

    The third lookup layer (after exact + normalization). lookup() returns a cached body when a
    near-duplicate prompt's embedding is within the configured cosine threshold; store() registers
    a served response's prompt embedding. Both are fail-safe: lookup → None and store → no-op on
    any failure (the layer can never fail the served request).
    """

    async def lookup(
        self, *, tenant_id: str, model: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the cached body for a near-duplicate prompt, or None on miss/failure."""
        ...

    async def store(
        self,
        *,
        tenant_id: str,
        model: str,
        body: dict[str, Any],
        response_body: dict[str, Any],
        ttl: int,
    ) -> None:
        """Register the served prompt's embedding pointing at the exact body. No-op on failure."""
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


@runtime_checkable
class UpstreamProvider(Protocol):
    """Typed port for non-chat upstream adapters (provider-seam TASK.md §3).

    Three-method surface designed by inspection of the four non-chat modalities:
      embeddings  → post_json("/embeddings", ...) → (status, body)
      images      → post_json("/images/generations", ...) → (status, body)
      audio_stt   → post_multipart("/audio/transcriptions", files, data) → (status, body)
      audio_tts   → stream_bytes("/audio/speech", ...) → AsyncIterator[bytes]

    Must be runtime_checkable so isinstance(provider, UpstreamProvider) works in
    production wiring regression tests (PS8, PS10).

    The chat path (CompletionUpstream) is NOT replaced; this is an additive seam.
    """

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST path with JSON body; returns (status_code, json_body).

        Used by: embeddings (POST /embeddings), images (POST /images/generations).
        """
        ...

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST path with multipart/form-data; returns (status_code, json_body).

        Used by: audio STT (POST /audio/transcriptions).
        """
        ...

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an async generator yielding raw bytes.

        Used by: audio TTS (POST /audio/speech).
        Raises UpstreamUnavailableError on first byte failure.
        Zero retry machinery (same rule as CompletionUpstream.stream()).
        """
        ...


@runtime_checkable
class ProviderResolver(Protocol):
    """Resolve the catalog provider name for a served model id.

    Additive extension @ provider-chat-dispatch TASK.md §3 (FROZEN @ v1).

    Implementations hold an in-memory cache built from the catalog at startup
    and refreshed on /internal/catalog/sync. The hot path is in-memory only.

    Contract:
      - provider_for NEVER raises. Unknown/unset model_id returns "openrouter".
      - refresh() is fail-safe: errors are logged + swallowed; last-good map kept.
    """

    async def provider_for(self, model_id: str) -> str:
        """Return the catalog provider for model_id; 'openrouter' for unknown/unset.

        NEVER raises — resolution failure degrades to 'openrouter'.
        """
        ...


@runtime_checkable
class DeploymentLoadGate(Protocol):
    """Async port exposing per-deployment live load metrics backed by Redis.

    Additive extension @ balance-strategies TASK.md §3.
    Fail-OPEN: any Redis read error returns the neutral value (in_flight=0 /
    ewma=0.0); acquire/release errors are swallowed. Logs deployment_id only —
    never key strings or secrets.
    """

    async def acquire(self, deployment_id: str) -> None:
        """Increment in-flight counter and refresh EXPIRE(ttl) for the key."""
        ...

    async def release(self, deployment_id: str) -> None:
        """Decrement in-flight counter (read clamps negative→0)."""
        ...

    async def in_flight(self, deployment_id: str) -> int:
        """Return current in-flight count (≥ 0). Fail-OPEN → 0 on error."""
        ...

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        """Update the per-deployment EWMA latency with a new sample."""
        ...

    async def latency_ewma(self, deployment_id: str) -> float:
        """Return recent EWMA latency in ms. Unseen deployment → 0.0. Fail-OPEN → 0.0."""
        ...


@runtime_checkable
class DeploymentLimitGate(Protocol):
    """Async port for per-deployment RPM/TPM saturation checks and recording.

    Additive extension @ deployment-limits TASK.md §3.
    Fail-OPEN: any Redis error in is_saturated → False (admit, never false-429).
    record_* errors are fail-soft (swallowed). Logs deployment_id only — never
    key strings or secrets.

    is_saturated is a READ-ONLY peek:
      True iff (rpm_limit is not None AND rpm-window count >= rpm_limit)
            OR (tpm_limit is not None AND tpm-window sum >= tpm_limit).
      Both None => False with ZERO Redis commands (fast path).
    """

    async def is_saturated(
        self,
        deployment_id: str,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> bool:
        """Return True iff deployment has exceeded its RPM or TPM limit this window.

        Both-None fast path: returns False with zero Redis IO.
        Fail-OPEN: any error returns False (admit).
        """
        ...

    async def record_request(self, deployment_id: str) -> None:
        """Increment the per-minute RPM window for deployment_id (on serve).

        Fail-soft: errors are swallowed (logged with deployment_id only).
        """
        ...

    async def record_tokens(self, deployment_id: str, tokens: int) -> None:
        """Add tokens to the per-minute TPM window for deployment_id (post-response).

        Fail-soft: errors are swallowed (logged with deployment_id only).
        """
        ...


@runtime_checkable
class TenantCredentialResolver(Protocol):
    """Resolve the per-tenant, per-provider credential for an upstream call.

    Additive extension @ credential-resolution-seam TASK.md §3 (FROZEN @ v1).

    Implementations wrap ``TenantProviderKeyStore.get`` with an in-memory TTL
    cache (warm path = zero DB calls within TTL) and a bounded ``asyncio``
    timeout on the cold DB fetch.

    Contract:
      - resolve ALWAYS raises ``ProviderKeyMissing`` on absent / disabled / None
        / timeout — NEVER returns a fallback or empty credential.
      - Only positive (hit) results are cached; a miss is NOT cached so a
        freshly-configured key takes effect immediately.
    """

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        """Return the enabled credential for ``(tenant_id, provider)``.

        Raises:
            ProviderKeyMissing: when the store returns None (absent or
                disabled row), when the DB fetch exceeds the bounded timeout,
                or when a decrypt error propagates from the store.
                NEVER returns a platform key as a fallback.
        """
        ...


__all__ = [
    "AuthzResult",
    "CompletionUpstream",
    "DeploymentLimitGate",
    "DeploymentLoadGate",
    "GuardrailEvaluator",
    "KeyAuthenticator",
    "ModelAccess",
    "ModelChecker",
    "ModelHealthGate",
    "ProviderCredential",
    "ProviderResolver",
    "ResponseCache",
    "TenantCredentialResolver",
    "UpstreamProvider",
    "UsageRecordExtras",
    "UsageRecorder",
    "VectorCache",
]
