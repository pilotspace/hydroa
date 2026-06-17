"""Red suite for bedrock-embeddings (v20 task 5) — frozen contract.

Tests the AWS Bedrock Titan embeddings provider: single-string and batch
invocations via the Titan /invoke endpoint (NOT /converse), SigV4 signing
with service="bedrock", error passthrough and fail-fast on 4xx, connect-error
mapping to UpstreamUnavailableError, dimensions passthrough, and
composition-root wiring in create_app().

All HTTP behavior uses httpx.MockTransport — no network, no real AWS
credentials.

Contract: frozen at v20 task 5 (bedrock-embeddings).
  - BedrockEmbeddingsProvider implements UpstreamProvider.post_json().
  - Single str input → one POST to /model/<model_id>/invoke, body {"inputText": <str>}.
  - list[str] input → N sequential POSTs (Titan has no batch endpoint).
  - Returns (200, OpenAI-list-shape) on success.
  - 4xx on any call → (status, {"error": {"message": ..., "type": "bedrock_error",
    "code": status}}); list input stops after first error (fail fast).
  - ConnectError → raises UpstreamUnavailableError.
  - payload "dimensions" key → forwarded to invoke body.
  - Wired in create_app() UNCONDITIONALLY (task-3: no env-cred guard).

v25 task-3 amendment: _make_provider now sets credential via contextvar;
BE7 wiring test updated to assert unconditional presence.
"""

from __future__ import annotations

import json
from urllib.parse import quote, unquote

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BedrockCredential

# RED until BUILD creates the module/class.
from gateway.proxy.infrastructure.bedrock_embeddings import BedrockEmbeddingsProvider  # noqa: E402

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

# v25 task-3: credentials travel via BedrockCredential in the contextvar.
_DUMMY_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

_MODEL_ID = "amazon.titan-embed-text-v1"
_MODEL_ID_V2 = "amazon.titan-embed-text-v2:0"

# Minimal Titan invoke 200 response shape.
_TITAN_200 = {"embedding": [0.1, 0.2, 0.3], "inputTextTokenCount": 8}


def _make_provider(
    handler: object,
    *,
    endpoint_url: str = "https://bedrock-runtime.us-east-1.amazonaws.com",
) -> BedrockEmbeddingsProvider:
    """Construct provider (no ctor creds/region — task-3) then swap its _client.

    v25 task-3: credentials travel via BedrockCredential in the contextvar.
    Callers must set/reset the contextvar around post_json() calls.
    """
    provider = BedrockEmbeddingsProvider(  # type: ignore[call-arg]
        endpoint_url=endpoint_url,
    )
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return provider


def _make_settings(**kwargs: object) -> object:
    """Build a minimal Settings instance. Mirrors bedrock_provider test helper."""
    from gateway.core.config import Settings

    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BE1 — Single string invoke: one POST, correct path, SigV4, OpenAI list shape
# ---------------------------------------------------------------------------


async def test_single_string_invoke() -> None:
    """Single str input → one POST to /model/<model_id>/invoke (NOT /converse).

    Asserts:
    - Exactly one outgoing request.
    - URL path ends with "/invoke"; "/converse" is absent.
    - Authorization header starts with "AWS4-HMAC-SHA256".
    - x-amz-date and x-amz-content-sha256 headers present.
    - Returns (200, OpenAI list shape) with correct usage and data fields.
    - model echoed back in response body.

    v25 task-3: credential supplied via contextvar.
    """
    call_count = 0
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        captured["req"] = request
        return httpx.Response(200, json=_TITAN_200)

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await provider.post_json(
            "/embeddings", {"model": _MODEL_ID, "input": "hello"}
        )
    finally:
        reset_provider_credential(tok)

    assert call_count == 1, f"Expected exactly 1 call, got {call_count}"
    assert status == 200, f"Expected 200, got {status}: {body}"

    req = captured["req"]

    # Path must end with /invoke (Titan uses /invoke, not /converse)
    raw_path = req.url.raw_path.decode()
    assert raw_path.endswith("/invoke"), f"Path must end with /invoke, got: {raw_path!r}"
    assert "/converse" not in raw_path, f"Path must NOT contain /converse, got: {raw_path!r}"

    # SigV4 headers
    headers = dict(req.headers)
    auth = headers.get("authorization", "")
    assert auth.startswith("AWS4-HMAC-SHA256"), (
        f"Authorization must start with AWS4-HMAC-SHA256, got: {auth!r}"
    )
    assert "x-amz-date" in headers, "Missing x-amz-date header"
    assert "x-amz-content-sha256" in headers, "Missing x-amz-content-sha256 header"

    # OpenAI list shape: object, data, model, usage
    assert body["object"] == "list", f"body['object'] must be 'list', got: {body.get('object')!r}"
    assert body["model"] == _MODEL_ID, f"model must be echoed, got: {body.get('model')!r}"

    data = body["data"]
    assert len(data) == 1, f"data must have 1 entry, got {len(data)}"
    assert data[0] == {
        "object": "embedding",
        "index": 0,
        "embedding": [0.1, 0.2, 0.3],
    }, f"data[0] shape mismatch: {data[0]}"

    usage = body["usage"]
    assert usage == {"prompt_tokens": 8, "total_tokens": 8}, f"usage mismatch: {usage}"


# ---------------------------------------------------------------------------
# BE2 — List input: N sequential invoke calls, indexed data, summed tokens
# ---------------------------------------------------------------------------


async def test_list_n_calls_summed() -> None:
    """list[str] input → N sequential invoke calls (Titan has no batch).

    Asserts:
    - call_count == len(input) (each item gets its own request).
    - data entries indexed 0, 1, 2 in order.
    - usage.total_tokens == sum of each call's inputTextTokenCount.

    v25 task-3: credential supplied via contextvar.
    """
    call_count = 0
    per_call_response = {"embedding": [0.5], "inputTextTokenCount": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=per_call_response)

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await provider.post_json(
            "/embeddings", {"model": _MODEL_ID, "input": ["a", "b", "c"]}
        )
    finally:
        reset_provider_credential(tok)

    assert call_count == 3, f"Expected 3 calls for 3 inputs, got {call_count}"
    assert status == 200, f"Expected 200, got {status}: {body}"

    data = body["data"]
    assert len(data) == 3, f"data must have 3 entries, got {len(data)}"

    for i, entry in enumerate(data):
        assert entry["index"] == i, f"data[{i}]['index'] must be {i}, got {entry['index']}"
        assert entry["object"] == "embedding"
        assert entry["embedding"] == [0.5]

    # total_tokens == sum of each call's inputTextTokenCount (3 * 3 = 9)
    assert body["usage"]["total_tokens"] == 9, (
        f"total_tokens must be 9 (3 calls x 3 tokens), got {body['usage']['total_tokens']}"
    )
    assert body["usage"]["prompt_tokens"] == 9


# ---------------------------------------------------------------------------
# BE3 — SigV4: service="bedrock", single-encoded %3A, /invoke not /converse
# ---------------------------------------------------------------------------


async def test_sigv4_service_bedrock() -> None:
    """Model with ':' in id (v2) → path single-encodes ':' as %3A, NOT %253A.

    Asserts:
    - unquote(raw_path) == f"/model/{model}/invoke".
    - quote(unquote(raw_path), safe="/~") contains "%3A" and NOT "%253A".
    - Authorization contains "/bedrock/aws4_request" (service is "bedrock",
      NOT "bedrock-runtime").

    v25 task-3: credential supplied via contextvar.
    """
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json=_TITAN_200)

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        await provider.post_json("/embeddings", {"model": _MODEL_ID_V2, "input": "hi"})
    finally:
        reset_provider_credential(tok)

    req = captured["req"]
    raw_path = req.url.raw_path.decode()

    # Single-decode must resolve to the exact model path
    assert unquote(raw_path) == f"/model/{_MODEL_ID_V2}/invoke", (
        f"unquote(raw_path) must equal '/model/{_MODEL_ID_V2}/invoke', got: {unquote(raw_path)!r}"
    )

    # Canonical URI must single-encode ':' as %3A (never double-encode as %253A)
    canonical_uri = quote(unquote(raw_path), safe="/~")
    assert "%3A" in canonical_uri, f"Canonical URI must contain %3A, got: {canonical_uri!r}"
    assert "%253A" not in canonical_uri, (
        f"Canonical URI must NOT contain %253A (double-encoded), got: {canonical_uri!r}"
    )

    # Authorization must use service "bedrock" (NOT "bedrock-runtime")
    auth = dict(req.headers).get("authorization", "")
    assert "/bedrock/aws4_request" in auth, (
        f"Authorization must contain '/bedrock/aws4_request' "
        f"(service must be 'bedrock'), got: {auth!r}"
    )


# ---------------------------------------------------------------------------
# BE4 — 4xx: error envelope returned, fail-fast on list input
# ---------------------------------------------------------------------------


async def test_invoke_4xx_envelope() -> None:
    """Handler returns 400 on first call of a 2-item list.

    Asserts:
    - Returns (400, error_body) with error_body["error"]["type"] == "bedrock_error".
    - error_body["error"]["code"] == 400.
    - call_count == 1 (fail fast — no second call after first error).

    v25 task-3: credential supplied via contextvar.
    """
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"message": "bad request", "__type": "ValidationException"})

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await provider.post_json(
            "/embeddings", {"model": _MODEL_ID, "input": ["first", "second"]}
        )
    finally:
        reset_provider_credential(tok)

    assert call_count == 1, f"Fail-fast: expected 1 call on first 4xx, got {call_count}"
    assert status == 400, f"Expected status 400, got {status}"

    error = body.get("error")
    assert error is not None, f"Response must have 'error' key; got: {body}"
    assert error["type"] == "bedrock_error", (
        f"error['type'] must be 'bedrock_error', got: {error.get('type')!r}"
    )
    assert error["code"] == 400, f"error['code'] must be 400, got: {error.get('code')!r}"
    assert isinstance(error.get("message"), str), (
        f"error['message'] must be a string, got: {type(error.get('message'))}"
    )


# ---------------------------------------------------------------------------
# BE5 — ConnectError → UpstreamUnavailableError
# ---------------------------------------------------------------------------


async def test_connect_error_raises() -> None:
    """Handler raises httpx.ConnectError → post_json raises UpstreamUnavailableError.

    v25 task-3: credential supplied via contextvar.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await provider.post_json("/embeddings", {"model": _MODEL_ID, "input": "hello"})
    finally:
        reset_provider_credential(tok)


# ---------------------------------------------------------------------------
# BE6 — dimensions passthrough: extra key forwarded to invoke body
# ---------------------------------------------------------------------------


async def test_dimensions_passthrough() -> None:
    """payload['dimensions'] is forwarded verbatim in the Titan invoke request body.

    Asserts that the captured invoke body is exactly
    {"inputText": "x", "dimensions": 256}.

    v25 task-3: credential supplied via contextvar.
    """
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_TITAN_200)

    provider = _make_provider(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        await provider.post_json(
            "/embeddings", {"model": _MODEL_ID, "input": "x", "dimensions": 256}
        )
    finally:
        reset_provider_credential(tok)

    assert "body" in captured, "Handler was not called"
    invoke_body = json.loads(captured["body"])
    assert invoke_body == {"inputText": "x", "dimensions": 256}, (
        f"Invoke body mismatch: {invoke_body}"
    )


# ---------------------------------------------------------------------------
# BE7 — Wiring: create_app registers bedrock embeddings UNCONDITIONALLY
# ---------------------------------------------------------------------------
# v25 task-3 amendment: bedrock is wired unconditionally — no env-cred guard.
# ---------------------------------------------------------------------------


def test_wiring_present_unconditionally() -> None:
    """create_app() with NO bedrock env creds → provider_registry.get('bedrock') is
    still a BedrockEmbeddingsProvider instance.

    v25 task-3: bedrock is registered unconditionally (env-guard removed).

    RIGHT-REASON RED: current wiring gates 'bedrock' embeddings on
    resolve_aws_credentials(settings) being truthy — absent without env creds.

    BE7 is sync (create_app is sync) — no asyncio mark.
    """
    from gateway.main import create_app

    # No bedrock env creds at all
    settings_no_creds = _make_settings()
    app = create_app(settings_no_creds)  # type: ignore[arg-type]
    bedrock_provider = app.state.provider_registry.get("bedrock")
    assert isinstance(bedrock_provider, BedrockEmbeddingsProvider), (
        f"provider_registry.get('bedrock') must be a BedrockEmbeddingsProvider "
        f"UNCONDITIONALLY after task-3 BUILD (no env-cred guard). "
        f"Got {type(bedrock_provider)!r} — pre-BUILD state (still env-gated)."
    )
