"""Red suite for gemini-json-mode (v11 task 2/4) — TASK.md §4.

Extends GeminiCompletionUpstream's v9 request helper so a provider=google chat
request carrying response_format produces NATIVE structured output via
generationConfig.responseMimeType (+ responseSchema for json_schema). REQUEST-SIDE
ONLY — the response/SSE path already returns JSON as message.content (v9), so two
tests pin that UNCHANGED behavior (green-by-design) and one pins no-rf byte-identical.

Contract: gemini-json-mode TASK.md §3 (FROZEN @ v1). Builds on the frozen
response-format-contract (extract_response_format).

RED until BUILD wires extract_response_format into _openai_to_gemini_request.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.proxy.infrastructure.gemini_upstream import (
    _gemini_to_openai,
    _openai_to_gemini_request,
)

_DMT = 4096
_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "temp": {"type": "number"}},
    "required": ["city"],
}


def _req(messages: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "gemini-x",
        "messages": messages or [{"role": "user", "content": "hi"}],
    }
    base.update(extra)
    return base


# ── Request: response_format -> generationConfig ──────────────────────────────


def test_json_object_sets_response_mime_type() -> None:
    out = _openai_to_gemini_request(
        _req(response_format={"type": "json_object"}), default_max_tokens=_DMT
    )
    assert out["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" not in out["generationConfig"]


def test_json_schema_sets_mime_type_and_schema() -> None:
    out = _openai_to_gemini_request(
        _req(response_format={"type": "json_schema", "json_schema": {"name": "w", "schema": _SCHEMA}}),
        default_max_tokens=_DMT,
    )
    assert out["generationConfig"]["responseMimeType"] == "application/json"
    assert out["generationConfig"]["responseSchema"] == _SCHEMA  # forwarded as-is


def test_composes_with_tools() -> None:
    tools = [
        {
            "type": "function",
            "function": {"name": "get_weather", "parameters": {"type": "object"}},
        }
    ]
    out = _openai_to_gemini_request(
        _req(
            tools=tools,
            response_format={"type": "json_schema", "json_schema": {"name": "w", "schema": _SCHEMA}},
        ),
        default_max_tokens=_DMT,
    )
    assert "tools" in out  # v10 tools still present
    assert out["generationConfig"]["responseSchema"] == _SCHEMA  # rf composes alongside


def test_no_response_format_byte_identical() -> None:
    out = _openai_to_gemini_request(_req(), default_max_tokens=_DMT)
    assert "responseMimeType" not in out["generationConfig"]
    assert "responseSchema" not in out["generationConfig"]


def test_text_type_is_noop() -> None:
    out = _openai_to_gemini_request(
        _req(response_format={"type": "text"}), default_max_tokens=_DMT
    )
    assert "responseMimeType" not in out["generationConfig"]


def test_unsupported_type_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_UNSUPPORTED_RESPONSE_FORMAT"):
        _openai_to_gemini_request(_req(response_format={"type": "yaml"}), default_max_tokens=_DMT)


def test_json_schema_missing_schema_rejected() -> None:
    with pytest.raises(ValueError, match="ERR_INVALID_JSON_SCHEMA"):
        _openai_to_gemini_request(
            _req(response_format={"type": "json_schema", "json_schema": {"name": "w"}}),
            default_max_tokens=_DMT,
        )


# ── Response: JSON text -> message.content (UNCHANGED v9 path, green-by-design) ─


def test_json_text_response_maps_to_content() -> None:
    json_answer = json.dumps({"city": "Paris", "temp": 18})
    body = {
        "candidates": [
            {"content": {"parts": [{"text": json_answer}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
    }
    out = _gemini_to_openai(body, model="gemini-x")
    msg = out["choices"][0]["message"]
    assert msg["content"] == json_answer  # the JSON string surfaces verbatim
    assert out["choices"][0]["finish_reason"] == "stop"
