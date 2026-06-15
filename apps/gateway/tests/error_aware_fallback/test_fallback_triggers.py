"""RED suite — the pure FALLBACK-TRIGGER TAXONOMY classifier (v19 task 2 §3).

classify_fallback_trigger(status, body) -> "context_window" | "content_policy" | None
is PURE, TOTAL, and NEVER raises. These tests fail with a clear RED message until
proxy/application/fallback_triggers.py is built.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    from gateway.proxy.application.fallback_triggers import classify_fallback_trigger  # noqa: F401

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _classify(status: int, body: Any) -> str | None:
    if not _AVAILABLE:
        pytest.fail(
            "RED: fallback_triggers.classify_fallback_trigger not yet implemented — build pending"
        )
    from gateway.proxy.application.fallback_triggers import classify_fallback_trigger

    return classify_fallback_trigger(status, body)


# --- context_window -------------------------------------------------------


def test_context_length_exceeded_code() -> None:
    assert _classify(400, {"error": {"code": "context_length_exceeded"}}) == "context_window"


def test_anthropic_too_long_message() -> None:
    body = {
        "error": {
            "code": "invalid_request_error",
            "message": "prompt is too long: 200000 tokens > 100000 maximum",
        }
    }
    assert _classify(400, body) == "context_window"


def test_gemini_token_count_message() -> None:
    body = {
        "error": {
            "code": "invalid_argument",
            "message": "The input token count exceeds the maximum number of tokens allowed",
        }
    }
    assert _classify(400, body) == "context_window"


# --- content_policy -------------------------------------------------------


def test_content_filter_code() -> None:
    assert _classify(400, {"error": {"code": "content_filter"}}) == "content_policy"


def test_safety_message() -> None:
    # OpenAI's real prompt-block message (no canonical code on the 4xx).
    body = {
        "error": {
            "type": "invalid_request_error",
            "message": "Your request was rejected as a result of our safety system.",
        }
    }
    assert _classify(400, body) == "content_policy"


def test_azure_content_management_message() -> None:
    body = {
        "error": {
            "message": "The response was filtered due to triggering Azure OpenAI's content management policy."
        }
    }
    assert _classify(400, body) == "content_policy"


def test_context_precedence_over_policy() -> None:
    # A body matching BOTH context-window and content-policy resolves to context_window.
    body = {
        "error": {
            "code": "content_filter",
            "message": "prompt is too long and blocked by safety policy",
        }
    }
    assert _classify(400, body) == "context_window"


# --- None paths -----------------------------------------------------------


def test_2xx_returns_none() -> None:
    assert _classify(200, {"choices": [{"finish_reason": "content_filter"}]}) is None


def test_5xx_returns_none() -> None:
    assert _classify(503, {"error": {"code": "context_length_exceeded"}}) is None


def test_non_matching_4xx_returns_none() -> None:
    assert _classify(401, {"error": {"code": "invalid_api_key"}}) is None


def test_retry_domain_429_not_classified() -> None:
    # 429 is the retry seam's domain (§1 REJECT: "already retry-handled") — never a fallover
    # trigger, even if the body text incidentally matches a policy/context pattern.
    assert _classify(429, {"error": {"code": "content_filter"}}) is None
    assert _classify(429, {"error": {"message": "rate limit exceeded"}}) is None


def test_retry_domain_408_not_classified() -> None:
    assert _classify(408, {"error": {"message": "prompt is too long"}}) is None


def test_generic_field_too_long_not_context() -> None:
    # A generic validation 400 must NOT misfire as context-window (precision over recall).
    assert _classify(400, {"error": {"message": "Field 'name' too long (max 64 chars)"}}) is None


def test_generic_blocked_by_firewall_not_policy() -> None:
    assert _classify(400, {"error": {"message": "Request blocked by firewall"}}) is None
    assert _classify(400, {"error": {"message": "safety valve parameter invalid"}}) is None


def test_malformed_body_returns_none() -> None:
    assert _classify(400, {}) is None
    assert _classify(400, {"error": "a string, not a dict"}) is None
    assert _classify(400, {"error": {}}) is None


def test_never_raises_for_odd_inputs() -> None:
    odd_inputs: list[Any] = [
        None,
        [],
        "string",
        42,
        {"error": None},
        {"error": {"code": None, "message": None, "type": None}},
        {"error": {"code": 123}},
        {"nested": {"error": {"code": "context_length_exceeded"}}},
    ]
    for body in odd_inputs:
        # Must not raise; must return None (unclassifiable → fail-safe passthrough).
        assert _classify(400, body) is None
