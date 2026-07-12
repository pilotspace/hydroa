"""Infrastructure adapter: VertexCompletionUpstream (vertex-adapter TASK.md §3 FROZEN @ v1).

Vertex AI's Gemini-publisher wire body is the SAME `generateContent` /
`streamGenerateContent` JSON shape as the public Gemini API, so this adapter REUSES
``gemini_upstream.py``'s pure OpenAI⇄Gemini translation functions UNCHANGED (M2) — no
forked/duplicated translation logic. Only auth and URL construction are Vertex-specific:

  1. Auth is a GCP JWT-bearer OAuth2 flow (RFC 7523) via VertexTokenProviderCache
     (mints/caches a short-lived Bearer token — see vertex_ad.py).
  2. The request URL is resolved PER-REQUEST from the catalog model id's synthetic
     `<region-code>.` prefix (`eu.`/`ap.`) via a FIXED internal map
     (_ID_PREFIX_TO_LOCATION) — NEVER from tenant input, NEVER an arbitrary string.
     An unrecognized/missing prefix FAILS CLOSED before any HTTP call (R4/M9) — the
     single worst failure mode this residency feature could have is silently
     defaulting to the wrong GCP region.

Resilience mirrors every sibling adapter: execute_with_retry + per-instance
CircuitBreaker, provider="vertex" metric/error label, 4xx passthrough verbatim (never
raised), 5xx/transport -> UpstreamUnavailableError.

Security: ``private_key`` never reaches this module — only the resolved Bearer token
(a short-lived, non-reusable-beyond-TTL credential) is placed in the Authorization
header. Never logged, echoed, or placed in a metric label / span attribute / exception
message / URL query param.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.core.error_catalog import PAYLOAD_INPUT_TOO_LONG, UNSUPPORTED_CONTENT_PART
from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import (
    GoogleServiceAccountCredential,
    ProviderKeyMissing,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.gemini_upstream import (
    _gemini_error_to_openai,
    _gemini_to_openai,
    _GeminiSSEStepper,
    _openai_to_gemini_request,
)
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry
from gateway.proxy.infrastructure.vertex_ad import VertexTokenProvider, VertexTokenProviderCache

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

#: Fixed, gateway-internal map from the catalog id's synthetic `<region-code>.` prefix
#: to the literal GCP Vertex AI location segment (§1 M5). NEVER derived from tenant or
#: catalog-admin input — extending this map is the ONLY sanctioned way to add a Vertex
#: region; a request whose model id carries an unlisted/missing prefix fails closed
#: (R4) rather than silently defaulting to a location.
_ID_PREFIX_TO_LOCATION: dict[str, str] = {
    "eu": "europe-west4",
    "ap": "asia-southeast1",
}


class VertexRegionUnresolvedError(Exception):
    """Raised when a catalog model id carries no recognized `<region-code>.` prefix.

    §1 R4 (vertex-adapter TASK.md, DECIDED at freeze) — ``ERR_VERTEX_REGION_UNRESOLVED``.
    This is meant to be UNREACHABLE in a correctly-seeded catalog (every
    ``VERTEX_SEED_MODELS`` row's id is prefixed by construction — M10/R6 guards that a
    seed row never ships without a matching adapter, and this guards the inverse: the
    adapter never silently guesses a location for an id it doesn't recognize). Raised
    BEFORE any HTTP call — no token mint, no Vertex dial — fail-closed, never a default
    location (a residency feature routing to the wrong GCP region is the worst failure
    mode this task could ship).
    """

    code: str = "ERR_VERTEX_REGION_UNRESOLVED"

    def __init__(self, model: str) -> None:
        super().__init__(f"Unresolved Vertex region prefix for model id: {model!r}")
        self.code = "ERR_VERTEX_REGION_UNRESOLVED"


def _parse_vertex_model(model: str) -> tuple[str, str]:
    """Split a synthetic `<region-code>.<bare-model>` catalog id into (location, bare_model).

    The `<region-code>.` prefix (`eu.`/`ap.`) is GATEWAY-INVENTED — it does NOT exist
    upstream at Vertex (unlike Bedrock's real AWS cross-region-profile ids, §0 Issue #2)
    — and is STRIPPED here before the bare model name ever reaches the Vertex URL/body.

    Raises:
        VertexRegionUnresolvedError: the prefix is missing, empty, or not a key of
            _ID_PREFIX_TO_LOCATION (fail-closed — R4/M9).
    """
    prefix, sep, bare_model = model.partition(".")
    if not sep or not bare_model or prefix not in _ID_PREFIX_TO_LOCATION:
        raise VertexRegionUnresolvedError(model)
    return _ID_PREFIX_TO_LOCATION[prefix], bare_model


def _build_url(location: str, project_id: str, bare_model: str, *, stream: bool) -> str:
    """Build the Vertex generateContent / streamGenerateContent URL (§1 M5).

    `location` and `bare_model` are ALWAYS derived from the fixed internal map + the
    stripped catalog id (never tenant input); `project_id` comes from the resolved
    GoogleServiceAccountCredential.
    """
    action = "streamGenerateContent" if stream else "generateContent"
    return (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/publishers/google/models/{bare_model}:{action}"
    )


def _map_translation_error(exc: ValueError) -> None:
    """Convert a translation-layer ValueError into the appropriate ProblemError.

    Mirrors GeminiCompletionUpstream._map_translation_error exactly (M2: the Gemini
    wire shape — and therefore its translation failure modes — is reused unchanged;
    this tiny dispatch is adapter boilerplate, not a fork of the translation itself).
    """
    msg = str(exc)
    if msg == "inline_too_large":
        raise PAYLOAD_INPUT_TOO_LONG.exc(
            detail="Inline data exceeds the per-request size limit"
        ) from exc
    if msg in (
        "only_data_url_supported",
        "invalid_data_url_base64",
        "unsupported_content_part",
    ):
        raise UNSUPPORTED_CONTENT_PART.exc() from exc
    raise  # re-raise — e.g. tool_call_id_required, ERR_UNSUPPORTED_RESPONSE_FORMAT


class VertexCompletionUpstream:
    """Forwards chat completions to Google Vertex AI's Gemini-publisher API.

    Implements the CompletionUpstream Protocol (complete + stream). Translation REUSES
    gemini_upstream.py's pure functions UNCHANGED (M2); auth + URL construction are
    Vertex-specific (M3-M5, M9).

    A single instance is shared for the lifetime of the application. The circuit
    breaker state is per-instance (per-replica).

    Credentials are resolved per-request from the GoogleServiceAccountCredential
    contextvar (M12) — no boot-time credential, no module-level singleton token.

    SECURITY: private_key never reaches this class; only the resolved short-lived
    Bearer token is placed in the Authorization header. NEVER logged, echoed, or
    placed in a metric label / span attribute / exception message / URL query param.
    """

    def __init__(
        self,
        *,
        token_provider_cache: VertexTokenProviderCache | None = None,
        default_max_tokens: int = 4096,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._token_provider_cache = token_provider_cache
        self._default_max_tokens = default_max_tokens
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry

        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
            follow_redirects=False,
        )

    def _get_credential(self) -> GoogleServiceAccountCredential:
        """Read GoogleServiceAccountCredential from the request contextvar (fail-closed).

        Raises ProviderKeyMissing("vertex") when the contextvar is unset OR holds a
        wrong-type value — NEVER produces an unauthenticated upstream request (M12/R2).
        """
        cred = get_provider_credential()
        if cred is None or not isinstance(cred, GoogleServiceAccountCredential):
            raise ProviderKeyMissing("vertex")
        return cred

    async def _auth_headers_for_credential(
        self, cred: GoogleServiceAccountCredential
    ) -> dict[str, str]:
        """Build Bearer auth headers by minting/reusing a cached JWT-bearer token."""
        config = cred.to_vertex_service_account_config()
        tp: VertexTokenProvider
        if self._token_provider_cache is not None:
            tp = self._token_provider_cache.get_or_create(config)
        else:
            # No cache supplied (e.g. verify tests) — instantiate directly per-call.
            tp = VertexTokenProvider(config=config)
        token = await tp.get_token()
        return {"Authorization": f"Bearer {token}", "content-type": "application/json"}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the resolved Vertex location.

        Returns (status_code, openai_shaped_body).
        - 200  -> translated OpenAI chat.completion
        - 4xx (non-408) -> OpenAI error body (no exception; gateway forwards the status)
        - 5xx / 429 / 408 / connect error / pool timeout -> retried up to max_retries
        - read/write timeout / network error -> UpstreamUnavailableError (not retried)

        Fail-closed BEFORE any HTTP/IO, in order:
          1. model region-prefix resolution (R4/M9) — no token mint, no dial.
          2. credential resolution (M12/R2) — no unauthenticated request.
          3. request translation (ValueErrors mapped to 4xx ProblemError).
        """
        model: str = str(payload.get("model", ""))
        location, bare_model = _parse_vertex_model(model)
        cred = self._get_credential()
        try:
            gemini_body = _openai_to_gemini_request(
                payload, default_max_tokens=self._default_max_tokens
            )
        except ValueError as exc:
            _map_translation_error(exc)
            raise  # unreachable — _map_translation_error always raises

        url = _build_url(location, cred.project_id, bare_model, stream=False)
        headers = await self._auth_headers_for_credential(cred)

        async def _do_request() -> httpx.Response:
            return await self._client.post(url, json=gemini_body, headers=headers)

        def _render(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
            if resp.status_code >= 400:
                return resp.status_code, _gemini_error_to_openai(resp.json())
            return 200, _gemini_to_openai(resp.json(), model=model)

        return await execute_with_retry(
            _do_request,
            _render,
            breaker=self._breaker,
            provider="vertex",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields OpenAI SSE chunk bytes.

        The circuit breaker is checked before the stream opens. Vertex SSE data:
        frames are translated LIVE via a stateful _GeminiSSEStepper — each OpenAI
        chunk is yielded as its source frame arrives (incremental delivery); finish()
        emits the terminal usage frame + [DONE]. Mirrors GeminiCompletionUpstream.stream
        exactly, bar Vertex's own auth/URL.

        Fail-closed BEFORE the generator is even returned: model region-prefix
        resolution (R4/M9), credential resolution (M12/R2), and translation ValueErrors
        (mapped to 4xx ProblemError) all happen synchronously here, not inside _gen().
        """
        self._breaker.guard()

        model: str = str(payload.get("model", ""))
        location, bare_model = _parse_vertex_model(model)
        cred = self._get_credential()
        try:
            gemini_body = _openai_to_gemini_request(
                payload, default_max_tokens=self._default_max_tokens
            )
        except ValueError as exc:
            _map_translation_error(exc)
            raise  # unreachable

        url = _build_url(location, cred.project_id, bare_model, stream=True)

        async def _gen() -> AsyncIterator[bytes]:
            try:
                # The token fetch is async; a mint failure raises UpstreamUnavailableError
                # before the first byte, inside the generator (mirrors Azure's own pattern).
                headers = await self._auth_headers_for_credential(cred)
                async with self._client.stream(
                    "POST",
                    url,
                    params={"alt": "sse"},
                    json=gemini_body,
                    headers=headers,
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

                    stepper = _GeminiSSEStepper()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:") :].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            data_obj: dict[str, Any] = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        for frame in stepper.step(data_obj):
                            yield frame
                    for frame in stepper.finish():
                        yield frame

            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()


__all__ = [
    "_ID_PREFIX_TO_LOCATION",
    "VertexCompletionUpstream",
    "VertexRegionUnresolvedError",
    "_parse_vertex_model",
]
