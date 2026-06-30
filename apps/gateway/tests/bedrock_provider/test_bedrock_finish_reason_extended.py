"""Tests for extended Bedrock stopReason mapping.

RED: model_context_window_exceeded was not in the mapping, falling through to
"stop" instead of "length" (same semantic as max_tokens — context exceeded).
"""

from __future__ import annotations

from gateway.proxy.infrastructure.bedrock_upstream import _map_finish_reason


def test_model_context_window_exceeded_maps_to_length() -> None:
    assert _map_finish_reason("model_context_window_exceeded") == "length"


# Regression guard — previously mapped values must be unchanged
def test_existing_mappings_unchanged() -> None:
    assert _map_finish_reason("end_turn") == "stop"
    assert _map_finish_reason("max_tokens") == "length"
    assert _map_finish_reason("stop_sequence") == "stop"
    assert _map_finish_reason("tool_use") == "tool_calls"
    assert _map_finish_reason("content_filtered") == "content_filter"
    assert _map_finish_reason("guardrail_intervened") == "content_filter"
    assert _map_finish_reason(None) == "stop"
    assert _map_finish_reason("unknown_future") == "stop"


# malformed_model_output / malformed_tool_use remain "stop" — they are Bedrock-specific
# model errors with no clean OpenAI equivalent; "stop" is the least misleading default.
def test_malformed_codes_default_to_stop() -> None:
    assert _map_finish_reason("malformed_model_output") == "stop"
    assert _map_finish_reason("malformed_tool_use") == "stop"
