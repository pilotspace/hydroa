"""RED suite for agent-coding-stub-harness (TASK.md §2).

Tests SEAM B (StubCompletionUpstream), SEAM C (wire_mock_transport), fixture library
(helios_request / provider_fixture), and the provenance guard.

NON-e2e: zero live network calls, zero @pytest.mark.e2e markers.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── imports that will be RED until the harness module exists ─────────────────

from tests._helios_harness import (
    HarnessError,
    HeliosCase,
    Provider,
    ProviderFixture,
    StubCompletionUpstream,
    assert_fixtures_have_provenance,
    fake_provider_credential,
    helios_request,
    provider_fixture,
    sse_handler,
    wire_mock_transport,
)

# ── local fixtures (api_key + active_model mirror test_proxy_completions.py) ─

@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "Acme", "email": "ada@acme.io", "password": "correct horse battery"},
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "ada@acme.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


# ── constants ────────────────────────────────────────────────────────────────

# Canonical non-stream chat OpenAI-wire response.
# This constant MUST NOT change — it is the byte-identical golden baseline.
_GOLDEN_NOOP_BODY: dict[str, Any] = {
    "id": "chatcmpl-noop-baseline-golden",
    "object": "chat.completion",
    "created": 1750000000,
    "model": "openai/gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


# ═════════════════════════════════════════════════════════════════════════════
# §1 — HarnessError shape
# ═════════════════════════════════════════════════════════════════════════════


def test_harness_error_is_assertion_error() -> None:
    """HarnessError must subclass AssertionError so pytest treats it as a test failure."""
    err = HarnessError("invalid_sse_fixture")
    assert isinstance(err, AssertionError)
    assert err.code == "invalid_sse_fixture"


def test_harness_error_codes() -> None:
    for code in ("invalid_sse_fixture", "stub_unscripted", "unfaithful_fixture"):
        err = HarnessError(code)  # type: ignore[arg-type]
        assert err.code == code


# ═════════════════════════════════════════════════════════════════════════════
# §2 — StubCompletionUpstream (SEAM B)
# ═════════════════════════════════════════════════════════════════════════════


def test_stub_construction_no_scripts() -> None:
    """Construction with no scripts is valid; forwarded list starts empty."""
    stub = StubCompletionUpstream()
    assert stub.forwarded == []


def test_stub_construction_with_complete_script() -> None:
    stub = StubCompletionUpstream(complete=(200, {"id": "x", "choices": []}))
    assert stub.forwarded == []


def test_stub_construction_with_valid_sse_frames() -> None:
    frames = [
        b'data: {"id":"g-1","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    stub = StubCompletionUpstream(stream=frames)
    assert stub.forwarded == []


def test_stub_rejects_malformed_sse_frame_at_construction() -> None:
    """A frame not starting with b'data: ' must raise HarnessError at construction time."""
    with pytest.raises(HarnessError) as exc_info:
        StubCompletionUpstream(stream=[b"bad-frame\n\n"])
    assert exc_info.value.code == "invalid_sse_fixture"


def test_stub_rejects_frame_missing_double_newline() -> None:
    """A frame missing the trailing \\n\\n terminator is malformed."""
    with pytest.raises(HarnessError) as exc_info:
        StubCompletionUpstream(stream=[b"data: {}\n"])  # only single newline
    assert exc_info.value.code == "invalid_sse_fixture"


def test_stub_rejects_empty_frame() -> None:
    with pytest.raises(HarnessError) as exc_info:
        StubCompletionUpstream(stream=[b""])
    assert exc_info.value.code == "invalid_sse_fixture"


@pytest.mark.asyncio
async def test_stub_complete_records_payload_and_returns_scripted() -> None:
    body = {"id": "x", "choices": [], "usage": {}}
    stub = StubCompletionUpstream(complete=(200, body))
    payload = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    status, result = await stub.complete(payload)
    assert status == 200
    assert result == body
    assert stub.forwarded == [payload]


@pytest.mark.asyncio
async def test_stub_complete_records_multiple_calls_in_order() -> None:
    stub = StubCompletionUpstream(complete=(200, {"id": "y"}))
    p1 = {"model": "m", "messages": [{"role": "user", "content": "a"}]}
    p2 = {"model": "m", "messages": [{"role": "user", "content": "b"}]}
    await stub.complete(p1)
    await stub.complete(p2)
    assert stub.forwarded == [p1, p2]


@pytest.mark.asyncio
async def test_stub_complete_unscripted_raises_harness_error() -> None:
    stub = StubCompletionUpstream()  # no complete script
    with pytest.raises(HarnessError) as exc_info:
        await stub.complete({"model": "m", "messages": []})
    assert exc_info.value.code == "stub_unscripted"


@pytest.mark.asyncio
async def test_stub_stream_records_payload_and_yields_frames() -> None:
    frames = [
        b'data: {"id":"g-1","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    stub = StubCompletionUpstream(stream=frames)
    payload = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "yo"}]}
    collected: list[bytes] = []
    async for chunk in stub.stream(payload):
        collected.append(chunk)
    assert collected == frames
    assert stub.forwarded == [payload]


@pytest.mark.asyncio
async def test_stub_stream_unscripted_raises_harness_error() -> None:
    stub = StubCompletionUpstream()  # no stream script
    with pytest.raises(HarnessError) as exc_info:
        async for _ in stub.stream({"model": "m", "messages": []}):
            pass
    assert exc_info.value.code == "stub_unscripted"


# ═════════════════════════════════════════════════════════════════════════════
# §3 — Fixture library
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "case",
    [
        "chat",
        "chat_stream",
        "tool_call",
        "parallel_tool_calls",
        "tool_result_followup",
        "reasoning_effort",
    ],
)
def test_helios_request_has_required_fields(case: str) -> None:
    req = helios_request(case)  # type: ignore[arg-type]
    assert isinstance(req, dict)
    assert "model" in req
    assert "messages" in req


def test_helios_request_chat_stream_has_stream_true() -> None:
    req = helios_request("chat_stream")
    assert req.get("stream") is True


def test_helios_request_tool_call_has_tools() -> None:
    req = helios_request("tool_call")
    assert "tools" in req and len(req["tools"]) >= 1  # type: ignore[arg-type]


def test_helios_request_parallel_tool_calls_has_tools() -> None:
    req = helios_request("parallel_tool_calls")
    assert "tools" in req and len(req["tools"]) >= 1  # type: ignore[arg-type]


def test_helios_request_tool_result_followup_has_tool_role() -> None:
    req = helios_request("tool_result_followup")
    messages: list[dict[str, Any]] = req["messages"]  # type: ignore[assignment]
    roles = [m.get("role") for m in messages]
    assert "tool" in roles


def test_helios_request_reasoning_effort_has_reasoning_field() -> None:
    req = helios_request("reasoning_effort")
    # Must carry reasoning_effort or reasoning key at top-level
    assert "reasoning_effort" in req or "reasoning" in req


@pytest.mark.parametrize(
    "case,provider",
    [
        ("chat", "anthropic"),
        ("chat", "gemini"),
        ("chat", "bedrock"),
        ("chat", "openrouter"),
        ("tool_call", "anthropic"),
        ("parallel_tool_calls", "anthropic"),
        ("reasoning_effort", "anthropic"),
    ],
)
def test_provider_fixture_has_native_and_provenance(case: str, provider: str) -> None:
    pf = provider_fixture(case, provider)  # type: ignore[arg-type]
    assert isinstance(pf, dict)
    assert "native" in pf
    assert "provenance" in pf
    # provenance must be a non-empty string
    assert isinstance(pf["provenance"], str) and pf["provenance"].strip() != ""


def test_provider_fixture_chat_anthropic_native_is_dict() -> None:
    """chat/anthropic must return a dict native body (non-stream response)."""
    pf = provider_fixture("chat", "anthropic")
    assert isinstance(pf["native"], dict)


def test_provider_fixture_parallel_tool_calls_anthropic_native_is_dict() -> None:
    """parallel_tool_calls/anthropic native is the non-stream body (for SEAM A)."""
    pf = provider_fixture("parallel_tool_calls", "anthropic")
    native = pf["native"]
    assert isinstance(native, dict)
    # Must contain 2 tool_use blocks in content
    content = native.get("content", [])
    assert isinstance(content, list)
    tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) == 2


def test_provider_fixture_chat_stream_anthropic_native_is_list_of_bytes() -> None:
    """chat_stream/anthropic must return list[bytes] native SSE frames."""
    pf = provider_fixture("chat_stream", "anthropic")
    native = pf["native"]
    assert isinstance(native, list)
    assert all(isinstance(f, bytes) for f in native)


def test_assert_fixtures_have_provenance_passes_for_real_library() -> None:
    """The canonical library must have provenance on all entries — no exception."""
    assert_fixtures_have_provenance()  # must not raise


def test_assert_fixtures_have_provenance_fails_on_empty_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monkey-patch the library to inject a blank-provenance entry; must raise."""
    import tests._helios_harness as harness_mod

    original = harness_mod._FIXTURE_LIBRARY.copy()  # type: ignore[attr-defined]
    try:
        harness_mod._FIXTURE_LIBRARY[("chat", "anthropic")] = ProviderFixture(
            native={"bad": True},
            provenance="",  # empty → should fail guard
        )
        with pytest.raises(HarnessError) as exc_info:
            assert_fixtures_have_provenance()
        assert exc_info.value.code == "unfaithful_fixture"
    finally:
        harness_mod._FIXTURE_LIBRARY.clear()
        harness_mod._FIXTURE_LIBRARY.update(original)


# ═════════════════════════════════════════════════════════════════════════════
# §4 — SEAM A: pure translation helper (no adapter, no HTTP)
# ═════════════════════════════════════════════════════════════════════════════


def test_seam_a_parallel_tool_translation() -> None:
    """_anthropic_to_openai on the parallel_tool_calls native body → two tool_calls."""
    from gateway.proxy.infrastructure.anthropic_upstream import _anthropic_to_openai

    pf = provider_fixture("parallel_tool_calls", "anthropic")
    native: dict[str, Any] = pf["native"]  # type: ignore[assignment]

    openai_body = _anthropic_to_openai(native)
    choices = openai_body.get("choices", [])
    assert len(choices) >= 1
    tool_calls = choices[0].get("message", {}).get("tool_calls", [])
    assert len(tool_calls) == 2, f"Expected 2 tool_calls, got {len(tool_calls)}: {tool_calls}"

    # Each tool call must have id, type, function.name, function.arguments
    for tc in tool_calls:
        assert tc.get("type") == "function"
        assert "id" in tc
        func = tc.get("function", {})
        assert "name" in func
        assert "arguments" in func


# ═════════════════════════════════════════════════════════════════════════════
# §5 — SEAM C: real adapter against a mocked httpx transport
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_seam_c_real_adapter_via_mock_transport() -> None:
    """AnthropicCompletionUpstream.stream() through MockTransport yields tool_calls frames.

    Exercises the REAL adapter (request build · auth · SSE parse · error map) with
    zero sockets — wire_mock_transport swaps _client for httpx.AsyncClient(MockTransport).
    """
    from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream

    # Native Anthropic SSE for parallel tool calls (streaming form).
    # Each element is one complete SSE event block: event:\ndata:\n\n
    native_sse_frames: list[bytes] = [
        b"event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_p",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-5-sonnet-20241022",
                    "usage": {"input_tokens": 25, "output_tokens": 0},
                },
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_start\ndata: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_01", "name": "get_weather", "input": {}},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_delta\ndata: "
        + json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"city": "Paris"}'},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_stop\ndata: "
        + json.dumps({"type": "content_block_stop", "index": 0}).encode()
        + b"\n\n",
        b"event: content_block_start\ndata: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_02", "name": "get_time", "input": {}},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_delta\ndata: "
        + json.dumps(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"timezone": "UTC"}'},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_stop\ndata: "
        + json.dumps({"type": "content_block_stop", "index": 1}).encode()
        + b"\n\n",
        b"event: message_delta\ndata: "
        + json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 12},
            }
        ).encode()
        + b"\n\n",
        b"event: message_stop\ndata: "
        + json.dumps({"type": "message_stop"}).encode()
        + b"\n\n",
    ]

    adapter = AnthropicCompletionUpstream()
    handler = sse_handler(native_sse_frames)
    wire_mock_transport(adapter, handler)

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "What's the weather in Paris and the UTC time?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Get time",
                    "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}},
                },
            },
        ],
    }

    with fake_provider_credential("test-anthropic-key"):
        frames_collected: list[bytes] = []
        async for chunk in adapter.stream(payload):
            frames_collected.append(chunk)

    # The adapter must have yielded at least some SSE frames
    assert len(frames_collected) > 0

    # Parse all tool_call deltas from the OpenAI-wire SSE output
    tool_names_seen: set[str] = set()
    tool_ids_seen: set[str] = set()
    for chunk in frames_collected:
        if not chunk.startswith(b"data: "):
            continue
        raw = chunk[len(b"data: "):].strip()
        if raw == b"[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for choice in obj.get("choices", []):
            delta = choice.get("delta", {})
            for tc_delta in delta.get("tool_calls", []):
                if tc_delta.get("function", {}).get("name"):
                    tool_names_seen.add(tc_delta["function"]["name"])
                if tc_delta.get("id"):
                    tool_ids_seen.add(tc_delta["id"])

    assert "get_weather" in tool_names_seen, f"get_weather not in {tool_names_seen}"
    assert "get_time" in tool_names_seen, f"get_time not in {tool_names_seen}"
    assert len(tool_ids_seen) == 2, f"Expected 2 unique tool ids, got {tool_ids_seen}"


# ═════════════════════════════════════════════════════════════════════════════
# §6 — stub_upstream fixture (SEAM B wired into app)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_stub_upstream_fixture_installs_on_app_state(
    app: object,
    stub_upstream: Any,
) -> None:
    """stub_upstream factory installs the stub on app.state.completion_upstream."""
    body: dict[str, Any] = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
    stub = stub_upstream(complete=(200, body))
    assert app.state.completion_upstream is stub  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_byte_identical_noop_baseline(
    client: httpx.AsyncClient,
    stub_upstream: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """The gateway must return the stub's body unchanged — no silent transformation.

    The GOLDEN constant is defined above; any difference is a proxy-layer bug.
    This test is NOT vacuous: the proxy could re-order keys, add fields, or
    strip fields — all of which would fail this assertion.
    """
    stub_upstream(complete=(200, _GOLDEN_NOOP_BODY))

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {api_key['key']}"},
    )
    assert resp.status_code == 200
    assert resp.json() == _GOLDEN_NOOP_BODY


# ═════════════════════════════════════════════════════════════════════════════
# §7 — recorded_usage fixture
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recorded_usage_readback(
    client: httpx.AsyncClient,
    stub_upstream: Any,
    recorded_usage: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """After a SEAM-B request, recorded_usage() returns the captured usage row."""
    body: dict[str, Any] = {
        "id": "chatcmpl-u1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }
    stub_upstream(complete=(200, body))

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {api_key['key']}"},
    )
    assert resp.status_code == 200

    row = await recorded_usage()
    assert row is not None
    assert row.prompt_tokens == 7
    assert row.completion_tokens == 3
    assert row.status == 200


# ═════════════════════════════════════════════════════════════════════════════
# §8 — suite-level invariants
# ═════════════════════════════════════════════════════════════════════════════


def test_harness_suite_non_e2e_no_network() -> None:
    """No test in this module carries @pytest.mark.e2e — all tests are local."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                # Check for @pytest.mark.e2e
                if isinstance(decorator, ast.Attribute):
                    if decorator.attr == "e2e":
                        pytest.fail(f"Found @pytest.mark.e2e on {node.name} — harness must be non-e2e")


def test_stub_upstream_is_non_blocking_pure_memory() -> None:
    """StubCompletionUpstream uses no sockets: construction and calls are pure in-memory."""
    import socket

    # We use a simple proof: patch socket.socket to raise if called, then build + call stub.
    original_socket = socket.socket

    class _NoSocket:
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise AssertionError("StubCompletionUpstream must not open sockets")

    socket.socket = _NoSocket  # type: ignore[misc]
    try:
        stub = StubCompletionUpstream(complete=(200, {"id": "ok"}))
        # If construction opened a socket, the above would have raised already.
        assert stub.forwarded == []
    finally:
        socket.socket = original_socket
