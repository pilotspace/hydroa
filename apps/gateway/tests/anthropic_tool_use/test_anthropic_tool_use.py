"""Red suite for anthropic-tool-use (v10 task 2/4) — TASK.md §4.

Extends AnthropicCompletionUpstream's pure v9 translation helpers so OpenAI
tools/tool_choice/tool_calls/role:"tool" map to/from the Anthropic Messages tool
shape (tools+tool_choice, tool_use blocks, tool_result blocks) on request,
response, and streaming paths — using the FROZEN tool-use-contract helpers.

Contract: anthropic-tool-use TASK.md §3 (FROZEN @ v1).
These assertions are RED until BUILD extends the helpers (the helpers already exist
from v9 but ignore tools, so the suite fails on behavior, not import).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.proxy.infrastructure.anthropic_upstream import (
    _anthropic_to_openai,
    _openai_to_anthropic_request,
    _translate_anthropic_sse,
)

_DMT = 4096  # default_max_tokens


def _req(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}
    base.update(extra)
    return base


# ── Request: tools -> input_schema ────────────────────────────────────────────


def test_tools_to_input_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]
    out = _openai_to_anthropic_request(_req(tools=tools), default_max_tokens=_DMT)
    assert out["tools"] == [
        {
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("none", {"type": "none"}),
        ({"type": "function", "function": {"name": "get_weather"}}, {"type": "tool", "name": "get_weather"}),
    ],
)
def test_tool_choice_mapping(choice: Any, expected: dict[str, Any]) -> None:
    out = _openai_to_anthropic_request(_req(tool_choice=choice), default_max_tokens=_DMT)
    assert out["tool_choice"] == expected


# ── Request: assistant tool_calls -> tool_use blocks ─────────────────────────


def test_assistant_tool_calls_to_tool_use() -> None:
    messages = [
        {"role": "user", "content": "weather in Paris?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                }
            ],
        },
    ]
    out = _openai_to_anthropic_request(
        {"model": "claude-x", "messages": messages}, default_max_tokens=_DMT
    )
    asst = out["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}
    ]


def test_consecutive_tool_msgs_merge() -> None:
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "call_2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "res1"},
        {"role": "tool", "tool_call_id": "call_2", "content": "res2"},
    ]
    out = _openai_to_anthropic_request(
        {"model": "claude-x", "messages": messages}, default_max_tokens=_DMT
    )
    # the two tool results collapse into ONE trailing user message, order preserved
    user_result = out["messages"][-1]
    assert user_result["role"] == "user"
    assert user_result["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "res1"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "res2"},
    ]


def test_string_content_unchanged() -> None:
    out = _openai_to_anthropic_request(
        {"model": "claude-x", "messages": [{"role": "user", "content": "hello"}]},
        default_max_tokens=_DMT,
    )
    assert out["messages"][0]["content"] == "hello"


def test_tool_msg_missing_id_rejected() -> None:
    messages = [{"role": "tool", "content": "orphan result"}]
    with pytest.raises(ValueError) as exc:
        _openai_to_anthropic_request({"model": "claude-x", "messages": messages}, default_max_tokens=_DMT)
    assert "tool_call_id_required" in str(exc.value)


def test_no_tools_request_byte_identical_v9() -> None:
    # without tools, the body carries no tool fields (v9-identical shape)
    out = _openai_to_anthropic_request(_req(), default_max_tokens=_DMT)
    assert "tools" not in out
    assert "tool_choice" not in out
    assert out["messages"] == [{"role": "user", "content": "hi"}]


# ── Response: tool_use -> tool_calls ──────────────────────────────────────────


def test_tool_use_response_to_openai() -> None:
    body = {
        "id": "msg_1",
        "model": "claude-x",
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}}
        ],
        "usage": {"input_tokens": 7, "output_tokens": 4},
    }
    out = _anthropic_to_openai(body)
    msg = out["choices"][0]["message"]
    assert msg["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
        }
    ]
    assert msg["content"] is None
    assert out["choices"][0]["finish_reason"] == "tool_calls"


def test_text_and_tool_use_response() -> None:
    body = {
        "id": "msg_2",
        "model": "claude-x",
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Let me check. "},
            {"type": "tool_use", "id": "toolu_2", "name": "get_weather", "input": {"city": "Rome"}},
        ],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    out = _anthropic_to_openai(body)
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Let me check. "
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"


# ── Streaming: tool_use -> delta.tool_calls fragments ─────────────────────────


def _collect(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for raw in _translate_anthropic_sse(events):
        line = raw.decode().strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload and payload != "[DONE]":
                chunks.append(json.loads(payload))
    return chunks


def test_streaming_tool_call_fragments() -> None:
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"message": {"id": "msg_s", "model": "claude-x", "usage": {"input_tokens": 7}}}),
        ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "get_weather"}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"city":'}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}}),
        ("content_block_stop", {"index": 0}),
        ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 4}}),
        ("message_stop", {}),
    ]
    chunks = _collect(events)
    # find the tool_calls fragments
    tool_frags = [
        c["choices"][0]["delta"]["tool_calls"][0]
        for c in chunks
        if c["choices"][0]["delta"].get("tool_calls")
    ]
    assert tool_frags, "expected delta.tool_calls fragments"
    # first fragment carries id + name
    assert tool_frags[0]["index"] == 0
    assert tool_frags[0]["id"] == "toolu_9"
    assert tool_frags[0]["function"]["name"] == "get_weather"
    # later fragments carry argument string fragments
    arg_frags = "".join(
        f["function"].get("arguments", "") for f in tool_frags if "arguments" in f["function"]
    )
    assert arg_frags == '{"city":"Paris"}'
    # terminal usage chunk present before [DONE]
    assert chunks[-1].get("usage") == {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
