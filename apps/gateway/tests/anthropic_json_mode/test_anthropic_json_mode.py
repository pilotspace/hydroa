"""Red suite for anthropic-json-mode (v11 task 3/4) — TASK.md §4.

Anthropic has NO native response_format field, so json_schema is satisfied by the
v10 tool seam: a synthetic forced "json_output" tool (input_schema = the requested
schema) whose tool_use is UNWRAPPED back into message.content on the return leg
(non-stream + streaming). json_object uses a system-instruction strategy. Extends 3
helpers (request append/force · response unwrap · streaming unwrap).

Contract: anthropic-json-mode TASK.md §3 (FROZEN @ v1). Builds on the frozen
response-format-contract helpers.

RED until BUILD wires the contract helpers into the 3 anthropic helpers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.proxy.domain.response_format_translation import JSON_COERCION_TOOL_NAME
from gateway.proxy.infrastructure.anthropic_upstream import (
    _anthropic_to_openai,
    _openai_to_anthropic_request,
    _translate_anthropic_sse,
)

_DMT = 4096
_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}
_RF_SCHEMA = {"type": "json_schema", "json_schema": {"name": "weather", "schema": _SCHEMA}}


def _req(messages: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "claude-x",
        "messages": messages or [{"role": "user", "content": "weather in Paris?"}],
    }
    base.update(extra)
    return base


# ── Request ───────────────────────────────────────────────────────────────────


def test_json_schema_appends_forced_coercion_tool() -> None:
    out = _openai_to_anthropic_request(_req(response_format=_RF_SCHEMA), default_max_tokens=_DMT)
    names = [t["name"] for t in out["tools"]]
    assert JSON_COERCION_TOOL_NAME in names
    coercion = next(t for t in out["tools"] if t["name"] == JSON_COERCION_TOOL_NAME)
    assert coercion["input_schema"] == _SCHEMA
    assert out["tool_choice"] == {"type": "tool", "name": JSON_COERCION_TOOL_NAME}


def test_json_object_appends_system_instruction() -> None:
    out = _openai_to_anthropic_request(
        _req(response_format={"type": "json_object"}), default_max_tokens=_DMT
    )
    assert "json" in out.get("system", "").lower()
    assert "tools" not in out  # no schema → no coercion tool


def test_json_schema_composes_with_caller_tools() -> None:
    tools = [
        {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}
    ]
    out = _openai_to_anthropic_request(
        _req(tools=tools, response_format=_RF_SCHEMA), default_max_tokens=_DMT
    )
    names = [t["name"] for t in out["tools"]]
    assert "get_weather" in names and JSON_COERCION_TOOL_NAME in names  # both present


def test_no_response_format_byte_identical() -> None:
    out = _openai_to_anthropic_request(_req(), default_max_tokens=_DMT)
    assert "tools" not in out
    assert "tool_choice" not in out


def test_unsupported_type_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_UNSUPPORTED_RESPONSE_FORMAT"):
        _openai_to_anthropic_request(_req(response_format={"type": "toml"}), default_max_tokens=_DMT)


def test_json_schema_missing_schema_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_INVALID_JSON_SCHEMA"):
        _openai_to_anthropic_request(
            _req(response_format={"type": "json_schema", "json_schema": {"name": "w"}}),
            default_max_tokens=_DMT,
        )


def test_caller_tool_reserved_name_rejected() -> None:
    tools = [{"type": "function", "function": {"name": JSON_COERCION_TOOL_NAME}}]
    with pytest.raises(ValueError, match="ERR_RESERVED_TOOL_NAME"):
        _openai_to_anthropic_request(
            _req(tools=tools, response_format=_RF_SCHEMA), default_max_tokens=_DMT
        )


# ── Response: coercion tool_use → message.content (unwrap) ─────────────────────


def test_coercion_tool_use_unwraps_to_content() -> None:
    body = {
        "id": "msg_1",
        "model": "claude-x",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": JSON_COERCION_TOOL_NAME, "input": {"city": "Paris"}}
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 7, "output_tokens": 4},
    }
    out = _anthropic_to_openai(body)
    msg = out["choices"][0]["message"]
    assert json.loads(msg["content"]) == {"city": "Paris"}  # JSON string content
    assert "tool_calls" not in msg  # NOT leaked as a tool call
    assert out["choices"][0]["finish_reason"] == "stop"


def test_coercion_composes_with_caller_tool_call() -> None:
    body = {
        "id": "msg_2",
        "model": "claude-x",
        "content": [
            {"type": "tool_use", "id": "t_json", "name": JSON_COERCION_TOOL_NAME, "input": {"city": "Paris"}},
            {"type": "tool_use", "id": "t_w", "name": "get_weather", "input": {"q": "Paris"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 7, "output_tokens": 4},
    }
    out = _anthropic_to_openai(body)
    msg = out["choices"][0]["message"]
    assert json.loads(msg["content"]) == {"city": "Paris"}  # coercion unwrapped to content
    assert [tc["function"]["name"] for tc in msg["tool_calls"]] == ["get_weather"]  # caller tool only


# ── Streaming: coercion block → delta.content (not tool_calls) ─────────────────


def _collect(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for raw in _translate_anthropic_sse(events):
        line = raw.decode().strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload and payload != "[DONE]":
                chunks.append(json.loads(payload))
    return chunks


def test_streaming_coercion_block_streams_as_content() -> None:
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"message": {"id": "msg_s", "model": "claude-x", "usage": {"input_tokens": 7}}}),
        ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "tj", "name": JSON_COERCION_TOOL_NAME}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"city":'}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}}),
        ("content_block_stop", {"index": 0}),
        ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 4}}),
        ("message_stop", {}),
    ]
    chunks = _collect(events)
    # the partial JSON streams as delta.content, NOT delta.tool_calls
    content = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    )
    assert content == '{"city":"Paris"}'
    assert not any(c["choices"][0]["delta"].get("tool_calls") for c in chunks)
    # terminal finish_reason is "stop" (coercion, not a real tool call)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1].get("usage") == {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}
