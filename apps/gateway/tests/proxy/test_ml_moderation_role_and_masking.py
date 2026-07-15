"""Red/green regression suite for the MED ml_moderation finding (audit-remediation
package A1, finding 2): `_concat_user_content` only kept `role == "user"` messages
(so MCP tool-result content, which arrives as `role == "tool"`, was completely
unscanned by moderation) AND it forwarded RAW, unmasked message content to
OpenAI's third-party `/v1/moderations` endpoint whenever the tenant's own
`pii_mask` guardrail was off/not configured — an external PII leak independent
of whatever guardrail posture the tenant chose for their OWN routed provider.

Pure unit-level suite against `MlModerationGuardrailEvaluator` with a fake
`ModerationProvider` double that records exactly what text it was asked to
classify — no HTTP/DB required.
"""

from __future__ import annotations

from typing import Any

from gateway.proxy.infrastructure.ml_moderation_evaluator import (
    ModerationVerdict,
    MlModerationGuardrailEvaluator,
)

ML_MODERATION_AUDIT = {"ml_moderation": {"enabled": True, "mode": "audit"}}


class _FakeModerationProvider:
    """Structural fake ModerationProvider — records the exact text it receives."""

    def __init__(self) -> None:
        self.calls = 0
        self.captured_inputs: list[str] = []

    async def moderate(self, text: str) -> ModerationVerdict:
        self.calls += 1
        self.captured_inputs.append(text)
        return ModerationVerdict(flagged=False, categories=[])


# ---------------------------------------------------------------------------
# Finding 2a — role-agnostic: MCP tool-result content (role == "tool") must be
# scanned by moderation, not silently skipped because it isn't role == "user".
# ---------------------------------------------------------------------------


async def test_moderation_scans_tool_role_mcp_content() -> None:
    provider = _FakeModerationProvider()
    evaluator = MlModerationGuardrailEvaluator(provider, credential_resolver=None)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "call the tool please"},
        {"role": "tool", "content": "untrusted MCP tool-result content to classify"},
    ]

    result = await evaluator.evaluate_pre(messages, ML_MODERATION_AUDIT)

    assert result.blocked is False
    assert provider.calls == 1
    captured = provider.captured_inputs[0]
    assert "untrusted MCP tool-result content to classify" in captured, (
        f"role=='tool' content must be included in the moderation scan text, "
        f"got captured={captured!r}"
    )


# ---------------------------------------------------------------------------
# Finding 2b — never forward unmasked PII to the 3rd-party moderation endpoint,
# even when the tenant's OWN pii_mask guardrail is off/not configured.
# ---------------------------------------------------------------------------


async def test_moderation_never_sees_raw_pii_when_pii_mask_not_configured() -> None:
    provider = _FakeModerationProvider()
    evaluator = MlModerationGuardrailEvaluator(provider, credential_resolver=None)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "please email me at leak@example.com about this"},
    ]

    # pii_mask is ABSENT from guardrail_configs entirely — the tenant never
    # configured it. Only ml_moderation is enabled.
    result = await evaluator.evaluate_pre(messages, ML_MODERATION_AUDIT)

    assert result.blocked is False
    assert provider.calls == 1
    captured = provider.captured_inputs[0]
    assert "leak@example.com" not in captured, (
        f"raw PII must never reach the 3rd-party moderation endpoint, "
        f"regardless of the tenant's pii_mask guardrail posture: captured={captured!r}"
    )
    assert "[EMAIL_REDACTED]" in captured


async def test_moderation_never_sees_raw_pii_when_pii_mask_explicitly_disabled() -> None:
    """Same as above, but pii_mask is explicitly present and disabled — must
    behave identically to the absent case (unconditional built-in scrub)."""
    provider = _FakeModerationProvider()
    evaluator = MlModerationGuardrailEvaluator(provider, credential_resolver=None)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "my ssn is 123-45-6789"},
    ]
    guardrail_configs = {
        **ML_MODERATION_AUDIT,
        "pii_mask": {"enabled": False, "mode": "mask"},
    }

    result = await evaluator.evaluate_pre(messages, guardrail_configs)

    assert result.blocked is False
    captured = provider.captured_inputs[0]
    assert "123-45-6789" not in captured
    assert "[SSN_REDACTED]" in captured


# ---------------------------------------------------------------------------
# HOLE 1 (adversarial-verification follow-up) — moderation must ALSO scan
# tool_calls[*].function.{name,arguments}, and must never forward raw PII
# living inside tool_calls arguments to the 3rd-party moderation endpoint.
# ---------------------------------------------------------------------------


async def test_moderation_scans_tool_call_arguments() -> None:
    provider = _FakeModerationProvider()
    evaluator = MlModerationGuardrailEvaluator(provider, credential_resolver=None)

    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "save_note",
                        "arguments": '{"note": "flaggable tool-call content here"}',
                    },
                }
            ],
        }
    ]

    result = await evaluator.evaluate_pre(messages, ML_MODERATION_AUDIT)

    assert result.blocked is False
    assert provider.calls == 1
    captured = provider.captured_inputs[0]
    assert "flaggable tool-call content here" in captured, (
        f"tool_calls[*].function.arguments must be included in the moderation "
        f"scan text, got captured={captured!r}"
    )


async def test_moderation_never_sees_raw_pii_in_tool_call_arguments() -> None:
    provider = _FakeModerationProvider()
    evaluator = MlModerationGuardrailEvaluator(provider, credential_resolver=None)

    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "save_contact",
                        "arguments": '{"email": "leak2@example.com"}',
                    },
                }
            ],
        }
    ]

    result = await evaluator.evaluate_pre(messages, ML_MODERATION_AUDIT)

    assert result.blocked is False
    captured = provider.captured_inputs[0]
    assert "leak2@example.com" not in captured, (
        f"raw PII inside tool_calls arguments must never reach the 3rd-party "
        f"moderation endpoint: captured={captured!r}"
    )
    assert "[EMAIL_REDACTED]" in captured
