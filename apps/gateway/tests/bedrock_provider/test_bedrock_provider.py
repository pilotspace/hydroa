"""Red suite for bedrock-provider (v20 task 2/4) — frozen contract.

Tests the AWS Bedrock Converse API chat adapter: OpenAI <-> Bedrock translation
(request mapping, response mapping, SigV4 signing, error passthrough, protocol
compliance) + composition-root wiring. Pure translation helpers are unit-tested
directly; the adapter's HTTP behavior uses an httpx.MockTransport (no network,
no real AWS credentials).

Contract: frozen at v20 task 2 (bedrock-upstream).
  - BedrockCompletionUpstream implements CompletionUpstream Protocol.
  - complete(): 200 -> OpenAI chat.completion; 4xx -> OpenAI error body (passthrough);
    5xx/429-exhausted -> UpstreamUnavailableError.
  - stream(): raises NotImplementedError (implemented in v20 task 3).
  - Signs every request with AWS SigV4 (bedrock_sigv4.sign_request).
  - Wired in main.py create_app() UNCONDITIONALLY (task-3: no env-cred guard).

v25 task-3 amendment: _make_adapter now sets the credential via the contextvar
(not ctor args). BC8 wiring tests updated to assert unconditional presence.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import quote, unquote

import httpx
import pytest

# ── Existing stable imports (already implemented) ────────────────────────────
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream
from gateway.proxy.domain.provider_credentials import BedrockCredential
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials

# RED until BUILD creates the module/symbols.
from gateway.proxy.infrastructure.bedrock_upstream import (  # noqa: E402
    BedrockCompletionUpstream,
    _bedrock_error_to_openai,
    _converse_to_openai,
    _map_finish_reason,
    _openai_to_converse_request,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

# v25 task-3: credentials travel via BedrockCredential in the contextvar, NOT ctor args.
# _DUMMY_CRED is a BedrockCredential; _DUMMY_CREDS (AwsCredentials) kept only for
# translation-helper tests (BC1-BC5) which call private helpers directly.
_DUMMY_CREDS = AwsCredentials(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

_DUMMY_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"

# Minimal valid Bedrock Converse 200 response body.
_CONVERSE_200 = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"text": "Hello"},
                {"text": " world"},
            ],
        }
    },
    "stopReason": "end_turn",
    "usage": {
        "inputTokens": 11,
        "outputTokens": 5,
        "totalTokens": 16,
    },
}

# Minimal valid Bedrock Converse 400 error body.
_CONVERSE_400 = {
    "message": "The request is invalid",
    "__type": "ValidationException",
}

# Minimal valid Bedrock Converse 503 error body.
_CONVERSE_503 = {
    "message": "Service unavailable",
    "__type": "ServiceUnavailableException",
}


def _make_adapter(
    handler: object,
    *,
    cred: BedrockCredential = _DUMMY_CRED,
    endpoint_url: str = "https://bedrock-runtime.us-east-1.amazonaws.com",
    max_retries: int = 0,
) -> BedrockCompletionUpstream:
    """Construct the adapter (no ctor creds/region — task-3), swap _client, set contextvar.

    v25 task-3 amendment: credentials travel via the contextvar (BedrockCredential),
    NOT as ctor arguments. The caller is responsible for setting and resetting the
    contextvar around the actual adapter.complete() / adapter.stream() call.

    This factory injects the cred into the contextvar for the duration of the test;
    tests that need fine-grained contextvar control set it themselves.
    """
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=endpoint_url,
        default_max_tokens=4096,
        max_retries=max_retries,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# BC1 — Request mapping: system lift + messages + inferenceConfig
# ---------------------------------------------------------------------------


def test_request_mapping_system_lift() -> None:
    """_openai_to_converse_request lifts role:'system' to top-level system[],
    maps user messages into Bedrock's messages format, and builds inferenceConfig
    with maxTokens (default applied when absent), temperature, topP, and
    stopSequences (stop: str -> list; list -> as-is)."""
    payload = {
        "model": _MODEL_ID,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi!"},
        ],
        "temperature": 0.8,
        "top_p": 0.95,
        "stop": ["END", "STOP"],
        # max_tokens deliberately absent — should default to default_max_tokens
    }
    model_id, body = _openai_to_converse_request(payload, default_max_tokens=4096)

    # model_id round-trip
    assert model_id == _MODEL_ID

    # System messages lifted to top-level key
    assert "system" in body
    assert body["system"] == [{"text": "You are a helpful assistant"}]

    # Non-system messages mapped to Bedrock messages format
    assert body["messages"] == [
        {"role": "user", "content": [{"text": "Hello there"}]},
        {"role": "assistant", "content": [{"text": "Hi!"}]},
    ]

    # inferenceConfig built with defaults applied
    ic = body["inferenceConfig"]
    assert ic["maxTokens"] == 4096  # default applied
    assert ic["temperature"] == 0.8
    assert ic.get("topP") == 0.95  # top_p -> topP
    assert ic["stopSequences"] == ["END", "STOP"]  # list -> as-is


def test_request_mapping_stop_str_to_list() -> None:
    """stop: str is promoted to a single-element list."""
    _, body = _openai_to_converse_request(
        {
            "model": _MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
            "stop": "HALT",
        },
        default_max_tokens=4096,
    )
    assert body["inferenceConfig"]["stopSequences"] == ["HALT"]


def test_request_mapping_no_system_omits_key() -> None:
    """When no role:'system' message is present, the 'system' key is omitted."""
    _, body = _openai_to_converse_request(
        {
            "model": _MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
        },
        default_max_tokens=4096,
    )
    assert "system" not in body


def test_request_mapping_max_tokens_explicit() -> None:
    """Explicit max_tokens in payload overrides the default."""
    _, body = _openai_to_converse_request(
        {
            "model": _MODEL_ID,
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 128,
        },
        default_max_tokens=4096,
    )
    assert body["inferenceConfig"]["maxTokens"] == 128


def test_request_mapping_optional_keys_absent() -> None:
    """temperature / topP / stopSequences are omitted when not in payload."""
    _, body = _openai_to_converse_request(
        {
            "model": _MODEL_ID,
            "messages": [{"role": "user", "content": "x"}],
        },
        default_max_tokens=4096,
    )
    ic = body["inferenceConfig"]
    assert "temperature" not in ic
    assert "topP" not in ic
    assert "stopSequences" not in ic


# ---------------------------------------------------------------------------
# BC2 — complete() signs the request and hits the correct Converse path
# ---------------------------------------------------------------------------


async def test_complete_signs_converse_path() -> None:
    """With a MockTransport handler that captures the outgoing request:
    - the wire request-target routes to the EXACT model id (/model/<model_id>/converse);
      botocore sends the literal ':' in the path and AWS canonicalizes it to %3A — so a
      double-encoded path (…v2%253A0, which routes to the wrong model) must be rejected
    - the SigV4 canonical URI single-encodes ':' as %3A (never %253A)
    - 'Authorization' header starts with 'AWS4-HMAC-SHA256'
    - 'x-amz-date' and 'x-amz-content-sha256' headers are present
    Handler returns a minimal valid 200 Converse body.

    v25 task-3: credential supplied via contextvar, not ctor."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=_CONVERSE_200)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await adapter.complete(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    finally:
        reset_provider_credential(tok)

    assert status == 200, f"Expected 200, got {status}: {body}"

    req = captured["req"]

    # Assert on the WIRE request-target (raw_path = the bytes actually sent), decoded EXACTLY
    # ONCE. botocore sends the literal ':' here; AWS canonicalizes it to %3A. A double-encoded
    # path (…v2%253A0) decodes once to …v2%3A0 — the WRONG model — so this equality catches it.
    wire_target = req.url.raw_path.decode()
    assert "/model/" in wire_target, f"Path missing /model/: {wire_target}"
    assert wire_target.endswith("/converse"), f"Path must end with /converse: {wire_target}"
    assert unquote(wire_target) == f"/model/{_MODEL_ID}/converse", (
        f"Wire target must route to the exact model id; got {wire_target!r} "
        f"-> {unquote(wire_target)!r}"
    )
    # The SigV4 canonical URI single-encodes ':' as %3A (matches botocore); never %253A.
    canonical_uri = quote(unquote(wire_target), safe="/~")
    assert "%3A" in canonical_uri and "%253A" not in canonical_uri, (
        f"Canonical URI must single-encode ':' as %3A, got: {canonical_uri}"
    )

    # SigV4 headers must be present
    headers = dict(req.headers)
    auth = headers.get("authorization", "")
    assert auth.startswith("AWS4-HMAC-SHA256"), (
        f"Authorization must start with AWS4-HMAC-SHA256, got: {auth!r}"
    )
    assert "x-amz-date" in headers, "Missing x-amz-date header"
    assert "x-amz-content-sha256" in headers, "Missing x-amz-content-sha256 header"


# ---------------------------------------------------------------------------
# BC3 — Response mapping: _converse_to_openai
# ---------------------------------------------------------------------------


def test_response_mapping() -> None:
    """_converse_to_openai on a 200 Converse body:
    - concatenates all content[].text fields
    - maps stopReason 'end_turn' -> finish_reason 'stop'
    - maps usage correctly to prompt/completion/total_tokens
    - sets object, choices[0].message.role, index
    """
    result = _converse_to_openai(_CONVERSE_200, model_id=_MODEL_ID)

    assert result["object"] == "chat.completion"
    assert result["model"] == _MODEL_ID

    choice = result["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    # Multiple text blocks concatenated
    assert choice["message"]["content"] == "Hello world"
    assert choice["finish_reason"] == "stop"

    usage = result["usage"]
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 16


def test_response_mapping_id_propagated() -> None:
    """Response id from Bedrock is propagated (or defaults to '')."""
    body_with_id = dict(_CONVERSE_200, ResponseMetadata={"RequestId": "req-abc-123"})
    # The id field in Bedrock's response is optional; adapter uses resp.get("id", "")
    result_no_id = _converse_to_openai(_CONVERSE_200, model_id=_MODEL_ID)
    assert "id" in result_no_id  # key must exist
    assert isinstance(result_no_id["id"], str)

    body_with_resp_id = dict(_CONVERSE_200, id="bdrk-msg-001")
    result_with_id = _converse_to_openai(body_with_resp_id, model_id=_MODEL_ID)
    assert result_with_id["id"] == "bdrk-msg-001"


def test_response_mapping_usage_totaltokens_fallback() -> None:
    """When totalTokens is absent, falls back to inputTokens + outputTokens."""
    body = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "x"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 7,
            "outputTokens": 3,
            # totalTokens deliberately absent
        },
    }
    result = _converse_to_openai(body, model_id=_MODEL_ID)
    assert result["usage"]["total_tokens"] == 10  # 7 + 3


# ---------------------------------------------------------------------------
# BC4 — Finish-reason mapping: _map_finish_reason
# ---------------------------------------------------------------------------


def test_finish_reason_map() -> None:
    """Each Bedrock stopReason maps to the correct OpenAI finish_reason."""
    assert _map_finish_reason("end_turn") == "stop"
    assert _map_finish_reason("max_tokens") == "length"
    assert _map_finish_reason("stop_sequence") == "stop"
    assert _map_finish_reason("tool_use") == "tool_calls"
    assert _map_finish_reason("content_filtered") == "content_filter"
    assert _map_finish_reason("guardrail_intervened") == "content_filter"
    # None and unknown values must both map to "stop"
    assert _map_finish_reason(None) == "stop"
    assert _map_finish_reason("something_new_and_unknown") == "stop"
    assert _map_finish_reason("") == "stop"


# ---------------------------------------------------------------------------
# BC5 — 4xx passthrough and 5xx raises UpstreamUnavailableError
# ---------------------------------------------------------------------------


async def test_4xx_passthrough_and_5xx_raises() -> None:
    """400 -> complete returns (400, body_with_error_key) without raising.
    503 -> complete raises UpstreamUnavailableError.

    v25 task-3: credential supplied via contextvar."""

    # ── 400 passthrough ──
    def handler_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=_CONVERSE_400)

    adapter_400 = _make_adapter(handler_400)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await adapter_400.complete(
            {"model": _MODEL_ID, "messages": [{"role": "user", "content": "x"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 400
    # Must wrap in OpenAI error envelope
    assert "error" in body
    assert isinstance(body["error"], dict)

    # ── 503 raises ──
    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=_CONVERSE_503)

    adapter_503 = _make_adapter(handler_503)
    tok2 = set_provider_credential(_DUMMY_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter_503.complete(
                {"model": _MODEL_ID, "messages": [{"role": "user", "content": "x"}]}
            )
    finally:
        reset_provider_credential(tok2)


async def test_422_passthrough() -> None:
    """Any 4xx response is passed through as (status, error_body), not raised.

    v25 task-3: credential supplied via contextvar."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"message": "Unprocessable", "__type": "ValidationException"}
        )

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await adapter.complete(
            {"model": _MODEL_ID, "messages": [{"role": "user", "content": "x"}]}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 422
    assert "error" in body


# ---------------------------------------------------------------------------
# BC6 — Session token included when AwsCredentials has session_token
# ---------------------------------------------------------------------------


async def test_session_token_signed() -> None:
    """When BedrockCredential includes a session_token, the outgoing request
    must carry an 'x-amz-security-token' header with that value.

    v25 task-3: session_token travels via BedrockCredential in the contextvar."""
    cred_with_token = BedrockCredential(
        access_key_id="AKIDTEST000000000000",
        secret_access_key="fakesecretkey0000000000000000000000000000",
        region="us-east-1",
        session_token="FakeSessionToken12345",
    )
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=_CONVERSE_200)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(cred_with_token)
    try:
        await adapter.complete(
            {"model": _MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}
        )
    finally:
        reset_provider_credential(tok)

    headers = dict(captured["req"].headers)
    assert "x-amz-security-token" in headers, (
        "x-amz-security-token header must be present when session_token is set in BedrockCredential"
    )
    assert headers["x-amz-security-token"] == "FakeSessionToken12345"


# ---------------------------------------------------------------------------
# BC7 — Protocol compliance and stream stub raises NotImplementedError
# ---------------------------------------------------------------------------


async def test_protocol_and_stream_stub() -> None:
    """isinstance(adapter, CompletionUpstream) must be True.

    The NotImplementedError stub was superseded by v20 task 3 which implements
    stream() for real.  This test retains only the Protocol structural check;
    the full streaming contract lives in tests/bedrock_streaming/.

    v25 task-3: adapter constructed without ctor creds/region.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CONVERSE_200)

    adapter = _make_adapter(handler)

    # Protocol structural check
    assert isinstance(adapter, CompletionUpstream), (
        "BedrockCompletionUpstream must satisfy the CompletionUpstream Protocol"
    )


# ---------------------------------------------------------------------------
# BC8 — Composition-root wiring: create_app() includes bedrock UNCONDITIONALLY
# ---------------------------------------------------------------------------
# v25 task-3 amendment: bedrock is wired unconditionally — no env-cred guard.
# The three old tests (present-when-creds-set, absent-when-creds-unset,
# absent-when-only-key-id-set) are REPLACED by a single unconditional test.
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: object) -> object:  # type: ignore[no-untyped-def]
    """Build a minimal Settings instance. Mirrors the anthropic wiring test helper."""
    from gateway.core.config import Settings

    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_wiring_bedrock_present_unconditionally() -> None:
    """create_app() with NO Bedrock env creds -> 'bedrock' still in app.state.chat_adapters.

    v25 task-3: bedrock is registered unconditionally (env-guard removed).
    The adapter reads its credentials per-request from the contextvar.

    RIGHT-REASON RED: current wiring gates 'bedrock' on resolve_aws_credentials(settings)
    being truthy — absent without env creds → assertion fails.
    """
    from gateway.main import create_app

    # No bedrock env creds at all
    settings = _make_settings()
    app = create_app(settings)  # type: ignore[arg-type]
    adapters = app.state.chat_adapters

    assert "bedrock" in adapters, (
        "'bedrock' must be in chat_adapters UNCONDITIONALLY after task-3 BUILD. "
        "It is absent — the env-cred guard is still in place (pre-BUILD state)."
    )
    assert isinstance(adapters["bedrock"], BedrockCompletionUpstream), (
        f"adapters['bedrock'] must be a BedrockCompletionUpstream instance, "
        f"got {type(adapters.get('bedrock'))!r}"
    )
    # openrouter fallback must still be present
    assert "openrouter" in adapters


# ---------------------------------------------------------------------------
# Additional: _bedrock_error_to_openai helper
# ---------------------------------------------------------------------------


def test_bedrock_error_to_openai() -> None:
    """_bedrock_error_to_openai wraps a Bedrock error body into OpenAI envelope."""
    result = _bedrock_error_to_openai(
        {"message": "Request too large", "__type": "ValidationException"},
        status=400,
    )
    assert "error" in result
    err = result["error"]
    assert isinstance(err.get("message"), str)
    assert isinstance(err.get("type"), str)
    # code field must be present
    assert "code" in err


def test_bedrock_error_to_openai_5xx() -> None:
    """_bedrock_error_to_openai also handles 5xx bodies."""
    result = _bedrock_error_to_openai(
        {"message": "Internal failure", "__type": "InternalServerException"},
        status=500,
    )
    assert "error" in result
    assert isinstance(result["error"]["message"], str)
