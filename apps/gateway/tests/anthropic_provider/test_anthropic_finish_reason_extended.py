"""Tests for extended Anthropic stop_reason mapping.

RED: "refusal" was falling through to "stop" instead of "content_filter".
pause_turn (server tool pause) has no OpenAI equivalent and correctly falls
through to "stop" — tested here as a regression/documentation guard.
"""

from __future__ import annotations

from gateway.proxy.infrastructure.anthropic_upstream import _map_finish_reason


def test_refusal_maps_to_content_filter() -> None:
    """refusal indicates model declined due to content policy → content_filter."""
    assert _map_finish_reason("refusal") == "content_filter"


def test_pause_turn_defaults_to_stop() -> None:
    """pause_turn (server tool pause) has no OpenAI equivalent; falls through to "stop".

    Documented here so it is explicit policy, not an accidental miss.
    """
    assert _map_finish_reason("pause_turn") == "stop"


# Regression guard — previously mapped values must be unchanged
def test_existing_mappings_unchanged() -> None:
    assert _map_finish_reason("end_turn") == "stop"
    assert _map_finish_reason("max_tokens") == "length"
    assert _map_finish_reason("stop_sequence") == "stop"
    assert _map_finish_reason("tool_use") == "tool_calls"
    assert _map_finish_reason(None) == "stop"
    assert _map_finish_reason("something_new") == "stop"
