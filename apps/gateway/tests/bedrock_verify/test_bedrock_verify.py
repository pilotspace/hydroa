"""Red suite for bedrock-verify (v20 task 6) — frozen contract.

EARNED-GREEN, no-docker integration verification of the v20 AWS Bedrock provider
surface (chat · streaming · tools · embeddings) against an INDEPENDENT SigV4
verifier over real TCP (127.0.0.1). The stub re-implements AWS SigV4 v4 from the
spec (it does NOT import gateway.sign_request), so accepting the real adapter's
signed requests is a genuine cross-check — the same independent-oracle philosophy
that caught the v20 task-2 double-encode bug. BV1 pins the stub's verifier to the
AWS-published get-vanilla vector so the verifier itself is ground-truth-correct
before it judges the gateway's signatures.

The stub lives at the repo-root ``scripts/v20_bedrock_stub.py`` (pure stdlib,
self-contained) and is loaded here via importlib so the single operator-runnable
artifact is also the test harness.

RED until BUILD creates ``scripts/v20_bedrock_stub.py`` (importlib load raises
FileNotFoundError) and the live script + overlay (BV9 existence asserts).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.infrastructure.bedrock_embeddings import BedrockEmbeddingsProvider
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials
from gateway.proxy.infrastructure.bedrock_upstream import BedrockCompletionUpstream

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Load the independent stub from the repo root (pure stdlib, no gateway import).
# RED: spec_from_file_location's loader raises FileNotFoundError until BUILD
# writes scripts/v20_bedrock_stub.py.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # bedrock_verify→tests→gateway→apps→<root>
_STUB_PATH = _REPO_ROOT / "scripts" / "v20_bedrock_stub.py"


def _load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("v20_bedrock_stub", _STUB_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # RED here: FileNotFoundError until the stub exists
    return mod


stub = _load_stub()

# ---------------------------------------------------------------------------
# Fake credentials — the AWS-published test-suite creds (PUBLIC, fake). The same
# secret pins BV1 to the get-vanilla vector and is the secret the stub recomputes
# against for every adapter request. NEVER a real key.
# ---------------------------------------------------------------------------

_FAKE_AKID = "AKIDEXAMPLE"
_FAKE_SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"  # AWS get-vanilla secret
_REGION = "us-east-1"

_CREDS = AwsCredentials(
    access_key_id=_FAKE_AKID,
    secret_access_key=_FAKE_SECRET,
    region=_REGION,
)

# Model ids the stub keys behavior on (a ':' in the chat model proves the %3A
# canonical-URI round-trip end-to-end against the independent verifier — the SV8 bug).
_M_CHAT = "anthropic.claude-3-5-sonnet-v2:0"
_M_STREAM = "anthropic.claude-3-haiku-stream"
_M_TOOL = "anthropic.claude-3-5-sonnet-tool"
_M_RETRY = "anthropic.claude-retry-once"
_M_FB_FAIL = "anthropic.claude-fb-fail"
_M_FB_OK = "anthropic.claude-fb-ok"
_M_EMBED = "amazon.titan-embed-text-v1"


# ---------------------------------------------------------------------------
# Stub server fixture — session-scoped on an ephemeral 127.0.0.1 port; reset
# (counters/retry state) before each test for double-pass-style idempotency.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def stub_server() -> Any:
    server: HTTPServer = stub.make_stub_server(port=0)  # 0 → OS-assigned ephemeral port
    thread = stub.start_stub_in_thread(server)
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"
    try:
        yield {"server": server, "base_url": base_url, "host": host, "port": port}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_stub(stub_server: dict[str, Any]) -> None:
    # Clear counters + retry state before each test (mirrors POST /__reset between live passes).
    httpx.post(f"{stub_server['base_url']}/__reset", timeout=5)


def _chat_upstream(base_url: str, *, max_retries: int = 0) -> BedrockCompletionUpstream:
    return BedrockCompletionUpstream(
        credentials=_CREDS,
        region=_REGION,
        endpoint_url=base_url,
        max_retries=max_retries,
        backoff_base=0.0,  # no real sleep between retries — fast test
    )


# ---------------------------------------------------------------------------
# BV1 — stub verifier pinned to the AWS get-vanilla published vector.
# ---------------------------------------------------------------------------


def test_BV1_stub_verifier_matches_aws_get_vanilla_vector() -> None:
    """The stub's INDEPENDENT SigV4 core reproduces the AWS-published get-vanilla
    signature byte-for-byte — proving the verifier is ground-truth-correct before
    it judges any gateway signature."""
    sig = stub.sigv4_signature(
        method="GET",
        canonical_uri="/",
        canonical_querystring="",
        signed_headers={"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"},
        payload_hash=hashlib.sha256(b"").hexdigest(),
        service="service",  # the AWS test suite's own service name (NOT "bedrock")
        region="us-east-1",
        secret_access_key=_FAKE_SECRET,
        amz_date="20150830T123600Z",
    )
    assert sig == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


# ---------------------------------------------------------------------------
# BV2 — real chat request accepted by the independent verifier; OpenAI shape + billing.
# ---------------------------------------------------------------------------


async def test_BV2_real_chat_request_accepted_with_billing(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    status, body = await upstream.complete(
        {"model": _M_CHAT, "messages": [{"role": "user", "content": "hi"}]}
    )
    assert status == 200, body  # stub ACCEPTED the SigV4 signature (incl. the %3A model path)
    assert body["object"] == "chat.completion"
    usage = body["usage"]
    assert isinstance(usage["prompt_tokens"], int)
    assert isinstance(usage["completion_tokens"], int)
    assert isinstance(usage["total_tokens"], int)
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# ---------------------------------------------------------------------------
# BV3 — tampered / missing signature rejected (proves the verifier is not a rubber stamp).
# ---------------------------------------------------------------------------


async def test_BV3_tampered_and_missing_signature_rejected(stub_server: dict[str, Any]) -> None:
    from gateway.proxy.infrastructure.bedrock_sigv4 import sign_request

    base = stub_server["base_url"]
    url = f"{base}/model/{_M_CHAT}/converse"
    body_bytes = json.dumps({"messages": []}, separators=(",", ":")).encode("utf-8")
    sig_headers = sign_request(
        method="POST",
        url=url,
        body=body_bytes,
        service="bedrock",
        region=_REGION,
        credentials=_CREDS,
        timestamp=datetime.now(UTC),
    )
    # Corrupt the Signature= hex in the otherwise-valid Authorization header.
    good_auth = sig_headers["Authorization"]
    tampered_auth = good_auth[:-8] + ("0" * 8 if not good_auth.endswith("0" * 8) else "1" * 8)
    async with httpx.AsyncClient(timeout=10) as client:
        r_tampered = await client.post(
            url,
            content=body_bytes,
            headers={
                **sig_headers,
                "Authorization": tampered_auth,
                "content-type": "application/json",
            },
        )
        assert r_tampered.status_code == 403
        assert "mismatch" in r_tampered.json()["message"].lower()

        # No Authorization header at all → 403.
        r_missing = await client.post(
            url,
            content=body_bytes,
            headers={"content-type": "application/json"},
        )
        assert r_missing.status_code == 403


# ---------------------------------------------------------------------------
# BV4 — real streaming request accepted; OpenAI SSE chunks end with usage + [DONE].
# ---------------------------------------------------------------------------


async def test_BV4_real_streaming_accepted_with_usage(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    chunks: list[bytes] = []
    async for chunk in upstream.stream(
        {"model": _M_STREAM, "messages": [{"role": "user", "content": "hi"}], "stream": True}
    ):
        chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8")
    assert "chat.completion.chunk" in text  # stub ACCEPTED the signature, real SSE emitted
    assert "[DONE]" in text
    # A usage frame must be present for streaming billing (v12 contract).
    data_lines = [ln[len("data: ") :] for ln in text.splitlines() if ln.startswith("data: ")]
    payloads = [json.loads(d) for d in data_lines if d.strip() and d.strip() != "[DONE]"]
    assert any("usage" in p and p["usage"] for p in payloads)


# ---------------------------------------------------------------------------
# BV5 — real tool round-trip: request carries toolConfig; response carries tool_calls.
# ---------------------------------------------------------------------------


async def test_BV5_real_tool_round_trip(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    status, body = await upstream.complete(
        {
            "model": _M_TOOL,
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        }
    )
    assert status == 200, body
    tool_calls = body["choices"][0]["message"]["tool_calls"]
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert json.loads(tool_calls[0]["function"]["arguments"])  # valid JSON args
    assert body["choices"][0]["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------------------
# BV6 — real embeddings: OpenAI list shape + EXACT summed inputTextTokenCount.
# ---------------------------------------------------------------------------


async def test_BV6_real_embeddings_exact_tokens(stub_server: dict[str, Any]) -> None:
    provider = BedrockEmbeddingsProvider(
        credentials=_CREDS, region=_REGION, endpoint_url=stub_server["base_url"]
    )
    # The stub returns inputTextTokenCount == len(inputText), so the exact sum is verifiable.
    status, body = await provider.post_json(
        "/embeddings", {"model": _M_EMBED, "input": ["hello", "hi"]}
    )
    assert status == 200, body
    assert body["object"] == "list"
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert all(isinstance(d["embedding"], list) and d["embedding"] for d in body["data"])
    expected = len("hello") + len("hi")  # 5 + 2 = 7 — exact, summed across the two invokes
    assert body["usage"]["total_tokens"] == expected
    assert body["usage"]["prompt_tokens"] == expected


# ---------------------------------------------------------------------------
# BV7 — retry composes: 503 on attempt 1 → 200 on attempt 2; stub saw >= 2 attempts.
# ---------------------------------------------------------------------------


async def test_BV7_retry_to_success_composes(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"], max_retries=2)
    status, body = await upstream.complete(
        {"model": _M_RETRY, "messages": [{"role": "user", "content": "hi"}]}
    )
    assert status == 200, body  # transparently served after the retry
    async with httpx.AsyncClient(timeout=10) as client:
        counters = (await client.get(f"{stub_server['base_url']}/__counters")).json()
    assert counters.get(_M_RETRY, 0) >= 2  # the stub observed the retried (re-signed) attempt


# ---------------------------------------------------------------------------
# BV8 — error-aware fallback composes: context-window 400 on fb-fail → 200 from fb-ok.
# ---------------------------------------------------------------------------


async def test_BV8_error_aware_fallback_composes(stub_server: dict[str, Any]) -> None:
    upstream = _chat_upstream(stub_server["base_url"])
    router = FallbackModelRouter(
        upstream=upstream,
        model_groups={"bedrock-fb": [_M_FB_FAIL, _M_FB_OK]},
        fallback_on_error=True,
    )
    status, body, served = await router.complete(
        {"model": "bedrock-fb", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert status == 200, body
    assert served == _M_FB_OK  # fell over from the context-window 400 to the healthy candidate


# ---------------------------------------------------------------------------
# BV9 — zero-regression floor: stub binds 127.0.0.1; live script + overlay exist + idempotent.
# ---------------------------------------------------------------------------


def test_BV9_localhost_binding_and_live_artifacts(stub_server: dict[str, Any]) -> None:
    assert stub_server["host"] == "127.0.0.1"  # NEVER 0.0.0.0 (security §1)
    assert stub.STUB_HOST == "127.0.0.1"

    live = _REPO_ROOT / "scripts" / "live_v20_verify.py"
    overlay = _REPO_ROOT / "infra" / "docker-compose.e2e.v20.yml"
    assert live.exists(), "operator live double-pass script must exist"
    assert overlay.exists(), "v20 e2e overlay must exist"

    live_src = live.read_text()
    assert "run_id" in live_src  # fresh identity per pass
    assert "__reset" in live_src  # reset between passes (double-pass idempotency)
    assert "127.0.0.1" in live_src  # asserts the stub binding

    overlay_src = overlay.read_text()
    assert "GATEWAY_BEDROCK_ENDPOINT_URL" in overlay_src
    assert "host.docker.internal:9927" in overlay_src
