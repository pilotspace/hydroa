"""Tests for STT passthrough field coverage.

RED: timestamp_granularities and chunking_strategy were not in _STT_PASSTHROUGH_FIELDS
and were silently dropped by the proxy, preventing clients from getting word/segment-
level timestamps or controlling audio chunking behavior.
"""

from __future__ import annotations

from gateway.proxy.application.audio_use_case import _STT_PASSTHROUGH_FIELDS


def test_timestamp_granularities_in_passthrough_fields() -> None:
    """timestamp_granularities must be forwarded to the upstream STT endpoint."""
    assert "timestamp_granularities" in _STT_PASSTHROUGH_FIELDS, (
        f"timestamp_granularities not in _STT_PASSTHROUGH_FIELDS: {_STT_PASSTHROUGH_FIELDS!r}"
    )


def test_chunking_strategy_in_passthrough_fields() -> None:
    """chunking_strategy must be forwarded to the upstream STT endpoint."""
    assert "chunking_strategy" in _STT_PASSTHROUGH_FIELDS, (
        f"chunking_strategy not in _STT_PASSTHROUGH_FIELDS: {_STT_PASSTHROUGH_FIELDS!r}"
    )


# Regression guard — existing fields must remain
def test_existing_passthrough_fields_unchanged() -> None:
    for field in ("language", "prompt", "response_format", "temperature"):
        assert field in _STT_PASSTHROUGH_FIELDS, (
            f"Existing field {field!r} missing from _STT_PASSTHROUGH_FIELDS after update"
        )
