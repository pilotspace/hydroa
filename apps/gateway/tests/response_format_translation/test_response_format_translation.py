"""Red suite for response-format-contract (v11 task 1/4) — TASK.md §4.

Freezes the canonical OpenAI<->native response_format seam every provider chat
translator imports: the TypedDict vocabulary (text/json_object/json_schema), the
Anthropic JSON-schema tool-COERCION builder (a synthetic forced tool whose
parameters ARE the requested schema), the tool_use->content UNWRAP helper, and a
raw-dict extractor with the no-op fast path. Plus regression pins that
response_format flows through the v9/v10 dispatch seam UNSTRIPPED and a
no-response_format request stays byte-identical to v10.

Contract: TASK.md §3 (FROZEN @ v1).
  - extract_response_format(payload) -> ResponseFormat | None; None for absent or
    {type:"text"}; raises ERR_UNSUPPORTED_RESPONSE_FORMAT / ERR_INVALID_JSON_SCHEMA.
  - build_json_coercion_tool(rf, *, existing_tool_names) -> (Tool, ToolChoiceNamed);
    canonical OpenAI Tool named JSON_COERCION_TOOL_NAME, parameters == schema;
    raises ERR_RESERVED_TOOL_NAME on a caller-name collision.
  - is_coercion_tool_call(name) -> bool; unwrap_coerced_tool_input(value) -> JSON str.

These imports are RED until BUILD creates
gateway/proxy/domain/response_format_translation.py.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from gateway.proxy.domain.response_format_translation import (
    JSON_COERCION_TOOL_NAME,
    build_json_coercion_tool,
    extract_response_format,
    is_coercion_tool_call,
    unwrap_coerced_tool_input,
)

# ---------------------------------------------------------------------------
# Canonical fixtures
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "temp": {"type": "number"}},
    "required": ["city"],
}
_RF_JSON_SCHEMA = {"type": "json_schema", "json_schema": {"name": "weather", "schema": _SCHEMA}}
_RF_JSON_OBJECT = {"type": "json_object"}
_RF_TEXT = {"type": "text"}


# ---------------------------------------------------------------------------
# extract_response_format — the no-op fast path + validation rejections
# ---------------------------------------------------------------------------


def test_extract_absent_returns_none() -> None:
    assert extract_response_format({"model": "m", "messages": []}) is None


def test_extract_text_type_is_noop_none() -> None:
    # {type:"text"} is the default; it must engage ZERO plumbing (byte-identical v10).
    assert extract_response_format({"response_format": _RF_TEXT}) is None


def test_extract_json_object_returned() -> None:
    rf = extract_response_format({"response_format": _RF_JSON_OBJECT})
    assert rf is not None
    assert rf["type"] == "json_object"


def test_extract_json_schema_returned() -> None:
    rf = extract_response_format({"response_format": _RF_JSON_SCHEMA})
    assert rf is not None
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == _SCHEMA


def test_extract_unsupported_type_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_UNSUPPORTED_RESPONSE_FORMAT"):
        extract_response_format({"response_format": {"type": "xml"}})


def test_extract_json_schema_missing_schema_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_INVALID_JSON_SCHEMA"):
        extract_response_format({"response_format": {"type": "json_schema", "json_schema": {"name": "w"}}})


# ---------------------------------------------------------------------------
# build_json_coercion_tool — synthetic forced tool + composition rule
# ---------------------------------------------------------------------------


def test_build_coercion_tool_shape() -> None:
    rf = extract_response_format({"response_format": _RF_JSON_SCHEMA})
    assert rf is not None
    tool, choice = build_json_coercion_tool(rf, existing_tool_names=())
    assert tool["type"] == "function"
    assert tool["function"]["name"] == JSON_COERCION_TOOL_NAME
    assert tool["function"]["parameters"] == _SCHEMA  # input schema IS the requested schema
    assert choice == {"type": "function", "function": {"name": JSON_COERCION_TOOL_NAME}}


def test_build_coercion_tool_distinct_from_caller_tools() -> None:
    rf = extract_response_format({"response_format": _RF_JSON_SCHEMA})
    assert rf is not None
    tool, _ = build_json_coercion_tool(rf, existing_tool_names=("get_weather",))
    # composes alongside caller tools — distinct reserved name, no collision
    assert tool["function"]["name"] != "get_weather"
    assert tool["function"]["name"] == JSON_COERCION_TOOL_NAME


def test_build_coercion_tool_reserved_name_collision_rejected() -> None:
    rf = extract_response_format({"response_format": _RF_JSON_SCHEMA})
    assert rf is not None
    with pytest.raises(ValueError, match="ERR_RESERVED_TOOL_NAME"):
        build_json_coercion_tool(rf, existing_tool_names=(JSON_COERCION_TOOL_NAME,))


# ---------------------------------------------------------------------------
# unwrap + classify
# ---------------------------------------------------------------------------


def test_unwrap_coerced_input_to_json_string() -> None:
    out = unwrap_coerced_tool_input({"city": "Paris", "temp": 18})
    assert json.loads(out) == {"city": "Paris", "temp": 18}


def test_unwrap_failsafe_never_raises() -> None:
    class _Weird:
        def __str__(self) -> str:
            return "weird"

    # fail-safe (reuses dump_tool_arguments): odd input never raises
    assert unwrap_coerced_tool_input(_Weird()) == "weird"


def test_is_coercion_tool_call() -> None:
    assert is_coercion_tool_call(JSON_COERCION_TOOL_NAME) is True
    assert is_coercion_tool_call("get_weather") is False


# ---------------------------------------------------------------------------
# Request passthrough invariant — response_format UNSTRIPPED through dispatch
# (regression-pinned through the v9/v10 dispatch seam with a spy adapter;
#  GREEN-BY-DESIGN — these pass against UNCHANGED dispatch code)
# ---------------------------------------------------------------------------


class _SpyAdapter:
    """CompletionUpstream spy: records the exact payload it receives."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.complete_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        self.complete_calls.append(payload)
        return 200, {"_served_by": self.name, "model": payload.get("model")}

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        self.stream_calls.append(payload)

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: [DONE]\n\n"

        return _gen()


class _ScriptedResolver:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def provider_for(self, model_id: str) -> str:
        return self._mapping.get(model_id, "openrouter")


pytestmark = pytest.mark.asyncio


async def test_request_passthrough_response_format_unstripped() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("anthropic")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": _SpyAdapter("openrouter"), "anthropic": spy},
        resolver=_ScriptedResolver({"claude-x": "anthropic"}),
    )
    body = {
        "model": "claude-x",
        "messages": [{"role": "user", "content": "weather?"}],
        "response_format": _RF_JSON_SCHEMA,
    }
    await wrapper.complete(body)
    received = spy.complete_calls[0]
    assert received["response_format"] == _RF_JSON_SCHEMA  # survives dispatch unchanged


async def test_openrouter_response_format_byte_identical() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("openrouter")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": spy},
        resolver=_ScriptedResolver({}),  # unknown -> openrouter (default)
    )
    body = {
        "model": "or-model",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": _RF_JSON_OBJECT,
    }
    await wrapper.complete(body)
    # openrouter adapter receives the body verbatim — no response_format translation
    assert spy.complete_calls[0] == body


async def test_no_response_format_byte_identical_v10() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("openrouter")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": spy},
        resolver=_ScriptedResolver({}),
    )
    body = {"model": "or-model", "messages": [{"role": "user", "content": "hi"}]}
    await wrapper.complete(body)
    received = spy.complete_calls[0]
    assert "response_format" not in received  # zero json plumbing engaged
