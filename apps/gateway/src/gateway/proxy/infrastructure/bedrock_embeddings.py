"""Infrastructure adapter: BedrockEmbeddingsProvider.

Translates OpenAI /v1/embeddings ⇄ AWS Bedrock Titan InvokeModel API.

Wire protocol:
  POST https://bedrock-runtime.{region}.amazonaws.com/model/{model_id}/invoke
  Headers: Authorization (SigV4), x-amz-date, x-amz-content-sha256,
           content-type: application/json, accept: application/json
  Body: {"inputText": <str>} (one call per input — Titan has no batch endpoint)

The model_id is placed RAW into the URL path (same as BedrockCompletionUpstream).
The IDENTICAL url string is passed to BOTH sign_request AND client.post so the
signed x-amz-content-sha256 matches the wire body exactly (v20 task-2 %3A rule).

Resilience mirrors BedrockCompletionUpstream:
  - Per-instance CircuitBreaker (5 consecutive failures → 30 s open)
  - ConnectError / TimeoutException / NetworkError → UpstreamUnavailableError
  - 4xx → pass through as OpenAI-shaped error body via _bedrock_error_to_openai;
    fail-fast: on the FIRST invoke with status >= 400, return immediately —
    no further calls for remaining list items.

Security:
  The AWS secret_access_key is a SECRET — NEVER logged, echoed, committed, or
  placed in metric labels / span attributes / exception messages.
  The signed x-amz-content-sha256 MUST match the exact POSTed bytes
  (use content=body_bytes, never json= which re-encodes).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BedrockCredential, ProviderKeyMissing
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials, sign_request
from gateway.proxy.infrastructure.bedrock_upstream import (
    _assert_region_consistent,  # pyright: ignore[reportPrivateUsage]
    _bedrock_error_to_openai,  # pyright: ignore[reportPrivateUsage]
)
from gateway.proxy.infrastructure.circuit_breaker import (
    status_counts_as_upstream_failure,
)
from gateway.proxy.infrastructure.tenant_breaker_registry import TenantScopedBreakerMixin

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# Pure translation helpers
# ---------------------------------------------------------------------------


def _titan_to_openai_embeddings(
    vectors: list[list[float]],
    *,
    model_id: str,
    total_tokens: int,
) -> dict[str, Any]:
    """Translate a list of Titan embedding vectors → OpenAI embeddings list shape.

    Args:
        vectors:      Ordered list of float vectors, one per input text.
        model_id:     The raw Titan model ID echoed back in the response.
        total_tokens: Sum of inputTextTokenCount across all invoke calls.

    Returns:
        OpenAI-shaped ``{"object": "list", "data": [...], "model": ..., "usage": {...}}``.
    """
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
        ],
        "model": model_id,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


# ---------------------------------------------------------------------------
# BedrockEmbeddingsProvider — embeddings seam (UpstreamProvider Protocol)
# ---------------------------------------------------------------------------


class BedrockEmbeddingsProvider(TenantScopedBreakerMixin):
    """Direct HTTP adapter for AWS Bedrock Titan embedding endpoints.

    Implements the UpstreamProvider Protocol (post_json / post_multipart / stream_bytes).
    Translates OpenAI /v1/embeddings ⇄ Titan InvokeModel API.

    A single instance is registered UNCONDITIONALLY per create_app() call; the AWS
    credentials are resolved per-request from the BedrockCredential contextvar
    (task-3 dynamic-auth-byok) — a missing/wrong-type credential fails closed with
    ProviderKeyMissing("bedrock") before any signing.

    SECURITY: the AWS secret_access_key is NEVER logged, echoed, or placed in any
    metric label / span attribute / exception message.
    Auth is SigV4 (Authorization header) only.
    """

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # No boot credentials — resolved per-request from the contextvar (task-3 BYOK).
        self._endpoint_url_override = endpoint_url  # None = derive from credential region
        self._metrics_registry = metrics_registry

        self._init_tenant_breakers()
        # NOTE: No base_url set — the signer needs the full absolute URL to compute
        # the canonical host header and canonical URI correctly.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            )
        )

    def _get_credentials(self) -> AwsCredentials:
        """Read BedrockCredential from the request contextvar and convert to AwsCredentials.

        Fail-CLOSED: raises ProviderKeyMissing("bedrock") if the contextvar is unset or
        holds a wrong-type credential — NEVER produces an unsigned upstream request.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BedrockCredential):
            raise ProviderKeyMissing("bedrock")
        return cred.to_aws_credentials()

    def _build_endpoint(self, aws: AwsCredentials) -> str:
        """Derive the Bedrock endpoint from the credential's region (or ctor override)."""
        return (
            self._endpoint_url_override or f"https://bedrock-runtime.{aws.region}.amazonaws.com"
        ).rstrip("/")

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST embeddings to Titan InvokeModel; returns (status_code, openai_shaped_body).

        Args:
            path:    Ignored (Titan endpoint path is derived from payload["model"]).
            payload: OpenAI embeddings request with keys "model", "input",
                     and optionally "dimensions".

        Credential is read from the request contextvar (BedrockCredential); raises
        ProviderKeyMissing("bedrock") before any HTTP if unset or wrong type (fail-closed).

        Fail-closed BEFORE any HTTP/IO, in order: credential resolution, model_id
        resolution, then the region-consistency guard (residency-bedrock-region-guard,
        FROZEN @ v1) — raises ERR_BEDROCK_REGION_MISMATCH before _build_endpoint or
        any invoke call.

        Raises:
            UpstreamUnavailableError: on ConnectError, TimeoutException, or NetworkError.

        Returns:
            (200, OpenAI-list-shape) on all-success, or
            (4xx, error_body) on the FIRST failed invoke call (fail-fast).
        """
        # Fail-closed: read & validate credential BEFORE any network activity.
        breaker = self._breaker_for()
        aws = self._get_credentials()

        model_id: str = payload["model"]
        # residency-bedrock-region-guard (FROZEN @ v1): BEFORE _build_endpoint or any
        # httpx call — the credential's AWS region geo must match the model's
        # cross-region-inference-profile geo (unprefixed ids are unconstrained, M3).
        _assert_region_consistent(model_id, aws.region)

        endpoint = self._build_endpoint(aws)

        inp: str | list[str] = payload["input"]
        texts: list[str] = [inp] if isinstance(inp, str) else list(inp)

        vectors: list[list[float]] = []
        total_tokens: int = 0

        # The model_id is placed RAW into the URL path (it carries a ':' version
        # suffix, e.g. ...-v2:0). The IDENTICAL url string is passed to BOTH
        # sign_request AND client.post so the signed canonical URI and the wire
        # target stay in lock-step (v20 task-2 %3A rule: no double-encode).
        url = f"{endpoint}/model/{model_id}/invoke"

        for text in texts:
            body: dict[str, Any] = {"inputText": text}
            if "dimensions" in payload:
                body["dimensions"] = payload["dimensions"]

            body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")

            breaker.guard()
            try:
                sig = sign_request(
                    method="POST",
                    url=url,
                    body=body_bytes,
                    service="bedrock",
                    region=aws.region,
                    credentials=aws,
                    timestamp=datetime.now(UTC),
                )
                # Use content=body_bytes (raw bytes) so the signed x-amz-content-sha256
                # matches the wire body EXACTLY — httpx must NOT re-encode the body.
                resp = await self._client.post(
                    url,
                    content=body_bytes,
                    headers={
                        **sig,
                        "content-type": "application/json",
                        "accept": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

            if resp.status_code >= 400:
                # Fail-fast: return immediately on the first non-2xx invoke call. The
                # fail-fast is about the N-call fan-out (don't keep invoking once one
                # chunk has failed) and is unchanged; only the BREAKER accounting moved.
                # A ValidationException on a malformed request is the caller's mistake,
                # not an outage, and this breaker is per-INSTANCE — counting it would take
                # Titan embeddings down for every other caller on this replica.
                if status_counts_as_upstream_failure(resp.status_code):
                    breaker.on_upstream_error()
                else:
                    breaker.record_success()
                return resp.status_code, _bedrock_error_to_openai(resp.json(), resp.status_code)

            breaker.record_success()
            j = resp.json()
            vectors.append(j["embedding"])
            total_tokens += int(j.get("inputTextTokenCount", 0))

        return 200, _titan_to_openai_embeddings(
            vectors, model_id=model_id, total_tokens=total_tokens
        )

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Raise UpstreamUnavailableError — Bedrock Titan images/audio out of scope."""
        raise UpstreamUnavailableError("bedrock-embeddings: unsupported modality")

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an async generator that raises UpstreamUnavailableError on first iteration.

        Bedrock Titan embeddings do not stream — never reached for embedding-modality models.
        """

        async def _gen() -> AsyncIterator[bytes]:
            raise UpstreamUnavailableError("bedrock-embeddings: unsupported modality")
            # Unreachable — makes the type checker happy that this is an async generator
            yield b""  # pragma: no cover

        return _gen()


__all__ = [
    "BedrockEmbeddingsProvider",
    "_titan_to_openai_embeddings",
]
