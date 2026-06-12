"""Red suite for gemini-tool-use (v10 task 3/4) — TASK.md §4.

Extends GeminiCompletionUpstream's pure v9 chat helpers so OpenAI
tools/tool_choice/tool_calls/role:"tool" map to/from the Gemini generateContent tool
shape (functionDeclarations, toolConfig, functionCall parts, functionResponse parts)
— using the FROZEN tool-use-contract helpers. The id-less functionCall gets a
synthesized id; the follow-up tool result is name-correlated via the assistant
tool_calls in the same request.

Contract: gemini-tool-use TASK.md §3 (FROZEN @ v1).
RED until BUILD extends the helpers (they exist from v9 but ignore tools → the suite
fails on behavior, not import).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.proxy.domain.tool_translation import synthesize_tool_call_id
from gateway.proxy.infrastructure.gemini_upstream import (
    _gemini_to_openai,
    _openai_to_gemini_request,
    _translate_gemini_sse,
)

_DMT = 4096


def _req(messages: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "gemini-x",
        "messages": messages or [{"role": "user", "content": "hi"}],
    }
    base.update(extra)
    return base


# ── Request: tools -> functionDeclarations ────────────────────────────────────


def test_tools_to_function_declarations() -> None:
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
    out = _openai_to_gemini_request(_req(tools=tools), default_max_tokens=_DMT)
    assert out["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Get the weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                }
            ]
        }
    ]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("auto", {"mode": "AUTO"}),
        ("required", {"mode": "ANY"}),
        ("none", {"mode": "NONE"}),
        (
            {"type": "function", "function": {"name": "x"}},
            {"mode": "ANY", "allowedFunctionNames": ["x"]},
        ),
    ],
)
def test_tool_choice_mapping(choice: Any, expected: dict[str, Any]) -> None:
    out = _openai_to_gemini_request(_req(tool_choice=choice), default_max_tokens=_DMT)
    assert out["toolConfig"]["functionCallingConfig"] == expected


def test_assistant_tool_calls_to_function_call() -> None:
    messages = [
        {"role": "user", "content": "weather?"},
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
    out = _openai_to_gemini_request(_req(messages), default_max_tokens=_DMT)
    model_content = out["contents"][1]
    assert model_content["role"] == "model"
    assert model_content["parts"] == [
        {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}
    ]


def test_tool_result_name_correlated() -> None:
    messages = [
        {"role": "user", "content": "weather?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]
    out = _openai_to_gemini_request(_req(messages), default_max_tokens=_DMT)
    tool_content = out["contents"][-1]
    assert tool_content["role"] == "user"
    assert tool_content["parts"] == [
        {"functionResponse": {"name": "get_weather", "response": {"result": "sunny"}}}
    ]


def test_no_tools_request_byte_identical_v9() -> None:
    out = _openai_to_gemini_request(_req(), default_max_tokens=_DMT)
    assert "tools" not in out
    assert "toolConfig" not in out
    assert out["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_tool_msg_missing_id_rejected() -> None:
    messages = [{"role": "tool", "content": "orphan"}]
    with pytest.raises(ValueError) as exc:
        _openai_to_gemini_request(_req(messages), default_max_tokens=_DMT)
    assert "tool_call_id_required" in str(exc.value)


# ── Response: functionCall -> tool_calls ──────────────────────────────────────


def test_function_call_response_to_openai() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
    }
    out = _gemini_to_openai(body, model="gemini-x")
    msg = out["choices"][0]["message"]
    assert msg["tool_calls"] == [
        {
            "id": synthesize_tool_call_id("get_weather", 0),
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
        }
    ]
    assert msg["content"] is None
    assert out["choices"][0]["finish_reason"] == "tool_calls"


# ── Streaming: functionCall -> one combined delta.tool_calls fragment ──────────


def _collect(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in _translate_gemini_sse(chunks):
        line = raw.decode().strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload and payload != "[DONE]":
                out.append(json.loads(payload))
    return out


def test_streaming_function_call_fragment() -> None:
    chunks = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
        }
    ]
    out = _collect(chunks)
    tool_frags = [
        c["choices"][0]["delta"]["tool_calls"][0]
        for c in out
        if c["choices"][0]["delta"].get("tool_calls")
    ]
    assert len(tool_frags) == 1
    frag = tool_frags[0]
    assert frag["index"] == 0
    assert frag["id"] == synthesize_tool_call_id("get_weather", 0)
    assert frag["function"]["name"] == "get_weather"
    assert json.loads(frag["function"]["arguments"]) == {"city": "Paris"}
    # terminal usage chunk carries finish_reason tool_calls
    assert out[-1].get("usage") == {"prompt_tokens": 9, "completion_tokens": 6, "total_tokens": 15}
    assert out[-1]["choices"][0]["finish_reason"] == "tool_calls"
