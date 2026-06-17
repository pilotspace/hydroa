"""RED tests for openai-chat-complete task (TASK.md §4).

Every test here is expected to FAIL before BUILD — for the RIGHT reason:
OpenAIDirectProvider implements only the UpstreamProvider surface (post_json /
post_multipart / stream_bytes); it has NO `complete()` / `stream()`, which the chat
dispatch (ProviderAwareCompletionUpstream) calls. The live task-6 double-pass caught
this as a 500 (AttributeError) for provider="openai".

Run only this suite:
  cd apps/gateway && uv run pytest tests/openai_chat_dispatch/ -q --no-cov -p no:cacheprovider

OC1  complete posts /chat/completions with the per-tenant Bearer → (200, body)
OC2  complete passes a 4xx through without raising (breaker success)
OC3  complete raises UpstreamUnavailableError on 5xx (breaker error)
OC4  stream yields raw SSE bytes from /chat/completions with the Bearer
OC5  stream raises UpstreamUnavailableError on 5xx before yielding
OC6  fail-closed: unset contextvar → ProviderKeyMissing, NO HTTP call
OC7  dispatch routes provider="openai" → OpenAIDirectProvider.complete (not openrouter)
OC8  satisfies CompletionUpstream AND UpstreamProvider (zero regression)
OC9  production wiring is type-correct (no masking type-ignore on the dispatch call)
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream, UpstreamProvider
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider
from tests.provider_seam.conftest import (
    CHAT_PAYLOAD,
    CHAT_RESPONSE_BODY,
    FAKE_API_KEY,
    FAKE_OPENAI_BASE_URL,
    FakeCompletionUpstream,
    SequencedMockTransport,
    make_json_response,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class SpyBreaker:
    """Records guard/success/error calls so the breaker contract is observable."""

    def __init__(self) -> None:
        self.guards = 0
        self.successes = 0
        self.errors = 0

    def guard(self) -> None:
        self.guards += 1

    def record_success(self) -> None:
        self.successes += 1

    def on_upstream_error(self) -> None:
        self.errors += 1

    def is_open(self) -> bool:
        return False


class StaticResolver:
    """ProviderResolver that always resolves to a fixed provider name."""

    def __init__(self, provider: str) -> None:
        self._provider = provider

    async def provider_for(self, model: str) -> str:
        return self._provider


def _make_provider(
    transport: SequencedMockTransport, breaker: SpyBreaker
) -> OpenAIDirectProvider:
    """Build an OpenAIDirectProvider wired to a MockTransport (PS8 __new__ pattern)."""
    provider = OpenAIDirectProvider.__new__(OpenAIDirectProvider)
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url=FAKE_OPENAI_BASE_URL,
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0),
    )
    provider._breaker = breaker  # type: ignore[attr-defined,assignment]
    provider._metrics_registry = None  # type: ignore[attr-defined]
    return provider


def _sse_response(status: int, body: bytes) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": "text/event-stream"},
        content=body,
    )


# ===========================================================================
# OC1 — complete posts /chat/completions with the per-tenant Bearer → (200, body)
# ===========================================================================


async def test_oc1_complete_posts_chat_completions_with_bearer() -> None:
    transport = SequencedMockTransport([make_json_response(200, CHAT_RESPONSE_BODY)])
    breaker = SpyBreaker()
    provider = _make_provider(transport, breaker)

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        status, body = await provider.complete(CHAT_PAYLOAD)
    finally:
        reset_provider_credential(token)

    assert status == 200, f"expected 200; got {status}"
    assert "choices" in body, f"expected chat.completion body; got {body}"
    assert transport.call_count == 1
    req = transport.last_request
    assert req is not None
    assert str(req.url) == f"{FAKE_OPENAI_BASE_URL}/chat/completions", str(req.url)
    assert req.method == "POST"
    assert req.headers.get("authorization") == f"Bearer {FAKE_API_KEY}"
    assert breaker.successes == 1 and breaker.errors == 0
    # zero-regression: the UpstreamProvider surface is untouched
    assert hasattr(provider, "post_json") and hasattr(provider, "post_multipart")
    assert hasattr(provider, "stream_bytes")


# ===========================================================================
# OC2 — complete passes a 4xx through without raising (breaker success)
# ===========================================================================


async def test_oc2_complete_4xx_passthrough_no_raise() -> None:
    transport = SequencedMockTransport([make_json_response(400, {"error": "bad"})])
    breaker = SpyBreaker()
    provider = _make_provider(transport, breaker)

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        status, body = await provider.complete(CHAT_PAYLOAD)
    finally:
        reset_provider_credential(token)

    assert status == 400, f"4xx must pass through; got {status}"
    assert body == {"error": "bad"}
    assert breaker.errors == 0, "a 4xx is not an upstream-availability failure"
    assert breaker.successes == 1


# ===========================================================================
# OC3 — complete raises UpstreamUnavailableError on 5xx (breaker error)
# ===========================================================================


async def test_oc3_complete_5xx_raises_upstream_unavailable() -> None:
    transport = SequencedMockTransport([make_json_response(503, {"error": "down"})])
    breaker = SpyBreaker()
    provider = _make_provider(transport, breaker)

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        with pytest.raises(UpstreamUnavailableError):
            await provider.complete(CHAT_PAYLOAD)
    finally:
        reset_provider_credential(token)

    assert breaker.errors == 1 and breaker.successes == 0


# ===========================================================================
# OC4 — stream yields raw SSE bytes from /chat/completions with the Bearer
# ===========================================================================


async def test_oc4_stream_yields_sse_bytes_with_bearer() -> None:
    sse = b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\ndata: [DONE]\n\n"
    transport = SequencedMockTransport([_sse_response(200, sse)])
    breaker = SpyBreaker()
    provider = _make_provider(transport, breaker)

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        chunks = [chunk async for chunk in provider.stream(CHAT_PAYLOAD)]
    finally:
        reset_provider_credential(token)

    assert b"".join(chunks) == sse, "stream must pass upstream bytes through unchanged"
    req = transport.last_request
    assert req is not None
    assert str(req.url) == f"{FAKE_OPENAI_BASE_URL}/chat/completions"
    assert req.method == "POST"
    assert req.headers.get("authorization") == f"Bearer {FAKE_API_KEY}"
    assert breaker.successes == 1 and breaker.errors == 0


# ===========================================================================
# OC5 — stream raises UpstreamUnavailableError on 5xx before yielding
# ===========================================================================


async def test_oc5_stream_5xx_raises_before_yield() -> None:
    transport = SequencedMockTransport([_sse_response(503, b"nope")])
    breaker = SpyBreaker()
    provider = _make_provider(transport, breaker)

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        with pytest.raises(UpstreamUnavailableError):
            _ = [chunk async for chunk in provider.stream(CHAT_PAYLOAD)]
    finally:
        reset_provider_credential(token)

    assert breaker.errors == 1


# ===========================================================================
# OC6 — fail-closed: unset contextvar → ProviderKeyMissing, NO HTTP call
# ===========================================================================


async def test_oc6_complete_failclosed_unset_contextvar_no_http() -> None:
    transport = SequencedMockTransport([make_json_response(200, CHAT_RESPONSE_BODY)])
    provider = _make_provider(transport, SpyBreaker())

    with pytest.raises(ProviderKeyMissing) as exc_info:
        await provider.complete(CHAT_PAYLOAD)

    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert transport.call_count == 0, "fail-closed: no upstream request without a key"


async def test_oc6_stream_failclosed_unset_contextvar_no_http() -> None:
    transport = SequencedMockTransport([_sse_response(200, b"data: {}\n\n")])
    provider = _make_provider(transport, SpyBreaker())

    with pytest.raises(ProviderKeyMissing) as exc_info:
        _ = [chunk async for chunk in provider.stream(CHAT_PAYLOAD)]

    assert exc_info.value.code == "ERR_PROVIDER_KEY_MISSING"
    assert transport.call_count == 0


# ===========================================================================
# OC7 — dispatch routes provider="openai" → OpenAIDirectProvider.complete
# ===========================================================================


async def test_oc7_dispatch_routes_openai_to_direct_provider() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    transport = SequencedMockTransport([make_json_response(200, CHAT_RESPONSE_BODY)])
    real_openai = _make_provider(transport, SpyBreaker())
    fake_openrouter = FakeCompletionUpstream()

    dispatch = ProviderAwareCompletionUpstream(
        adapters={"openrouter": fake_openrouter, "openai": real_openai},
        resolver=StaticResolver("openai"),
    )

    token = set_provider_credential(BearerCredential(secret=FAKE_API_KEY))
    try:
        status, _body = await dispatch.complete(CHAT_PAYLOAD)
    finally:
        reset_provider_credential(token)

    assert status == 200
    assert transport.call_count == 1, "the OpenAI direct adapter must have been invoked"
    assert fake_openrouter.complete_calls == [], "openrouter must NOT be the fallback here"


# ===========================================================================
# OC8 — satisfies CompletionUpstream AND UpstreamProvider (zero regression)
# ===========================================================================


async def test_oc8_satisfies_completionupstream_and_upstreamprovider() -> None:
    provider = _make_provider(
        SequencedMockTransport([make_json_response(200, CHAT_RESPONSE_BODY)]), SpyBreaker()
    )

    assert isinstance(provider, CompletionUpstream), "must satisfy the chat Protocol"
    assert isinstance(provider, UpstreamProvider), "must still satisfy UpstreamProvider"
    assert inspect.iscoroutinefunction(provider.complete)
    assert callable(provider.stream)


# ===========================================================================
# OC9 — production wiring is type-correct (no masking type-ignore)
# ===========================================================================


def test_oc9_main_wiring_no_type_ignore_on_dispatch() -> None:
    main_src = Path(__file__).resolve().parents[2] / "src" / "gateway" / "main.py"
    text = main_src.read_text()
    assert '_chat_adapters["openai"] = _openai_direct' in text, "openai must be a chat adapter"

    # The ProviderAwareCompletionUpstream(adapters=…) wiring must no longer be masked
    # by a type: ignore[arg-type] — OpenAIDirectProvider now satisfies CompletionUpstream.
    idx = text.index("ProviderAwareCompletionUpstream(")
    window = text[idx : idx + 240]
    assert "type: ignore[arg-type]" not in window, (
        "remove the type-ignore masking the adapter-map type — it hid the missing complete()"
    )
