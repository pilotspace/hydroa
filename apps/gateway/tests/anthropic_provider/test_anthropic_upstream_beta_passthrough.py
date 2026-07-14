"""Red/green regression suite for audit-remediation package C1 (MED proxy
passthrough headers dropped): `anthropic-beta` / `anthropic-workspace-id` are
captured from the inbound /v1/messages request into the
`anthropic_passthrough_headers` ContextVar (messages_router.py's
`set_passthrough_headers` call), but `AnthropicCompletionUpstream._auth_headers()`
never read that ContextVar — the outbound Anthropic dial only ever sent
`x-api-key` / `anthropic-version` / `content-type`, silently dropping any client
`anthropic-beta` feature flag (e.g. extended-thinking / prompt-caching beta gates)
and any `anthropic-workspace-id`.

Mirrors the MockTransport pattern from `test_anthropic_provider.py`
(`_make_adapter_with_handler`) — no network, no real key. New file (does not
edit the FROZEN `test_anthropic_provider.py` suite).
"""

from __future__ import annotations

import httpx
import pytest

from gateway.proxy.domain.credential_context import reset_provider_credential, set_provider_credential
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.anthropic_passthrough_headers import (
    AnthropicPassthroughHeaders,
    reset_passthrough_headers,
    set_passthrough_headers,
)
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream

_TEST_ANTHROPIC_SECRET = "sk-ant-test"
_TEST_BEARER_CRED = BearerCredential(secret=_TEST_ANTHROPIC_SECRET)

_ANTHROPIC_200 = {
    "id": "msg_01ABC",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "Hello world"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

pytestmark = pytest.mark.asyncio


def _make_adapter_with_handler(handler: object) -> AnthropicCompletionUpstream:
    adapter = AnthropicCompletionUpstream(
        base_url="https://api.anthropic.com/v1",
        anthropic_version="2023-06-01",
        default_max_tokens=4096,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url="https://api.anthropic.com/v1",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def test_anthropic_beta_header_forwarded_to_upstream() -> None:
    """Client's `anthropic-beta` value (e.g. an extended-thinking beta gate) must
    reach the actual outbound Anthropic call, not be silently dropped."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _make_adapter_with_handler(handler)
    cred_token = set_provider_credential(_TEST_BEARER_CRED)
    set_passthrough_headers(
        AnthropicPassthroughHeaders(
            anthropic_beta=("interleaved-thinking-2025-05-14", "prompt-caching-2024-07-31"),
        )
    )
    try:
        status, _ = await adapter.complete(
            {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(cred_token)
        reset_passthrough_headers()

    assert status == 200
    assert seen.get("anthropic-beta") == "interleaved-thinking-2025-05-14,prompt-caching-2024-07-31"


async def test_anthropic_workspace_id_header_forwarded_to_upstream() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _make_adapter_with_handler(handler)
    cred_token = set_provider_credential(_TEST_BEARER_CRED)
    set_passthrough_headers(AnthropicPassthroughHeaders(anthropic_workspace_id="wrkspc_01ABC"))
    try:
        await adapter.complete({"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]})
    finally:
        reset_provider_credential(cred_token)
        reset_passthrough_headers()

    assert seen.get("anthropic-workspace-id") == "wrkspc_01ABC"


async def test_no_passthrough_headers_set_forwards_nothing_extra() -> None:
    """Design-for-failure: an unset ContextVar (the empty sentinel) must not crash
    and must not inject empty/garbage headers — byte-identical to today's request
    shape for requests that never went through /v1/messages's capture step."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json=_ANTHROPIC_200)

    adapter = _make_adapter_with_handler(handler)
    reset_passthrough_headers()
    cred_token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, _ = await adapter.complete(
            {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(cred_token)

    assert status == 200
    assert "anthropic-beta" not in seen
    assert "anthropic-workspace-id" not in seen
    assert seen.get("x-api-key") == _TEST_ANTHROPIC_SECRET
    assert seen.get("anthropic-version") == "2023-06-01"
