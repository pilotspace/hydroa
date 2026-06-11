"""RegexGuardrailEvaluator — deterministic, regex-based guardrail implementation.

Implements the GuardrailEvaluator protocol (proxy/domain/ports.py).
Stateless: safe to use as a singleton.

Guardrail contracts (TASK.md §3 CONTRACT, frozen):
  Prompt-injection patterns (7 families, case-insensitive):
    1. ignore (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)
    2. disregard (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)
    3. forget (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)
    4. you are now <word>
    5. (act as|pretend to be|roleplay as|simulate being) <word>
    6. new (instructions|rules|persona|task|objective|goal):
    7. system: (in non-system role message content)

  PII replacement literals (exact):
    EMAIL       → "[EMAIL_REDACTED]"
    PHONE       → "[PHONE_REDACTED]"
    CREDIT_CARD → "[CREDIT_CARD_REDACTED]"
    SSN         → "[SSN_REDACTED]"

Fail-CLOSED: any exception during evaluate_pre() when any guardrail has mode=block
  → GuardrailResult(blocked=True, blocked_by="<guardrail>", ...)
Fail-OPEN: all active guardrails in mask/audit mode → log, return unblocked result.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from gateway.proxy.domain.entities import GuardrailEvent, GuardrailResult

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt-injection regex patterns (§3 CONTRACT, exact — tests assert on these)
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)ignore\s+(previous|prior|all|your|any)\s+"
        r"(instructions|rules|prompt|context|guidelines)"
    ),
    re.compile(
        r"(?i)disregard\s+(previous|prior|all|your|any)\s+"
        r"(instructions|rules|prompt|context|guidelines)"
    ),
    re.compile(
        r"(?i)forget\s+(previous|prior|all|your|any)\s+"
        r"(instructions|rules|prompt|context|guidelines)"
    ),
    re.compile(r"(?i)you\s+are\s+now\s+\w"),
    re.compile(r"(?i)(act\s+as|pretend\s+to\s+be|roleplay\s+as|simulate\s+being)\s+\w"),
    re.compile(r"(?i)new\s+(instructions|rules|persona|task|objective|goal)\s*:"),
    re.compile(r"(?i)\bsystem\s*:"),
]

# ---------------------------------------------------------------------------
# PII regex patterns and their exact replacement literals (§3 CONTRACT)
# ---------------------------------------------------------------------------
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "[EMAIL_REDACTED]",
    ),
    (
        re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
        "[PHONE_REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}"
            r"|6(?:011|5[0-9]{2})[0-9]{12})\b"
        ),
        "[CREDIT_CARD_REDACTED]",
    ),
    (
        re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
        "[SSN_REDACTED]",
    ),
]


def _has_block_mode(guardrail_configs: dict[str, Any]) -> bool:
    """Return True if any active guardrail is in block mode."""
    for _name, cfg in guardrail_configs.items():
        if isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("mode") == "block":
            return True
    return False


def _get_message_content(msg: dict[str, Any]) -> str:
    """Extract string content from a message dict; return '' if not a str."""
    content = msg.get("content", "")
    if not isinstance(content, str):
        return ""
    return content


def _check_injection(messages: list[dict[str, Any]]) -> bool:
    """Return True if any message content matches any injection pattern."""
    for msg in messages:
        content = _get_message_content(msg)
        if not content:
            continue
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(content):
                return True
    return False


def _mask_pii(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Apply all PII regexes to each message's content field.

    Returns (masked_messages, any_replaced).
    Creates a deep copy only when at least one replacement was made.
    """
    any_replaced = False
    result: list[dict[str, Any]] = []
    for msg in messages:
        content = _get_message_content(msg)
        if not content:
            result.append(msg)
            continue
        new_content = content
        for pattern, replacement in _PII_PATTERNS:
            new_content = pattern.sub(replacement, new_content)
        if new_content != content:
            any_replaced = True
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    return result, any_replaced


def _mask_pii_in_body(response_body: dict[str, Any]) -> dict[str, Any]:
    """Apply PII masking to choices[*].message.content in an upstream response body.

    Returns a (shallow-copy-of-top-level) modified body. Deep-copies only choices.
    """
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return response_body

    new_choices: list[Any] = []
    any_changed = False
    for choice in choices:
        if not isinstance(choice, dict):
            new_choices.append(choice)
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            new_choices.append(choice)
            continue
        content = message.get("content", "")
        if not isinstance(content, str) or not content:
            new_choices.append(choice)
            continue
        new_content = content
        for pattern, replacement in _PII_PATTERNS:
            new_content = pattern.sub(replacement, new_content)
        if new_content != content:
            any_changed = True
            new_message = {**message, "content": new_content}
            new_choices.append({**choice, "message": new_message})
        else:
            new_choices.append(choice)

    if not any_changed:
        return response_body
    return {**response_body, "choices": new_choices}


class RegexGuardrailEvaluator:
    """Stateless regex-based implementation of the GuardrailEvaluator protocol.

    evaluate_pre():
      1. prompt_injection check (if enabled): run 7 pattern families.
         BLOCK mode → blocked=True on match; fail-CLOSED on error.
         AUDIT mode → event(action="audited") on match; fail-OPEN on error.
         No match → event(action="passed").
      2. pii_mask check (if enabled): run 4 PII regexes.
         MASK mode → masked_messages = replaced copy; event(action="masked").
         AUDIT mode → event(action="audited"); masked_messages=None.
         No PII → event(action="passed").

    evaluate_post():
      Apply pii_mask (if enabled+mode=mask) to response_body choices[*].message.content.
      On error: log + return original body (fail-OPEN).
    """

    async def evaluate_pre(
        self,
        messages: list[dict[str, Any]],
        guardrail_configs: dict[str, Any],
    ) -> GuardrailResult:
        """Evaluate all configured pre-call guardrails."""
        try:
            return self._evaluate_pre_inner(messages, guardrail_configs)
        except Exception as exc:
            _log.warning(
                "guardrail_evaluator: unexpected error during evaluate_pre",
                exc_info=exc,
            )
            if _has_block_mode(guardrail_configs):
                return GuardrailResult(
                    blocked=True,
                    blocked_by="error",
                    masked_messages=None,
                    events=[
                        GuardrailEvent(
                            guardrail="error",
                            action="error",
                            detail=str(exc),
                        )
                    ],
                )
            # Fail-OPEN: all guardrails are mask/audit mode
            # Determine first guardrail name for the error event
            first_name = next(iter(guardrail_configs), "unknown")
            return GuardrailResult(
                blocked=False,
                blocked_by=None,
                masked_messages=None,
                events=[
                    GuardrailEvent(
                        guardrail=first_name,
                        action="error",
                        detail=str(exc),
                    )
                ],
            )

    def _evaluate_pre_inner(
        self,
        messages: list[dict[str, Any]],
        guardrail_configs: dict[str, Any],
    ) -> GuardrailResult:
        """Core evaluation logic (not exception-guarded — caller wraps)."""
        blocked = False
        blocked_by: str | None = None
        masked_messages: list[dict[str, Any]] | None = None
        events: list[GuardrailEvent] = []

        # --- 1. prompt_injection ---
        pi_cfg = guardrail_configs.get("prompt_injection")
        if isinstance(pi_cfg, dict) and pi_cfg.get("enabled"):
            mode = pi_cfg.get("mode", "audit")
            injection_found = _check_injection(messages)
            if injection_found:
                if mode == "block":
                    blocked = True
                    blocked_by = "prompt_injection"
                    events.append(
                        GuardrailEvent(
                            guardrail="prompt_injection",
                            action="blocked",
                            detail="Prompt injection pattern detected",
                        )
                    )
                else:
                    # audit mode
                    events.append(
                        GuardrailEvent(
                            guardrail="prompt_injection",
                            action="audited",
                            detail="Prompt injection pattern detected (audit mode)",
                        )
                    )
            else:
                events.append(
                    GuardrailEvent(
                        guardrail="prompt_injection",
                        action="passed",
                        detail="",
                    )
                )

        # --- 2. pii_mask ---
        pii_cfg = guardrail_configs.get("pii_mask")
        if isinstance(pii_cfg, dict) and pii_cfg.get("enabled"):
            mode = pii_cfg.get("mode", "audit")
            try:
                replaced_msgs, any_replaced = _mask_pii(messages)
            except Exception as exc:
                _log.warning("guardrail_evaluator: pii_mask error (fail-OPEN)", exc_info=exc)
                events.append(
                    GuardrailEvent(
                        guardrail="pii_mask",
                        action="error",
                        detail=str(exc),
                    )
                )
            else:
                if any_replaced:
                    if mode == "mask":
                        masked_messages = replaced_msgs
                        events.append(
                            GuardrailEvent(
                                guardrail="pii_mask",
                                action="masked",
                                detail="PII detected and masked",
                            )
                        )
                    else:
                        # audit mode — pass through unmodified; log event
                        events.append(
                            GuardrailEvent(
                                guardrail="pii_mask",
                                action="audited",
                                detail="PII detected (audit mode, not masked)",
                            )
                        )
                else:
                    events.append(
                        GuardrailEvent(
                            guardrail="pii_mask",
                            action="passed",
                            detail="",
                        )
                    )

        return GuardrailResult(
            blocked=blocked,
            blocked_by=blocked_by,
            masked_messages=masked_messages,
            events=events,
        )

    async def evaluate_post(
        self,
        response_body: dict[str, Any],
        guardrail_configs: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply PII masking to the upstream response body (post-call, non-streaming)."""
        pii_cfg = guardrail_configs.get("pii_mask")
        if not isinstance(pii_cfg, dict) or not pii_cfg.get("enabled"):
            return response_body
        if pii_cfg.get("mode") != "mask":
            return response_body
        try:
            return _mask_pii_in_body(response_body)
        except Exception as exc:
            _log.warning(
                "guardrail_evaluator: evaluate_post error (fail-OPEN, returning original body)",
                exc_info=exc,
            )
            return response_body
