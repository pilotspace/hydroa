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
    IP          → "[IP_REDACTED]"         (pii-v2)
    IBAN        → "[IBAN_REDACTED]"       (pii-v2)
    SECRET      → "[SECRET_REDACTED]"    (pii-v2)
    PASSPORT    → "[PASSPORT_REDACTED]"  (pii-v2)

Fail-CLOSED: any exception during evaluate_pre() when any guardrail has mode=block
  → GuardrailResult(blocked=True, blocked_by="<guardrail>", ...)
Fail-OPEN: all active guardrails in mask/audit mode → log, return unblocked result.

pii-v2 custom patterns:
  Custom patterns are tenant-supplied regexes stored in guardrail_configs
  under pii_mask.pii_custom_patterns. They are applied AFTER all built-ins.
  Runtime budget guard: 100 ms total for ALL custom patterns per call (built-ins
  are excluded). Budget exceeded → fail-OPEN (skip remaining), emit metric + WARNING.
  Input cap: custom patterns only scan the first 64 KB of each field.
  Seam: _custom_budget_seconds instance attribute (default None → uses module constant
  _CUSTOM_BUDGET_SECONDS=0.1). Tests set to 0.0 to force immediate budget exhaustion.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import structlog

from gateway.proxy.domain.entities import GuardrailEvent, GuardrailResult

_log = logging.getLogger(__name__)
_slog = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# pii-v2 runtime budget guard constants (§3 CONTRACT)
# ---------------------------------------------------------------------------
_CUSTOM_BUDGET_SECONDS: float = 0.1  # 100 ms total for all custom pattern scans per call
_CUSTOM_INPUT_CAP: int = 65536  # 64 KB per message field cap for custom pattern scanning

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
# v4 frozen built-ins (1-4) + pii-v2 new built-ins (5-8)
# ---------------------------------------------------------------------------
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # --- v4 frozen (IMMUTABLE — never change these) ---
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
    # --- pii-v2 new built-ins (5-8, VERBATIM from §3 CONTRACT — FROZEN) ---
    # 5. IPv4 address
    (
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]\d|\d)\b"
        ),
        "[IP_REDACTED]",
    ),
    # 6. IBAN
    (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b"),
        "[IBAN_REDACTED]",
    ),
    # 7. API secret / high-entropy token
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{36}"
            r"|xoxb-[A-Za-z0-9\-]{24,})\b"
        ),
        "[SECRET_REDACTED]",
    ),
    # 8. Passport number (simplified)
    (
        re.compile(r"\b[A-Z]{1,2}[0-9]{6,9}\b"),
        "[PASSPORT_REDACTED]",
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
    """Apply all built-in PII regexes to each message's content field.

    Returns (masked_messages, any_replaced).
    Creates a deep copy only when at least one replacement was made.
    Built-in patterns only — no budget guard (they are linear-time and pre-verified).
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
    """Apply built-in PII masking to choices[*].message.content in an upstream response body.

    Returns a (shallow-copy-of-top-level) modified body. Deep-copies only choices.
    Built-in patterns only — no budget guard.
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


def mask_pii_in_messages(
    messages: list[dict[str, Any]],
    guardrail_configs: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply PII masking to a flat list of message-shaped dicts (payload-capture-store §3).

    Shape-generalized sibling of `_mask_pii_in_body` — extracted so BOTH `evaluate_post`
    (response body, existing caller) and `logs/application/capture_writer.py` (request
    AND response messages, new caller) apply the SAME regex table (built-ins + tenant
    custom patterns), never a parallel reimplementation (folded lesson, foundation-version
    12: "one alert seam not a parallel re-impl").

    Only masks when guardrail_configs["pii_mask"] is enabled AND mode == "mask" — mirrors
    `evaluate_post`'s own gate exactly (audit/disabled mode → pass through unchanged, same
    as the response path today).

    Unlike `evaluate_post`, this function does NOT fail-open on error — a bug here is
    exactly as likely as in `_mask_pii`/`_apply_custom_patterns_to_messages` (both already
    exception-free for well-formed input), and the payload-capture-store caller owns its
    OWN independent try/except mapping ANY exception to a metadata-only row (never a
    silently-unmasked one) — conflating the two failure directions is the single
    highest-stakes mistake documented in that task's Ground findings.
    """
    pii_cfg = guardrail_configs.get("pii_mask")
    if not isinstance(pii_cfg, dict) or not pii_cfg.get("enabled") or pii_cfg.get("mode") != "mask":
        return messages
    masked, _any_replaced = _mask_pii(messages)
    custom_compiled = _compile_custom_patterns(pii_cfg)
    if custom_compiled:
        deadline = time.monotonic() + _CUSTOM_BUDGET_SECONDS
        masked, _custom_any_replaced, _budget_exceeded = _apply_custom_patterns_to_messages(
            masked, custom_compiled, deadline, "mask", []
        )
    return masked


def _compile_custom_patterns(
    pii_cfg: dict[str, Any],
) -> list[tuple[re.Pattern[str], str]]:
    """Compile tenant custom patterns from pii_cfg.pii_custom_patterns.

    Returns a list of (compiled_pattern, literal) pairs.
    Literal is derived server-side: f"[{name}_REDACTED]".
    Invalid patterns are silently skipped (validation is enforced at PUT time).
    """
    raw_list = pii_cfg.get("pii_custom_patterns")
    if not isinstance(raw_list, list) or not raw_list:
        return []

    compiled: list[tuple[re.Pattern[str], str]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        pattern_str = item.get("pattern", "")
        if not name or not pattern_str:
            continue
        try:
            compiled_pat = re.compile(pattern_str)
        except re.error:
            continue  # defensive: PUT validation should have blocked this
        literal = f"[{name}_REDACTED]"
        compiled.append((compiled_pat, literal))
    return compiled


def _apply_custom_patterns_to_messages(
    messages: list[dict[str, Any]],
    custom_compiled: list[tuple[re.Pattern[str], str]],
    deadline: float,
    mode: str,
    budget_exceeded_events: list[GuardrailEvent],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Apply custom patterns to messages with monotonic deadline guard.

    Returns (result_messages, any_replaced, budget_exceeded).
    Applies patterns AFTER built-ins have already run on messages.
    Each field is capped to _CUSTOM_INPUT_CAP bytes before scanning.
    On budget exceeded: skip remaining patterns, fail-OPEN.
    """
    if not custom_compiled:
        return messages, False, False

    budget_exceeded = False
    any_replaced = False
    result: list[dict[str, Any]] = list(messages)  # shallow copy; update on change

    for pat_idx, (pattern, literal) in enumerate(custom_compiled):
        if time.monotonic() > deadline:
            budget_exceeded = True
            _slog.warning(
                "guardrail_custom_budget_exceeded",
                patterns_applied=pat_idx,
                patterns_total=len(custom_compiled),
            )
            break

        new_result: list[dict[str, Any]] = []
        for msg in result:
            content = _get_message_content(msg)
            if not content:
                new_result.append(msg)
                continue
            # Cap input to first 64 KB for custom pattern scanning
            capped = content[:_CUSTOM_INPUT_CAP]
            new_content = pattern.sub(literal, capped)
            # Reconstruct: capped portion replaced + rest (uncapped) preserved verbatim
            # Since we only scan capped portion, append the remainder unchanged
            remainder = content[_CUSTOM_INPUT_CAP:]
            full_new = new_content + remainder
            if full_new != content:
                any_replaced = True
                new_result.append({**msg, "content": full_new})
            else:
                new_result.append(msg)
        result = new_result

    return result, any_replaced, budget_exceeded


def _apply_custom_patterns_to_body(
    response_body: dict[str, Any],
    custom_compiled: list[tuple[re.Pattern[str], str]],
    deadline: float,
) -> tuple[dict[str, Any], bool]:
    """Apply custom patterns to response body choices[*].message.content.

    Returns (modified_body, budget_exceeded).
    Each content field is capped to _CUSTOM_INPUT_CAP bytes.
    On budget exceeded: skip remaining patterns, fail-OPEN (return what was done so far).
    """
    if not custom_compiled:
        return response_body, False

    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return response_body, False

    budget_exceeded = False
    # Build a working copy of choices as mutable dicts
    working_choices: list[Any] = []
    for choice in choices:
        if not isinstance(choice, dict):
            working_choices.append(choice)
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            working_choices.append(choice)
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            working_choices.append(choice)
            continue
        # Store current content for mutation
        working_choices.append(
            {
                "_choice": choice,
                "_message": message,
                "_content": content,
            }
        )

    any_changed = False
    for pat_idx, (pattern, literal) in enumerate(custom_compiled):
        if time.monotonic() > deadline:
            budget_exceeded = True
            _slog.warning(
                "guardrail_custom_budget_exceeded_post",
                patterns_applied=pat_idx,
                patterns_total=len(custom_compiled),
            )
            break

        for _i, wc in enumerate(working_choices):
            if not isinstance(wc, dict) or "_content" not in wc:
                continue
            content = wc["_content"]
            capped = content[:_CUSTOM_INPUT_CAP]
            new_content = pattern.sub(literal, capped)
            remainder = content[_CUSTOM_INPUT_CAP:]
            full_new = new_content + remainder
            if full_new != content:
                any_changed = True
                wc["_content"] = full_new

    if not any_changed and not budget_exceeded:
        # No changes made — short-circuit
        # Check if any change was made even with budget exceeded
        pass

    # Reconstruct the response body from working copies
    new_choices: list[Any] = []
    for wc in working_choices:
        if not isinstance(wc, dict) or "_content" not in wc:
            new_choices.append(wc)
            continue
        orig_choice = wc["_choice"]
        orig_message = wc["_message"]
        current_content = wc["_content"]
        if current_content != orig_message.get("content", ""):
            new_message = {**orig_message, "content": current_content}
            new_choices.append({**orig_choice, "message": new_message})
        else:
            new_choices.append(orig_choice)

    if not any_changed:
        return response_body, budget_exceeded
    return {**response_body, "choices": new_choices}, budget_exceeded


class RegexGuardrailEvaluator:
    """Regex-based implementation of the GuardrailEvaluator protocol.

    evaluate_pre():
      1. prompt_injection check (if enabled): run 7 pattern families.
         BLOCK mode → blocked=True on match; fail-CLOSED on error.
         AUDIT mode → event(action="audited") on match; fail-OPEN on error.
         No match → event(action="passed").
      2. pii_mask check (if enabled): run 8 built-in PII regexes (no budget guard).
         Then apply custom patterns (with budget guard) if pii_custom_patterns present.
         MASK mode → masked_messages = replaced copy; event(action="masked").
         AUDIT mode → event(action="audited"); masked_messages=None.
         No PII → event(action="passed").

    evaluate_post():
      Apply pii_mask built-ins + custom patterns to response_body choices[*].message.content.
      On error: log + return original body (fail-OPEN).

    _custom_budget_seconds: instance attribute, default None → reads _CUSTOM_BUDGET_SECONDS.
      Tests set to 0.0 to force immediate budget exhaustion.
    """

    def __init__(self) -> None:
        # Budget seam: None → use module constant _CUSTOM_BUDGET_SECONDS (0.1 s)
        # Tests override: evaluator._custom_budget_seconds = 0.0
        self._custom_budget_seconds: float | None = None

    def _get_deadline(self) -> float:
        """Compute the monotonic deadline for the current custom pattern scan."""
        budget = (
            self._custom_budget_seconds
            if self._custom_budget_seconds is not None
            else _CUSTOM_BUDGET_SECONDS
        )
        return time.monotonic() + budget

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

        # --- 2. pii_mask (built-ins + custom patterns) ---
        pii_cfg = guardrail_configs.get("pii_mask")
        if isinstance(pii_cfg, dict) and pii_cfg.get("enabled"):
            mode = pii_cfg.get("mode", "audit")
            try:
                # 2a. Apply all 8 built-in patterns (no budget guard — linear-time)
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
                # 2b. Apply custom patterns (after built-ins, with budget guard)
                custom_compiled = _compile_custom_patterns(pii_cfg)
                custom_any_replaced = False
                budget_exceeded = False

                if custom_compiled:
                    deadline = self._get_deadline()
                    working_msgs = replaced_msgs  # already has built-in masking applied
                    custom_msgs, custom_any_replaced, budget_exceeded = (
                        _apply_custom_patterns_to_messages(
                            working_msgs,
                            custom_compiled,
                            deadline,
                            mode,
                            [],  # budget_exceeded_events placeholder (not used directly)
                        )
                    )
                    replaced_msgs = custom_msgs
                    any_replaced = any_replaced or custom_any_replaced

                    if budget_exceeded:
                        events.append(
                            GuardrailEvent(
                                guardrail="pii_mask",
                                action="budget_exceeded",
                                detail="Custom pattern budget exceeded; remaining patterns skipped",
                            )
                        )

                # Emit the primary pii_mask event based on detection result
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
                elif not budget_exceeded:
                    events.append(
                        GuardrailEvent(
                            guardrail="pii_mask",
                            action="passed",
                            detail="",
                        )
                    )
                # If budget_exceeded but no pii found yet, still emit budget_exceeded
                # (already appended above)

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
        """Apply PII masking to the upstream response body (post-call, non-streaming).

        Applies built-in patterns first, then custom patterns (with budget guard).
        Fail-OPEN on any error.
        """
        pii_cfg = guardrail_configs.get("pii_mask")
        if not isinstance(pii_cfg, dict) or not pii_cfg.get("enabled"):
            return response_body
        if pii_cfg.get("mode") != "mask":
            return response_body
        try:
            # Apply built-in patterns
            result_body = _mask_pii_in_body(response_body)

            # Apply custom patterns (after built-ins, with budget guard)
            custom_compiled = _compile_custom_patterns(pii_cfg)
            if custom_compiled:
                deadline = self._get_deadline()
                result_body, _budget_exceeded = _apply_custom_patterns_to_body(
                    result_body, custom_compiled, deadline
                )
                # _budget_exceeded is fail-OPEN — we already returned what was done

            return result_body
        except Exception as exc:
            _log.warning(
                "guardrail_evaluator: evaluate_post error (fail-OPEN, returning original body)",
                exc_info=exc,
            )
            return response_body
