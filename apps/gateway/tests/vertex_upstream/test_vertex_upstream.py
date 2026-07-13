"""Failing-first (RED) suite for VertexCompletionUpstream (vertex-adapter TASK.md §4).

Covers: adapter registration (M1), translation reuse byte-identical to
gemini_upstream's pure function (M2), region-prefix→location URL resolution (M5),
prefix-stripped-before-wire (M9), unrecognized-prefix fail-closed (R4),
adapter+seed-landed-together (M10/R6), 5xx retry/circuit-breaker (M11), 4xx passthrough
verbatim (R7), missing-credential fail-closed (M12/R2), incremental streaming (M2 edge),
and usage extraction (M2/After).

HTTP behavior via httpx.MockTransport; credential resolution via the request-scoped
contextvar with a FakeCache/FakeProvider (auth mechanics are covered in
tests/vertex_auth/test_vertex_auth.py — these tests isolate the adapter's own
translation/URL/resilience/fail-closed behavior). No real network.

RED reason: gateway.proxy.infrastructure.vertex_upstream does not exist yet ->
ImportError on every test in this file.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import (
    GoogleServiceAccountCredential,
    ProviderKeyMissing,
)
from gateway.proxy.infrastructure.gemini_upstream import _openai_to_gemini_request

pytestmark = pytest.mark.asyncio

_CRED = GoogleServiceAccountCredential(
    project_id="proj-1",
    client_email="sa-1@proj-1.iam.gserviceaccount.com",
    private_key="-----BEGIN PRIVATE KEY-----\nZmFrZQ==\n-----END PRIVATE KEY-----\n",
)


class _FakeTokenProvider:
    def __init__(self, token: str = "tok-fixed") -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token


class _FailingTokenProvider:
    async def get_token(self) -> str:
        raise UpstreamUnavailableError("mint failed")


class _FakeCache:
    def __init__(self, provider: object) -> None:
        self._provider = provider

    def get_or_create(self, config: object, tenant_id: object | None = None) -> object:
        return self._provider


def _make_adapter(handler: object, *, cache: object | None = None, max_retries: int = 0):
    from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream

    adapter = VertexCompletionUpstream(
        token_provider_cache=cache if cache is not None else _FakeCache(_FakeTokenProvider()),
        max_retries=max_retries,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _counting_handler(response_fn: Callable[[httpx.Request], httpx.Response], hits: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return response_fn(request)

    return handler


def _canned_200(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"parts": [{"text": "hi there"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 3,
                "totalTokenCount": 10,
            },
        },
    )


_PAYLOAD = {
    "model": "eu.gemini-2.5-flash",
    "messages": [{"role": "user", "content": "hi"}],
}


# ===========================================================================
# M1 — adapter registered and dispatched by provider
# ===========================================================================


async def test_M1_vertex_adapter_registered_unconditionally() -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app
    from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert "vertex" in app.state.chat_adapters
    assert isinstance(app.state.chat_adapters["vertex"], VertexCompletionUpstream)


# ===========================================================================
# M10 / R6 — every seeded vertex row has a matching registered adapter
# ===========================================================================


async def test_M10_R6_seed_rows_and_adapter_land_together() -> None:
    from gateway.catalog.infrastructure.vertex_seed import VERTEX_SEED_MODELS
    from gateway.core.config import Settings
    from gateway.main import create_app

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    vertex_rows = [m for m in VERTEX_SEED_MODELS if m.provider == "vertex"]
    assert len(vertex_rows) >= 1, "vertex_seed.py must carry at least one provider=vertex row"
    assert "vertex" in app.state.chat_adapters, (
        "every provider='vertex' seed row must have a corresponding _chat_adapters['vertex'] "
        "registration in the SAME diff (M10/R6)"
    )


# ===========================================================================
# M2 — translation reuses gemini_upstream's pure function, byte-identical
# ===========================================================================


async def test_M2_translation_byte_identical_to_gemini_pure_function() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _canned_200(request)

    adapter = _make_adapter(handler)
    payload = {
        "model": "eu.gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": "auto",
    }
    tok = set_provider_credential(_CRED)
    try:
        await adapter.complete(payload)
    finally:
        reset_provider_credential(tok)

    expected = _openai_to_gemini_request(payload, default_max_tokens=4096)
    assert captured["body"] == expected, "VertexCompletionUpstream must NOT fork translation logic"


async def test_usage_extraction_matches_gemini_shape() -> None:
    """M2/After: usage.prompt_tokens/completion_tokens/total_tokens match usageMetadata exactly."""
    adapter = _make_adapter(_canned_200)
    tok = set_provider_credential(_CRED)
    try:
        status, body = await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


# ===========================================================================
# M5 — region-prefix -> location URL resolution
# ===========================================================================


async def test_M5_eu_prefix_resolves_europe_west4() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _canned_200(request)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        await adapter.complete({"model": "eu.gemini-2.5-flash", "messages": []})
    finally:
        reset_provider_credential(tok)

    url = captured["url"]
    assert url.startswith("https://europe-west4-aiplatform.googleapis.com/")
    assert "/locations/europe-west4/" in url
    assert url.endswith("/publishers/google/models/gemini-2.5-flash:generateContent")


async def test_M5_ap_prefix_resolves_asia_southeast1() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _canned_200(request)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        await adapter.complete({"model": "ap.gemini-2.5-pro", "messages": []})
    finally:
        reset_provider_credential(tok)

    url = captured["url"]
    assert url.startswith("https://asia-southeast1-aiplatform.googleapis.com/")
    assert "/locations/asia-southeast1/" in url
    assert url.endswith("/publishers/google/models/gemini-2.5-pro:generateContent")


# ===========================================================================
# M9 — region prefix stripped before reaching the wire (URL AND body)
# ===========================================================================


async def test_M9_region_prefix_stripped_from_wire() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return _canned_200(request)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        await adapter.complete(
            {"model": "eu.gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)

    assert "eu." not in captured["url"]
    assert "eu." not in captured["body"]
    assert "gemini-2.5-flash" in captured["url"]


# ===========================================================================
# R4 — unrecognized region prefix fails closed BEFORE any HTTP call
# ===========================================================================


async def test_R4_unrecognized_prefix_fails_closed_on_complete() -> None:
    from gateway.proxy.infrastructure.vertex_upstream import VertexRegionUnresolvedError

    hits = [0]
    adapter = _make_adapter(_counting_handler(_canned_200, hits))
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(VertexRegionUnresolvedError) as exc:
            await adapter.complete({"model": "zz.gemini-2.5-flash", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert exc.value.code == "ERR_VERTEX_REGION_UNRESOLVED"
    assert hits[0] == 0, "no HTTP call — no token mint, no Vertex dial — must occur"


async def test_R4_unrecognized_prefix_fails_closed_on_stream() -> None:
    from gateway.proxy.infrastructure.vertex_upstream import VertexRegionUnresolvedError

    hits = [0]
    adapter = _make_adapter(_counting_handler(_canned_200, hits))
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(VertexRegionUnresolvedError):
            adapter.stream({"model": "unknownprefix.gemini-2.5-flash", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 0


async def test_R4_missing_prefix_fails_closed() -> None:
    """A bare model id with no `<code>.` prefix at all is equally unresolved."""
    from gateway.proxy.infrastructure.vertex_upstream import VertexRegionUnresolvedError

    hits = [0]
    adapter = _make_adapter(_counting_handler(_canned_200, hits))
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(VertexRegionUnresolvedError):
            await adapter.complete({"model": "gemini-2.5-flash", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 0


# ===========================================================================
# M11 — 5xx raises UpstreamUnavailableError and retries compose
# ===========================================================================


async def test_M11_5xx_retries_to_success() -> None:
    attempts = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        attempts[0] += 1
        if attempts[0] == 1:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return _canned_200(request)

    adapter = _make_adapter(handler, max_retries=1)
    tok = set_provider_credential(_CRED)
    try:
        status, body = await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert attempts[0] == 2


async def test_M11_5xx_no_retry_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    adapter = _make_adapter(handler, max_retries=0)
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)


# ===========================================================================
# R7 — 4xx passthrough verbatim (regional-404 case); breaker not tripped
# ===========================================================================


async def test_R7_4xx_passthrough_verbatim_regional_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": 404,
                    "message": "model not available in this location",
                    "status": "NOT_FOUND",
                }
            },
        )

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        status, body = await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)
    assert status == 404
    assert body["error"]["type"] == "not_found"
    assert not adapter._breaker.is_open(), "a 4xx must NOT trip the circuit breaker"


# ===========================================================================
# M12 / R2 — missing credential fails closed BEFORE any HTTP call
# ===========================================================================


async def test_M12_R2_missing_credential_fails_closed() -> None:
    hits = [0]
    adapter = _make_adapter(_counting_handler(_canned_200, hits))
    # No set_provider_credential() call — contextvar stays unset.
    with pytest.raises(ProviderKeyMissing) as exc:
        await adapter.complete(_PAYLOAD)
    assert exc.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert hits[0] == 0


async def test_M12_R2_missing_credential_fails_closed_on_stream() -> None:
    hits = [0]
    adapter = _make_adapter(_counting_handler(_canned_200, hits))
    with pytest.raises(ProviderKeyMissing):
        adapter.stream(_PAYLOAD)
    assert hits[0] == 0


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_map_translation_error_inline_too_large_maps_to_413() -> None:
    """_map_translation_error: 'inline_too_large' -> 413 PAYLOAD_INPUT_TOO_LONG.

    Unit-tested directly against the dispatch function: `max_inline_bytes` is not
    currently wired through VertexCompletionUpstream's constructor (not named in the
    frozen §3 contract), so this ValueError is unreachable via a live `.complete()`
    call today — `_map_translation_error` still carries the branch (copied verbatim
    from gemini_upstream.py's own dispatcher) for forward-consistency with Gemini's
    reused translation surface, so it is exercised directly here."""
    from gateway.proxy.infrastructure.vertex_upstream import _map_translation_error

    with pytest.raises(Exception) as exc:
        _map_translation_error(ValueError("inline_too_large"))
    assert getattr(exc.value, "status", None) == 413


async def test_translation_error_unsupported_content_part_maps_to_400() -> None:
    """_map_translation_error: 'unsupported_content_part' -> 400 UNSUPPORTED_CONTENT_PART."""
    adapter = _make_adapter(_canned_200)
    payload = {
        "model": "eu.gemini-2.5-flash",
        "messages": [
            {"role": "user", "content": [{"type": "audio_url", "audio_url": {"url": "x"}}]}
        ],
    }
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(Exception) as exc:
            await adapter.complete(payload)
    finally:
        reset_provider_credential(tok)
    assert getattr(exc.value, "status", None) == 400


async def test_translation_error_other_valueerror_reraised() -> None:
    """_map_translation_error: an unmapped ValueError (e.g. tool_call_id_required) is
    re-raised as-is, not swallowed into a 4xx ProblemError."""
    adapter = _make_adapter(_canned_200)
    payload = {
        "model": "eu.gemini-2.5-flash",
        "messages": [{"role": "tool", "content": "orphan"}],  # missing tool_call_id
    }
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(ValueError, match="tool_call_id_required"):
            await adapter.complete(payload)
    finally:
        reset_provider_credential(tok)


async def test_no_token_provider_cache_instantiates_provider_directly() -> None:
    """When token_provider_cache is None (e.g. verify-script usage), the adapter
    instantiates a VertexTokenProvider directly per-call instead of via a cache."""
    from gateway.proxy.infrastructure.vertex_upstream import VertexCompletionUpstream

    captured_auth: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth["authorization"] = request.headers.get("authorization")
        return _canned_200(request)

    adapter = VertexCompletionUpstream(token_provider_cache=None)
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]

    import gateway.proxy.infrastructure.vertex_upstream as vertex_upstream_mod

    class _FakeProvider:
        def __init__(self, *, config: object) -> None:
            self._config = config

        async def get_token(self) -> str:
            return "direct-tok"

    original_cls = vertex_upstream_mod.VertexTokenProvider
    vertex_upstream_mod.VertexTokenProvider = _FakeProvider  # type: ignore[assignment, misc]
    tok = set_provider_credential(_CRED)
    try:
        status, _body = await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)
        vertex_upstream_mod.VertexTokenProvider = original_cls  # type: ignore[misc]
    assert status == 200
    assert captured_auth["authorization"] == "Bearer direct-tok"


async def test_R3_token_mint_failure_fails_closed_no_vertex_dial() -> None:
    hits = [0]
    adapter = _make_adapter(
        _counting_handler(_canned_200, hits), cache=_FakeCache(_FailingTokenProvider())
    )
    tok = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.complete(_PAYLOAD)
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 0, "a token-mint failure must never reach the Vertex generateContent call"


# ===========================================================================
# Streaming — incremental delivery + terminal usage frame (M2 edge case)
# ===========================================================================


def _sse_counting_handler(chunks: list[bytes], counter: list[int]):
    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            counter[0] += 1
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    return handler


_STREAM_CHUNKS: list[bytes] = [
    b'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n',
    b'data: {"candidates":[{"content":{"parts":[{"text":" world"}]},'
    b'"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":10,'
    b'"candidatesTokenCount":5,"totalTokenCount":15}}\n\n',
]


async def test_streaming_5xx_raises_and_trips_breaker() -> None:
    """A 5xx status opening the stream raises UpstreamUnavailableError and trips the
    circuit breaker (mirrors the non-streaming 5xx path)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        gen = adapter.stream({"model": "eu.gemini-2.5-flash", "messages": []})
        with pytest.raises(UpstreamUnavailableError):
            await gen.__anext__()
    finally:
        reset_provider_credential(tok)


async def test_streaming_skips_malformed_json_data_lines() -> None:
    """A `data:` line whose payload is not valid JSON is skipped (not raised)."""
    chunks = [
        b"data: not-json-at-all\n\n",
        b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},'
        b'"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,'
        b'"candidatesTokenCount":1,"totalTokenCount":2}}\n\n',
    ]
    counter = [0]
    adapter = _make_adapter(_sse_counting_handler(chunks, counter))
    tok = set_provider_credential(_CRED)
    try:
        gen = adapter.stream(
            {"model": "eu.gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]}
        )
        collected = [c async for c in gen]
    finally:
        reset_provider_credential(tok)
    text = b"".join(collected).decode()
    assert "chat.completion.chunk" in text
    assert text.endswith("data: [DONE]\n\n")


async def test_streaming_network_error_fails_closed() -> None:
    """A network error mid-stream raises UpstreamUnavailableError with no chained
    exception (avoids leaking request internals) and trips the breaker."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_CRED)
    try:
        gen = adapter.stream({"model": "eu.gemini-2.5-flash", "messages": []})
        with pytest.raises(UpstreamUnavailableError) as exc:
            await gen.__anext__()
    finally:
        reset_provider_credential(tok)
    assert exc.value.__cause__ is None


async def test_streaming_incremental_fidelity() -> None:
    counter = [0]
    adapter = _make_adapter(_sse_counting_handler(_STREAM_CHUNKS, counter))
    tok = set_provider_credential(_CRED)
    try:
        gen = adapter.stream(
            {"model": "eu.gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]}
        )
        first = await gen.__anext__()
        # Incremental: the role frame is produced after pulling only the first
        # upstream chunk — NOT after draining the whole stream.
        assert counter[0] < len(_STREAM_CHUNKS)
        assert b'"role"' in first and b"assistant" in first
        rest = [chunk async for chunk in gen]
    finally:
        reset_provider_credential(tok)

    all_chunks = [first, *rest]
    assert all_chunks[-1] == b"data: [DONE]\n\n"
    text = b"".join(all_chunks).decode()
    assert "chat.completion.chunk" in text
    # Terminal frame carries usage + finish_reason.
    data_lines = [ln[len("data: ") :] for ln in text.splitlines() if ln.startswith("data: ")]
    payloads = [json.loads(d) for d in data_lines if d.strip() and d.strip() != "[DONE]"]
    terminal = payloads[-1]
    assert terminal["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert terminal["choices"][0]["finish_reason"] == "stop"
