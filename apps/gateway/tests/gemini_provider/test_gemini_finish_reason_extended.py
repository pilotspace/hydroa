"""Tests for extended Gemini finishReason mapping (content-policy codes).

RED: BLOCKLIST, PROHIBITED_CONTENT, SPII, IMAGE_SAFETY were falling through to
"stop", causing clients to misread policy-blocked responses as normal completions.
"""

from __future__ import annotations

from gateway.proxy.infrastructure.gemini_upstream import _map_gemini_finish_reason


def test_blocklist_maps_to_content_filter() -> None:
    assert _map_gemini_finish_reason("BLOCKLIST") == "content_filter"


def test_prohibited_content_maps_to_content_filter() -> None:
    assert _map_gemini_finish_reason("PROHIBITED_CONTENT") == "content_filter"


def test_spii_maps_to_content_filter() -> None:
    assert _map_gemini_finish_reason("SPII") == "content_filter"


def test_image_safety_maps_to_content_filter() -> None:
    assert _map_gemini_finish_reason("IMAGE_SAFETY") == "content_filter"


# Regression guard — previously mapped values must be unchanged
def test_existing_mappings_unchanged() -> None:
    assert _map_gemini_finish_reason("STOP") == "stop"
    assert _map_gemini_finish_reason("MAX_TOKENS") == "length"
    assert _map_gemini_finish_reason("SAFETY") == "content_filter"
    assert _map_gemini_finish_reason("RECITATION") == "stop"
    assert _map_gemini_finish_reason(None) == "stop"
    assert _map_gemini_finish_reason("UNKNOWN_FUTURE") == "stop"
