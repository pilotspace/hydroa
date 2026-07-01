"""Infrastructure adapter: OpenRouterCompletionUpstream.

Wraps httpx.AsyncClient with:
- Per-request tenant credential injection via request-scoped contextvar
  (credential-resolution-seam TASK.md §3 — removed the platform API key path)
- Connect timeout 10 s, non-stream total 120 s, stream read 300 s
- Circuit breaker (5 consecutive failures -> 30 s open -> half-open)
- Opt-in bounded retries (default GATEWAY_UPSTREAM_MAX_RETRIES=0 = NEVER retry,
  byte-identical to v5 behavior; operators enable retries by setting the knob).
  The "NEVER retry a completion (non-idempotent)" rule from proxy-completions TASK.md §1
  is superseded by this module's retry-policy contract — preserved by construction:
  max_retries=0 (default) makes the behavior byte-identical to the original rule.
  See .add/tasks/retry-policy/TASK.md §3 SUPERSESSION BLOCK.
- Upstream 4xx: pass through verbatim (never retried)
- Upstream 5xx / connect error / pool timeout: raise UpstreamUnavailableError
  (retried when max_retries > 0); read/write timeout / network error: raise
  UpstreamUnavailableError immediately (never retried — conservative against double-billing)
- Retries are confined to complete() ONLY; stream() has zero retry machinery
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.domain.web_search import WEB_SEARCH_FLAG, native_web_search_tool
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_BASE_URL = "https://openrouter.ai/api/v1"
_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GenerationCost:
    """Authoritative cost + native token usage for one OpenRouter generation.

    Returned by ``OpenRouterCompletionUpstream.get_generation``. Money fields are
    ``Decimal`` (billing precision — never float); token counts are ints. Built from
    the ``data`` object of the GET /generation response (v30 cost-recovery).
    """

    total_cost: Decimal
    upstream_inference_cost: Decimal
    native_tokens_prompt: int
    native_tokens_completion: int
    native_tokens_cached: int


def _gen_to_decimal(value: object) -> Decimal:
    """Parse a money value to Decimal via str (never float). Missing/garbage -> 0."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _gen_to_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0


def _parse_generation(body: dict[str, Any]) -> GenerationCost | None:
    """Map a /generation response body to GenerationCost, or None if unavailable.

    Tolerates both ``{"data": {...}}`` and a flat top-level shape. A body without a
    ``total_cost`` is treated as 'not available yet' (None) — same as a non-200, so
    the recovery caller can retry or defer to the sweep backstop.
    """
    data = body.get("data", body)
    if not isinstance(data, dict) or data.get("total_cost") is None:
        return None
    return GenerationCost(
        total_cost=_gen_to_decimal(data.get("total_cost")),
        upstream_inference_cost=_gen_to_decimal(data.get("upstream_inference_cost")),
        native_tokens_prompt=_gen_to_int(data.get("native_tokens_prompt")),
        native_tokens_completion=_gen_to_int(data.get("native_tokens_completion")),
        native_tokens_cached=_gen_to_int(data.get("native_tokens_cached")),
    )


class OpenRouterCompletionUpstream:
    """Forwards completions to OpenRouter with circuit breaker protection.

    A single instance is shared for the lifetime of the application
    (wired in main.py onto app.state.completion_upstream).
    The circuit breaker state is per-instance (per-replica).

    Auth is read per-request from the request-scoped contextvar set by the
    use-case (credential-resolution-seam §3). No api_key constructor argument.

    Retry policy (opt-in):
      _max_retries=0 (default): exactly one attempt — byte-identical to v5.
      _max_retries>0: up to _max_retries additional attempts with full-jitter
      exponential backoff. Retries only on the complete() path; stream() is unchanged.
    """

    # Class-level default so instances built via __new__ (test doubles that don't set
    # every attribute) still resolve the usage-accounting knob to OFF — keeps the
    # outbound request byte-identical. The __init__ below overrides it per-instance.
    _usage_accounting: bool = False

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
        usage_accounting: bool = False,
    ) -> None:
        self._base_url = base_url
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry
        # provider-cost-reconciliation: opt-in (default OFF). When True the outbound
        # payload asks OpenRouter to report its own cost so the recorder can bill on it.
        self._usage_accounting = usage_accounting

    def _maybe_inject_web_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inject the web_search_preview native tool when web_search flag is truthy.

        Returns a shallow copy with:
          - "web_search" key removed (NEVER reaches upstream).
          - {"type":"web_search_preview"} appended to a copy of the tools list
            when web_search was truthy (preserves any existing function tools).

        When web_search is absent/falsy, returns a copy with the flag stripped but
        tools untouched — byte-identical payload for the non-web-search case.
        Non-destructive: the caller's dict is never mutated.
        """
        if WEB_SEARCH_FLAG not in payload:
            return payload
        # Build a clean copy without the raw flag
        outbound = {k: v for k, v in payload.items() if k != WEB_SEARCH_FLAG}
        if payload.get(WEB_SEARCH_FLAG):
            ws_tool = native_web_search_tool("openrouter")
            if ws_tool is not None:
                existing = list(outbound.get("tools", []))
                outbound["tools"] = [*existing, ws_tool]
        return outbound

    def _maybe_inject_usage_accounting(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Opt-in usage accounting (provider-cost-reconciliation §3).

        When the knob is OFF, returns the payload object UNCHANGED — byte-identical
        to the pre-task request. When ON, returns a shallow copy with
        `usage={"include": true}` added so OpenRouter returns its reported cost.
        Non-destructive: a caller-supplied `usage` key is preserved, never overwritten.
        """
        if not self._usage_accounting or "usage" in payload:
            return payload
        return {**payload, "usage": {"include": True}}

    def _auth_headers(self) -> dict[str, str]:
        """Build OpenRouter auth headers from the request-scoped credential contextvar.

        Raises ProviderKeyMissing when the contextvar is unset (None) or carries
        a non-Bearer credential — never emits an unauthenticated or empty request.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing("openrouter")
        return {"Authorization": f"Bearer {cred.secret.get_secret_value()}"}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward non-streaming request to OpenRouter via the unified retry seam.

        Returns (status_code, json_body).
        Upstream 4xx (!=429/408): pass-through after exactly 1 attempt.
        Upstream 5xx / 429 / 408 / connect error / pool timeout: retried up to
        _max_retries times with full-jitter backoff, bounded by _retry_deadline_s.
        Read/write timeout / network error: raise UpstreamUnavailableError (not retried).
        Circuit open: raise CircuitOpenError (re-raised from breaker.guard).

        With _max_retries=0 (default): exactly one attempt, no backoff, no sleep —
        byte-identical to v5 behavior. The retry policy lives in upstream_retry.py.
        """

        outbound = self._maybe_inject_usage_accounting(self._maybe_inject_web_search(payload))

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                "/chat/completions",
                json=outbound,
                headers=self._auth_headers(),
            )

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider="openrouter",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    async def embed(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming embeddings request to OpenRouter via the retry seam.

        POSTs `payload` UNMODIFIED to /embeddings — no _maybe_inject_web_search /
        _maybe_inject_usage_accounting (chat-only concerns; embeddings requests
        carry no tools/web_search fields). Same execute_with_retry seam, same
        breaker/auth/timeout contract as complete() (openrouter-embeddings-routing
        TASK.md §3): non-200 passed through as (status, body); network/timeout
        errors raise UpstreamUnavailableError; CircuitOpenError re-raised from
        breaker.guard().
        """

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                "/embeddings",
                json=payload,
                headers=self._auth_headers(),
            )

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider="openrouter",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    async def get_generation(self, generation_id: str) -> GenerationCost | None:
        """Fetch a past generation's authoritative cost + native token usage.

        GET /generation?id=... through the shared designed-for-failure seam
        (bounded retry on 5xx/429/408/connect+pool-timeout + circuit breaker).
        Returns a GenerationCost on a 200 that carries cost; None when the
        generation is not available (a non-200, or a 200 without total_cost —
        OpenRouter stats are eventually-consistent, so the recovery caller retries
        or defers to the sweep backstop). Raises UpstreamUnavailableError on an
        exhausted/terminal transport failure and CircuitOpenError when the breaker
        is open — same contract as complete().

        Read-side primitive for disconnect cost-recovery (v30 t6); complete() and
        stream() are untouched and the per-instance breaker state is shared.
        """

        async def _do_request() -> httpx.Response:
            return await self._client.get(
                "/generation",
                params={"id": generation_id},
                headers=self._auth_headers(),
            )

        def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
            try:
                body = resp.json()
            except ValueError:  # includes json.JSONDecodeError
                body = {}
            return resp.status_code, body if isinstance(body, dict) else {}

        status, body = await execute_with_retry(
            _do_request,
            _render,
            breaker=self._breaker,
            provider="openrouter",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )
        if status == 200:
            return _parse_generation(body)
        if status == 404:
            # Not ready / unknown id — an expected, retry-or-defer signal (eventual consistency).
            return None
        # Any other terminal non-200 (e.g. 401/403 auth failure) is PERMANENT: surface it so the
        # recovery caller hard-fails instead of re-polling a broken lookup forever as "not ready".
        raise UpstreamUnavailableError(f"get_generation failed: HTTP {status}")

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields raw SSE byte chunks.

        The circuit breaker is checked before the first byte is yielded.
        Raises CircuitOpenError immediately if the breaker is open.
        Zero retry machinery — stream() is unchanged by the retry-policy task.
        """
        self._breaker.guard()
        outbound = self._maybe_inject_usage_accounting(self._maybe_inject_web_search(payload))

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=outbound,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_STREAM_READ_TIMEOUT,
                        write=_NON_STREAM_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                ) as response:
                    if response.status_code >= 500:
                        self._breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {response.status_code} on stream"
                        )
                    self._breaker.record_success()
                    async for chunk in response.aiter_bytes():
                        yield chunk
            # RemoteProtocolError = graceful mid-stream peer-close (Finding C, v35):
            # a ProtocolError, not a NetworkError — map it like any upstream failure so
            # the use-case mid-stream catch can emit the terminal SSE error frame + [DONE].
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()
