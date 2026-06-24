"""Failing-first (red) suite for prompt-cache-passthrough (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario (PC1-PC13).

RED before BUILD — they fail for the right reason:
  * SEAM-A translator tests: _openai_to_anthropic_request lacks auto_cache param / cache
    injection logic → AttributeError or missing cache_control keys.
  * SEAM-A response tests: _anthropic_to_openai / _gemini_to_openai don't surface
    cache tokens → cached_tokens missing / prompt_tokens wrong.
  * SEAM-C stream test: AnthropicCompletionUpstream lacks auto_cache ctor param + stepper
    doesn't surface cache tokens → assertion fails.
  * Billing tests: compute_per_token_cost_usd lacks cache_creation_tokens param → TypeError.
  * DB test: usage_records lacks cache_creation_tokens column → Postgres error.

Run only the unit subset (no infra):
  cd apps/gateway && uv run pytest tests/prompt_cache_passthrough/ -q --no-cov \
      -k "not db" -p no:cacheprovider
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers reused across tests
# ---------------------------------------------------------------------------

_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_SYSTEM_CONTENT = "You are a helpful assistant."
_USER_CONTENT = "Hello, tell me about caching."

_BASE_PAYLOAD: dict[str, Any] = {
    "model": "claude-3-5-sonnet-20241022",
    "messages": [
        {"role": "system", "content": _SYSTEM_CONTENT},
        {"role": "user", "content": _USER_CONTENT},
    ],
    "tools": [_TOOL],
}

_PAYLOAD_NO_SYSTEM_NO_TOOLS: dict[str, Any] = {
    "model": "claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hello"}],
}


# ---------------------------------------------------------------------------
# PC1 — Auto-inject caches the stable prefix (system + last tool)
# ---------------------------------------------------------------------------


def test_anthropic_auto_inject_caches_prefix() -> None:
    """_openai_to_anthropic_request(system+tools, no cc, auto_cache=True)
    → system block + last tool have cache_control ephemeral.
    """
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _openai_to_anthropic_request,  # type: ignore[attr-defined]
    )

    result = _openai_to_anthropic_request(
        _BASE_PAYLOAD,
        default_max_tokens=4096,
        auto_cache=True,
    )

    # System block must be in list form with cache_control
    system = result.get("system")
    assert isinstance(system, list), (
        f"system must be a list when cache injected, got: {type(system)}"
    )
    assert len(system) >= 1, "system list must have at least one block"
    system_block = system[-1] if isinstance(system, list) else None
    assert system_block is not None
    cc = system_block.get("cache_control")
    assert cc == {"type": "ephemeral"}, (
        f"system block must carry cache_control ephemeral, got: {cc!r}"
    )

    # Last tool must have cache_control
    tools = result.get("tools", [])
    assert tools, "tools must be non-empty"
    last_tool = tools[-1]
    assert last_tool.get("cache_control") == {"type": "ephemeral"}, (
        f"last tool must carry cache_control ephemeral, got: {last_tool.get('cache_control')!r}"
    )


# ---------------------------------------------------------------------------
# PC2 — Client cache_control passes through; auto-inject suppressed
# ---------------------------------------------------------------------------


def test_client_cache_control_passthrough_suppresses_autoinject() -> None:
    """body w/ client cache_control → forwarded AND no extra auto breakpoint."""
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _openai_to_anthropic_request,  # type: ignore[attr-defined]
    )

    payload: dict[str, Any] = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Hello",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ],
    }

    result = _openai_to_anthropic_request(
        payload,
        default_max_tokens=4096,
        auto_cache=True,  # knob on, but client provided cc
    )

    # The user message content must carry the forwarded cache_control
    non_system_msgs = result.get("messages", [])
    user_msgs = [m for m in non_system_msgs if m.get("role") == "user"]
    assert user_msgs, "must have at least one user message"
    user_content = user_msgs[0].get("content")
    assert isinstance(user_content, list), "user content must be a list (block form)"
    found_cc = any(
        isinstance(block, dict) and block.get("cache_control") == {"type": "ephemeral"}
        for block in user_content
    )
    assert found_cc, (
        f"client cache_control must be forwarded to Anthropic block; got: {user_content!r}"
    )

    # System block must NOT have auto-injected cache_control (client intent respected)
    system = result.get("system")
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                assert block.get("cache_control") is None, (
                    "auto-inject must be suppressed when client already set cache_control; "
                    f"got system block with cache_control: {block!r}"
                )
    elif isinstance(system, str):
        pass  # string form → no cache_control, which is correct


# ---------------------------------------------------------------------------
# PC3 — Knob OFF is byte-identical (no cache_control anywhere)
# ---------------------------------------------------------------------------


def test_knob_off_byte_identical() -> None:
    """auto_cache=False, no client cc → no cache_control anywhere (byte-identical)."""
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _openai_to_anthropic_request,  # type: ignore[attr-defined]
    )

    result = _openai_to_anthropic_request(
        _BASE_PAYLOAD,
        default_max_tokens=4096,
        auto_cache=False,
    )

    # System must be a plain string (byte-identical)
    system = result.get("system")
    assert isinstance(system, str), f"knob OFF must keep system as a string, got: {type(system)}"
    assert "cache_control" not in str(result), (
        f"knob OFF must have no cache_control anywhere, got: {result!r}"
    )

    # No tool in result should have cache_control
    for tool in result.get("tools", []):
        assert "cache_control" not in tool, (
            f"knob OFF must not inject cache_control on tools, got: {tool!r}"
        )


# ---------------------------------------------------------------------------
# PC4 — Anthropic response surfaces cache read + creation tokens
# ---------------------------------------------------------------------------


def test_anthropic_response_surfaces_cache_read() -> None:
    """_anthropic_to_openai(usage i=100, cr=900, cc=50)
    → prompt_tokens==1050, cached_tokens==900, cache_creation_tokens==50.
    """
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _anthropic_to_openai,  # type: ignore[attr-defined]
    )

    body: dict[str, Any] = {
        "id": "msg_01abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "Hello!"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
        },
    }

    result = _anthropic_to_openai(body)
    usage = result.get("usage", {})

    # Total prompt_tokens = i + cr + cc = 100 + 900 + 50 = 1050
    assert usage.get("prompt_tokens") == 1050, (
        f"prompt_tokens must be i+cr+cc=1050, got: {usage.get('prompt_tokens')!r}"
    )
    # cached_tokens = cr = 900
    ptd = usage.get("prompt_tokens_details", {})
    assert ptd.get("cached_tokens") == 900, (
        f"prompt_tokens_details.cached_tokens must be 900 (cache_read), got: {ptd!r}"
    )
    # cache_creation_tokens = cc = 50
    assert ptd.get("cache_creation_tokens") == 50, (
        f"prompt_tokens_details.cache_creation_tokens must be 50, got: {ptd!r}"
    )


# ---------------------------------------------------------------------------
# PC5 — Anthropic streaming surfaces cache tokens (SEAM C real adapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_stream_surfaces_cache_read() -> None:
    """SEAM C: real AnthropicCompletionUpstream streaming with message_start.usage
    carrying cache_read_input_tokens=900 → terminal usage chunk has
    prompt_tokens_details.cached_tokens==900.
    """
    from tests._helios_harness import (
        fake_provider_credential,
        sse_handler,
        wire_mock_transport,
    )

    from gateway.proxy.infrastructure.anthropic_upstream import (
        AnthropicCompletionUpstream,
    )

    # SSE fixture with cache tokens in message_start.usage
    cache_stream_frames = [
        b"event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_cache_01",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-5-sonnet-20241022",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 900,
                        "cache_creation_input_tokens": 50,
                    },
                },
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_start\ndata: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_delta\ndata: "
        + json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi"},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_stop\ndata: "
        + json.dumps({"type": "content_block_stop", "index": 0}).encode()
        + b"\n\n",
        b"event: message_delta\ndata: "
        + json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            }
        ).encode()
        + b"\n\n",
        b"event: message_stop\ndata: " + json.dumps({"type": "message_stop"}).encode() + b"\n\n",
    ]

    adapter = AnthropicCompletionUpstream(auto_cache=False)
    wire_mock_transport(adapter, sse_handler(cache_stream_frames))

    payload: dict[str, Any] = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }

    terminal_usage: dict[str, Any] | None = None
    with fake_provider_credential("test-key"):
        async for chunk_bytes in adapter.stream(payload):
            if chunk_bytes == b"data: [DONE]\n\n":
                continue
            if chunk_bytes.startswith(b"data: "):
                try:
                    chunk = json.loads(chunk_bytes[6:])
                    if "usage" in chunk:
                        terminal_usage = chunk["usage"]
                except (json.JSONDecodeError, IndexError):
                    continue

    assert terminal_usage is not None, "No terminal usage chunk found in stream"
    ptd = terminal_usage.get("prompt_tokens_details", {})
    assert ptd.get("cached_tokens") == 900, (
        f"terminal usage must have prompt_tokens_details.cached_tokens==900, got: {ptd!r}"
    )
    # PC5 gap: cache_creation_input_tokens=50 in the fixture must also surface.
    assert ptd.get("cache_creation_tokens") == 50, (
        f"terminal usage must have prompt_tokens_details.cache_creation_tokens==50, got: {ptd!r}"
    )
    # prompt_tokens = input_tokens(100) + cache_read(900) + cache_creation(50) = 1050
    assert terminal_usage.get("prompt_tokens") == 1050, (
        f"terminal usage prompt_tokens must be 100+900+50=1050, got: {terminal_usage.get('prompt_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# PC6 — Gemini response surfaces cached content tokens
# ---------------------------------------------------------------------------


def test_gemini_response_surfaces_cached_content() -> None:
    """_gemini_to_openai(promptTokenCount=1000, cachedContentTokenCount=800)
    → prompt_tokens==1000, prompt_tokens_details.cached_tokens==800.
    """
    from gateway.proxy.infrastructure.gemini_upstream import (
        _gemini_to_openai,  # type: ignore[attr-defined]
    )

    body: dict[str, Any] = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Hello!"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "totalTokenCount": 1050,
            "cachedContentTokenCount": 800,
        },
    }

    result = _gemini_to_openai(body, model="google/gemini-2.0-flash")
    usage = result.get("usage", {})

    # promptTokenCount=1000 already includes the 800 cached → prompt_tokens=1000
    assert usage.get("prompt_tokens") == 1000, (
        f"prompt_tokens must be 1000 (total from Gemini), got: {usage.get('prompt_tokens')!r}"
    )
    ptd = usage.get("prompt_tokens_details", {})
    assert ptd.get("cached_tokens") == 800, (
        f"prompt_tokens_details.cached_tokens must be 800, got: {ptd!r}"
    )


# ---------------------------------------------------------------------------
# PC7 — Cache reads bill at the cached rate via the recorder (SEAM B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_billing_via_recorder() -> None:
    """SEAM B: a response usage with cached_tokens → the usage dict passed to the
    recorder (captured by _HarnessUsageRecorder) includes prompt_tokens_details.cached_tokens.

    This verifies the full proxy→recorder path: the translated response must carry
    prompt_tokens_details.cached_tokens so the recorder can bill at the cached rate.
    We verify the harness recorder receives the cached_tokens in the usage dict.
    """
    import uuid

    from tests.tiered_token_billing.conftest import (
        FakeSession,
        FakeSessionFactory,
        StreamCapture,
    )

    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=Decimal("0.000005"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    # Usage dict as would come from the translator (prompt_tokens includes cache_read)
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="anthropic/claude-3-5-sonnet",
        usage={
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens": 100,
        },
        status=200,
    )

    evt = stream.last_event
    # 800 cached_tokens must appear in the event
    assert evt.get("cached_tokens") == "800", (
        f"recorder event must carry cached_tokens=800, got: {evt.get('cached_tokens')!r}"
    )
    # Billing: (200×0.00001) + (800×0.000005) + (100×0.00003) = 0.002+0.004+0.003 = 0.009
    expected = (
        Decimal("200") * Decimal("0.00001")
        + Decimal("800") * Decimal("0.000005")
        + Decimal("100") * Decimal("0.00003")
    )
    assert Decimal(evt["cost_usd"]) == expected, (
        f"cost must bill cached at cached_input rate; expected {expected}, got {evt['cost_usd']!r}"
    )


# ---------------------------------------------------------------------------
# PC8 — Cache WRITES bill at the cache-creation rate
# ---------------------------------------------------------------------------


def test_cache_write_billed_at_creation_rate() -> None:
    """compute_per_token_cost_usd with cache_creation_tokens=50 + cache_creation_price set
    → cost includes 50*creation_price; fresh_in == prompt - cached - cache_creation.
    """
    from gateway.usage.application.recorder import compute_per_token_cost_usd  # type: ignore[import]

    prompt_price = Decimal("0.00001")
    cached_price = Decimal("0.000005")
    creation_price = Decimal("0.0000125")  # Anthropic 1.25× write premium
    completion_price = Decimal("0.00003")

    # prompt_tokens=1000, cached=800, cache_creation=50, fresh_in = 1000-800-50 = 150
    cost = compute_per_token_cost_usd(
        prompt_tokens=1000,
        completion_tokens=100,
        cached_tokens=800,
        reasoning_tokens=0,
        cache_creation_tokens=50,
        prompt_price=prompt_price,
        completion_price=completion_price,
        cached_price=cached_price,
        reasoning_price=None,
        cache_creation_price=creation_price,
        markup_pct=Decimal("0"),
    )

    # fresh_in = 1000 - 800 - 50 = 150
    expected = (
        Decimal("150") * prompt_price
        + Decimal("800") * cached_price
        + Decimal("50") * creation_price
        + Decimal("100") * completion_price
    )
    assert cost == expected, f"cache-write billing mismatch: expected {expected}, got {cost}"


# ---------------------------------------------------------------------------
# PC9 — Cache-write tier is byte-identical when price is NULL
# ---------------------------------------------------------------------------


def test_cache_write_null_price_falls_back_to_prompt_rate() -> None:
    """cache_creation_price=None, cache_creation_tokens=50
    → 50 tokens billed at prompt rate (no regression).
    """
    from gateway.usage.application.recorder import compute_per_token_cost_usd  # type: ignore[import]

    prompt_price = Decimal("0.00001")
    completion_price = Decimal("0.00003")

    cost = compute_per_token_cost_usd(
        prompt_tokens=1000,
        completion_tokens=100,
        cached_tokens=0,
        reasoning_tokens=0,
        cache_creation_tokens=50,
        prompt_price=prompt_price,
        completion_price=completion_price,
        cached_price=None,
        reasoning_price=None,
        cache_creation_price=None,  # NULL → falls back to prompt rate
        markup_pct=Decimal("0"),
    )

    # fresh_in = 1000 - 0 - 50 = 950
    # cache_creation = 50 at prompt_price (NULL fallback)
    expected = (
        Decimal("950") * prompt_price
        + Decimal("50") * prompt_price  # NULL creation → prompt rate
        + Decimal("100") * completion_price
    )
    assert cost == expected, (
        f"NULL creation price must fall back to prompt rate: expected {expected}, got {cost}"
    )


# ---------------------------------------------------------------------------
# PC10 — All tiers zero takes the flat path (byte-identical)
# ---------------------------------------------------------------------------


def test_all_tiers_zero_flat_path() -> None:
    """cached==reasoning==cache_creation==0 → flat byte-identical cost."""
    from gateway.usage.application.recorder import compute_per_token_cost_usd  # type: ignore[import]

    prompt_price = Decimal("0.00001")
    completion_price = Decimal("0.00003")
    markup = Decimal("10")

    cost = compute_per_token_cost_usd(
        prompt_tokens=1000,
        completion_tokens=200,
        cached_tokens=0,
        reasoning_tokens=0,
        cache_creation_tokens=0,
        prompt_price=prompt_price,
        completion_price=completion_price,
        cached_price=None,
        reasoning_price=None,
        cache_creation_price=None,
        markup_pct=markup,
    )

    # Flat path: (1000*0.00001 + 200*0.00003) * 1.10
    expected = (Decimal("1000") * prompt_price + Decimal("200") * completion_price) * (
        Decimal("1") + markup / Decimal("100")
    )
    assert cost == expected, f"all-tiers-zero must match flat path: expected {expected}, got {cost}"


# ---------------------------------------------------------------------------
# PC11 — Recorder persists cache_creation_tokens through the event stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recorder_persists_cache_creation_tokens() -> None:
    """record() reads prompt_tokens_details.cache_creation_tokens → event_fields carries it."""
    import uuid

    from tests.tiered_token_billing.conftest import (
        FakeSession,
        FakeSessionFactory,
        StreamCapture,
    )

    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    snapshot_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="anthropic/claude-3-5-sonnet",
        usage={
            "prompt_tokens": 1000,
            "prompt_tokens_details": {
                "cached_tokens": 0,
                "cache_creation_tokens": 50,
            },
            "completion_tokens": 100,
        },
        status=200,
    )

    evt = stream.last_event
    # cache_creation_tokens must be in the event fields
    assert "cache_creation_tokens" in evt, (
        f"event_fields must carry cache_creation_tokens; got keys: {list(evt.keys())}"
    )
    assert evt["cache_creation_tokens"] == "50", (
        f"cache_creation_tokens must be '50', got: {evt['cache_creation_tokens']!r}"
    )


# ---------------------------------------------------------------------------
# PC12 — REJECT malformed client cache_control (SEAM A)
# ---------------------------------------------------------------------------


def test_reject_malformed_cache_control(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wrong-shape client cc → dropped, WARN 'cache_control_malformed',
    other fields == baseline (no crash).
    """
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _openai_to_anthropic_request,  # type: ignore[attr-defined]
    )

    payload_malformed: dict[str, Any] = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Hello",
                        "cache_control": {"type": 42},  # malformed: type is an int, not str
                    }
                ],
            }
        ],
    }

    # Baseline: same payload WITHOUT cache_control
    payload_baseline: dict[str, Any] = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    with caplog.at_level(logging.WARNING):
        result = _openai_to_anthropic_request(
            payload_malformed,
            default_max_tokens=4096,
            auto_cache=True,
        )
        baseline = _openai_to_anthropic_request(
            payload_baseline,
            default_max_tokens=4096,
            auto_cache=True,  # auto-inject suppressed since client "intended" to send cc
        )

    # Must warn about malformed
    assert "cache_control_malformed" in caplog.text, (
        "must log cache_control_malformed for wrong-shape cache_control"
    )

    # No cache_control should appear in result (dropped)
    result_str = json.dumps(result)
    assert "cache_control" not in result_str, (
        f"malformed cache_control must be dropped entirely, got: {result!r}"
    )

    # PC12 — §2 contract: every non-cache field must be unchanged from the clean baseline.
    # The malformed payload uses list-content form; the baseline uses plain-string form.
    # Normalize both to extract the canonical message text for comparison.
    def _extract_user_texts(translated: dict[str, Any]) -> list[str]:
        """Flatten user message content to a list of text strings (normalises list vs str)."""
        texts: list[str] = []
        for msg in translated.get("messages", []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        texts.append(block.get("text", ""))
        return texts

    assert result.get("model") == baseline.get("model"), (
        f"model must be unchanged: result={result.get('model')!r} baseline={baseline.get('model')!r}"
    )
    assert result.get("max_tokens") == baseline.get("max_tokens"), (
        f"max_tokens must be unchanged: result={result.get('max_tokens')!r} baseline={baseline.get('max_tokens')!r}"
    )
    result_texts = _extract_user_texts(result)
    baseline_texts = _extract_user_texts(baseline)
    assert result_texts == baseline_texts, (
        f"user message text must be identical after dropping malformed cc; "
        f"result_texts={result_texts!r} baseline_texts={baseline_texts!r}"
    )
    # Ensure no spurious extra keys were added to result
    result_keys = set(result.keys()) - {"cache_control"}
    baseline_keys = set(baseline.keys()) - {"cache_control"}
    assert result_keys == baseline_keys, (
        f"result must have same top-level keys as baseline (excluding cache_control): "
        f"result_keys={result_keys!r} baseline_keys={baseline_keys!r}"
    )


# ---------------------------------------------------------------------------
# PC13 — REJECT auto-inject with nothing to anchor (no system + no tools)
# ---------------------------------------------------------------------------


def test_reject_autoinject_nothing_to_anchor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """knob on, a body with no system block and no tools → no cache_control added,
    request identical to baseline, DEBUG 'cache_nothing_to_anchor'.
    """
    from gateway.proxy.infrastructure.anthropic_upstream import (
        _openai_to_anthropic_request,  # type: ignore[attr-defined]
    )

    with caplog.at_level(logging.DEBUG):
        result = _openai_to_anthropic_request(
            _PAYLOAD_NO_SYSTEM_NO_TOOLS,
            default_max_tokens=4096,
            auto_cache=True,
        )

    # Must log the anchor warning
    assert "cache_nothing_to_anchor" in caplog.text, (
        "must log cache_nothing_to_anchor when there is nothing to inject cache_control on"
    )

    # No cache_control must appear anywhere
    result_str = json.dumps(result)
    assert "cache_control" not in result_str, (
        f"no-op auto-inject must not add cache_control anywhere, got: {result!r}"
    )

    # Request must be otherwise identical to baseline
    baseline = _openai_to_anthropic_request(
        _PAYLOAD_NO_SYSTEM_NO_TOOLS,
        default_max_tokens=4096,
        auto_cache=False,
    )
    assert result == baseline, (
        f"nothing-to-anchor must produce byte-identical output to auto_cache=False; "
        f"diff result: {result!r} vs baseline: {baseline!r}"
    )


# ---------------------------------------------------------------------------
# PC14 — NEW: _GeminiSSEStepper.finish surfaces cached content tokens (SEAM C §0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_stream_surfaces_cached_content() -> None:
    """SEAM C: real GeminiCompletionUpstream streaming with usageMetadata carrying
    cachedContentTokenCount=800 → terminal/finish chunk emits
    prompt_tokens_details.cached_tokens==800 and NO cache_creation_tokens (Gemini
    has no write-cache concept on the wire).

    Mirrors PC6 (non-stream) for the streaming path via _GeminiSSEStepper.finish().
    """
    from tests._helios_harness import (
        fake_provider_credential,
        sse_handler,
        wire_mock_transport,
    )

    from gateway.proxy.infrastructure.gemini_upstream import (
        GeminiCompletionUpstream,
    )

    # Two Gemini SSE frames: a content chunk then the final chunk with usageMetadata.
    # Gemini streams native JSON objects (not the OpenAI SSE format) — each "data:" line
    # is a raw Gemini generateContent response dict.
    gemini_cache_stream_frames = [
        b"data: "
        + json.dumps(
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Hello!"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1000,
                    "candidatesTokenCount": 50,
                    "totalTokenCount": 1050,
                    "cachedContentTokenCount": 800,
                },
            }
        ).encode()
        + b"\n\n",
    ]

    adapter = GeminiCompletionUpstream()
    wire_mock_transport(adapter, sse_handler(gemini_cache_stream_frames))

    payload: dict[str, Any] = {
        "model": "google/gemini-2.0-flash",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    }

    terminal_usage: dict[str, Any] | None = None
    with fake_provider_credential("test-key"):
        async for chunk_bytes in adapter.stream(payload):
            if chunk_bytes == b"data: [DONE]\n\n":
                continue
            if chunk_bytes.startswith(b"data: "):
                try:
                    chunk = json.loads(chunk_bytes[6:])
                    if "usage" in chunk:
                        terminal_usage = chunk["usage"]
                except (json.JSONDecodeError, IndexError):
                    continue

    assert terminal_usage is not None, "No terminal usage chunk found in Gemini stream"

    # cachedContentTokenCount=800 must surface as cached_tokens
    ptd = terminal_usage.get("prompt_tokens_details", {})
    assert ptd.get("cached_tokens") == 800, (
        f"terminal usage must have prompt_tokens_details.cached_tokens==800 "
        f"(from cachedContentTokenCount), got: {ptd!r}"
    )

    # Gemini has no write-cache wire field — cache_creation_tokens must NOT appear
    assert "cache_creation_tokens" not in ptd, (
        f"Gemini terminal usage must NOT have cache_creation_tokens (no write-cache concept), "
        f"got: {ptd!r}"
    )

    # prompt_tokens must equal the full promptTokenCount (Gemini already includes cached)
    assert terminal_usage.get("prompt_tokens") == 1000, (
        f"terminal usage prompt_tokens must be 1000 (Gemini promptTokenCount), "
        f"got: {terminal_usage.get('prompt_tokens')!r}"
    )
