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

import functools
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


def _extract_text_from_content_list(content: list[Any]) -> str:
    """Join scannable text out of a list-shaped message content field.

    Mirrors the list-content traversal idiom in
    `proxy/application/modality_guard.py::required_input_modalities_for_chat`:
    only dict parts with `type == "text"` and a str `text` field contribute
    (Anthropic content-blocks and OpenAI vision `text` parts share this shape);
    `image_url`/`image`/other part types carry no scannable text and are
    skipped, never raising on an unknown or malformed shape. A part that
    self-identifies as `type == "text"` but whose `text` field is missing or
    not a str is ALSO skipped here (nothing to join) — callers that need to
    know about that unreadable-but-text-typed case use
    `_message_has_unresolvable_text_block` to fail closed instead of silently
    treating it as PII-free.
    """
    texts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)


def _extract_tool_call_text(tool_calls: Any) -> str:
    """Join scannable text out of a message's `tool_calls` field (OpenAI shape).

    HOLE 1 (adversarial-verification follow-up, audit-remediation package A1):
    `tool_calls[].function.arguments` is a JSON string carrying arbitrary
    tenant-supplied or model-emitted data (the primary agent-gateway traffic
    shape — Anthropic `tool_use` blocks are translated into this exact shape at
    ingress, see `anthropic_ingress.py::_assistant_content_to_openai`) — it was
    previously invisible to every scan (injection detection, moderation concat)
    because only `content` was ever read. Each call's `function.name` and
    `function.arguments` are treated as plain scannable text; the regex table
    is applied to the raw `arguments` string verbatim, never parsed as JSON, so
    detection/masking can never depend on (or break) JSON structure.
    Non-list/malformed input contributes "" and is skipped, mirroring the
    fail-open-on-unknown-shape posture of `_extract_text_from_content_list`.
    """
    if not isinstance(tool_calls, list):
        return ""
    texts: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str):
            texts.append(name)
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            texts.append(arguments)
    return "\n".join(texts)


def _get_message_content(msg: dict[str, Any]) -> str:
    """Extract scannable text from a message dict's content field AND tool_calls.

    str content -> returned verbatim (the common path, unchanged).
    list content (Anthropic content-blocks, incl. cache_control-bearing blocks,
    and OpenAI vision `image_url` parts) -> the joined text of every recognized
    `{"type": "text", "text": <str>}` part; other part types contribute "".
    Any other shape (missing/None/dict/etc.) -> "" (no scannable text) — same
    fail-open-on-unknown-shape posture `modality_guard` uses for content it
    cannot interpret.

    HOLE 1: `tool_calls[].function.{name,arguments}` text (see
    `_extract_tool_call_text`) is appended so injection detection
    (`_check_injection`, the sole caller of this function) also covers PII/
    injection payloads smuggled inside tool-call arguments, not just `content`.
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = _extract_text_from_content_list(content)
    else:
        text = ""
    tool_call_text = _extract_tool_call_text(msg.get("tool_calls"))
    if tool_call_text:
        return f"{text}\n{tool_call_text}" if text else tool_call_text
    return text


def _message_has_unresolvable_text_block(msg: dict[str, Any]) -> bool:
    """True if `msg["content"]` is a list containing a block that declares
    itself `type == "text"` but whose `text` field is missing or not a str —
    a recognized-but-unreadable text-bearing block.

    PII masking cannot safely scan OR rewrite such a block: mirrors the
    MCP-connector `_get_block_text` / `_build_scan_messages` idiom (mcp_connector/
    application/use_cases.py) where only a WELL-FORMED text field is ever
    substituted into and anything else fails the relay CLOSED rather than
    silently passing content that might carry unmasked PII in a field the
    evaluator cannot read.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    for part in content:
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and not isinstance(part.get("text"), str)
        ):
            return True
    return False


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


def _apply_pattern_table(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    """Sequentially apply every (pattern, replacement) pair to text."""
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def _mask_tool_calls(tool_calls: Any) -> tuple[Any, bool]:
    """Apply built-in PII regexes to a message's `tool_calls[*].function`
    `name` and `arguments` fields (HOLE 1 — see `_extract_tool_call_text`).

    `arguments` is a JSON string; the regex table is applied to it verbatim
    (never parsed/re-serialized), so a well-formed JSON string stays a
    well-formed JSON string after masking — the frozen replacement literals
    (e.g. `[EMAIL_REDACTED]`) contain no characters that could break JSON
    string-literal syntax.

    Returns (new_tool_calls, changed). Non-list/malformed input is returned
    unchanged (nothing to safely mask) — same fail-open-on-unknown-shape
    posture as the content-side helpers.
    """
    if not isinstance(tool_calls, list):
        return tool_calls, False
    changed = False
    new_calls: list[Any] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            new_calls.append(call)
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            new_calls.append(call)
            continue
        new_function = function
        function_changed = False
        name = function.get("name")
        if isinstance(name, str) and name:
            new_name = _apply_pattern_table(name, _PII_PATTERNS)
            if new_name != name:
                new_function = {**new_function, "name": new_name}
                function_changed = True
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            new_arguments = _apply_pattern_table(arguments, _PII_PATTERNS)
            if new_arguments != arguments:
                new_function = {**new_function, "arguments": new_arguments}
                function_changed = True
        if function_changed:
            changed = True
            new_calls.append({**call, "function": new_function})
        else:
            new_calls.append(call)
    return new_calls, changed


def _mask_message_content(msg: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Apply built-in PII regexes to ONE message's `content` field AND
    `tool_calls[*].function.{name,arguments}` (HOLE 1), whatever their shapes.

    str content -> masked string (the common path, unchanged behavior).
    list content -> a NEW list where every recognized `{"type": "text", "text":
    <str>}` part has its `text` field masked IN PLACE (all other keys on that
    part, e.g. `cache_control`, are carried through byte-identical via dict
    spread); every other part (image_url, image, or any part this evaluator
    doesn't recognize) is carried through unchanged, by identity, in the same
    position — mirrors the MCP-connector `_apply_masked_blocks` idiom of only
    ever rewriting the recognized text field, nothing else.
    Any other content shape -> unchanged, not-replaced (nothing to safely mask).
    `tool_calls` (if present on the message) is masked via `_mask_tool_calls`.

    Returns (updates, changed): `updates` is a dict containing ONLY the fields
    that changed (`"content"` and/or `"tool_calls"`), meant to be splatted by
    the caller as `{**msg, **updates}`; `({}, False)` when nothing changed.
    Reused verbatim by the response-side `_mask_pii_in_body` so request- and
    response-leg masking share one implementation.
    """
    updates: dict[str, Any] = {}
    changed = False

    content = msg.get("content", "")
    if isinstance(content, str):
        if content:
            new_content = _apply_pattern_table(content, _PII_PATTERNS)
            if new_content != content:
                updates["content"] = new_content
                changed = True
    elif isinstance(content, list):
        content_changed = False
        new_parts: list[Any] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    new_text = _apply_pattern_table(text, _PII_PATTERNS)
                    if new_text != text:
                        content_changed = True
                        new_parts.append({**part, "text": new_text})
                        continue
            new_parts.append(part)
        if content_changed:
            updates["content"] = new_parts
            changed = True

    if "tool_calls" in msg:
        new_tool_calls, tool_calls_changed = _mask_tool_calls(msg.get("tool_calls"))
        if tool_calls_changed:
            updates["tool_calls"] = new_tool_calls
            changed = True

    return updates, changed


def _mask_pii(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Apply all built-in PII regexes to each message's content AND tool_calls.

    Returns (masked_messages, any_replaced).
    Creates a deep copy only when at least one replacement was made.
    Handles plain str content, list-shaped content (Anthropic content-blocks /
    OpenAI vision parts), and `tool_calls[*].function.{name,arguments}` (HOLE 1)
    via `_mask_message_content`, which rewrites recognized text in place and
    leaves every other part (image_url, image, cache_control, etc.) byte-identical.
    Built-in patterns only — no budget guard (they are linear-time and pre-verified).
    """
    any_replaced = False
    result: list[dict[str, Any]] = []
    for msg in messages:
        updates, changed = _mask_message_content(msg)
        if changed:
            any_replaced = True
            result.append({**msg, **updates})
        else:
            result.append(msg)
    return result, any_replaced


def _mask_pii_in_body(response_body: dict[str, Any]) -> dict[str, Any]:
    """Apply built-in PII masking to choices[*].message in an upstream response body.

    HOLE 2 / HOLE 1 response-side (adversarial-verification follow-up, audit-
    remediation package A1 rework): reuses `_mask_message_content` — the SAME
    function the request-side `_mask_pii` uses — so list-shaped content
    (Anthropic content-blocks reaching this path through any future adapter)
    AND `tool_calls[*].function.{name,arguments}` (a model-EMITTED tool call
    carrying PII in its arguments) get the identical masking treatment on the
    response leg as on the request leg, with no parallel reimplementation.

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
        updates, changed = _mask_message_content(message)
        if changed:
            any_changed = True
            new_message = {**message, **updates}
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

    UNCONDITIONAL (payload-capture-store verify fix, Tin decision 2026-07-10): built-in
    PII patterns are ALWAYS applied, regardless of whether the tenant has independently
    enabled `guardrail_configs["pii_mask"]`. Scrub-before-persist must hold for every
    tenant that only ever calls `PUT /admin/capture` — gating the capture-path scrub
    behind a SEPARATE, independently-configured `PUT /admin/guardrails` toggle defeated
    the "PII-scrubbed capture" invariant for every tenant using the DB default
    (`guardrail_configs = '{}'`). Tenant custom patterns (`pii_mask.pii_custom_patterns`)
    are additionally applied whenever configured, also independent of the `enabled`/`mode`
    gate, for the same reason. This function currently has no OTHER caller (evaluate_post
    masks response bodies via `_mask_pii_in_body`, a separate, still-toggle-gated code
    path controlling what is echoed back to the client) — so this change is scoped to the
    capture-persist path only; it does not alter response-side guardrail behavior.

    Unlike `evaluate_post`, this function does NOT fail-open on error — a bug here is
    exactly as likely as in `_mask_pii`/`_apply_custom_patterns_to_messages` (both already
    exception-free for well-formed input), and the payload-capture-store caller owns its
    OWN independent try/except mapping ANY exception to a metadata-only row (never a
    silently-unmasked one) — conflating the two failure directions is the single
    highest-stakes mistake documented in that task's Ground findings.
    """
    masked, _any_replaced = _mask_pii(messages)
    pii_cfg = guardrail_configs.get("pii_mask")
    if isinstance(pii_cfg, dict):
        custom_compiled = _compile_custom_patterns(pii_cfg)
        if custom_compiled:
            deadline = time.monotonic() + _CUSTOM_BUDGET_SECONDS
            masked, _custom_any_replaced, _budget_exceeded = _apply_custom_patterns_to_messages(
                masked, custom_compiled, deadline, "mask", []
            )
    return masked


@functools.lru_cache(maxsize=512)
def _compile_patterns_cached(
    items: tuple[tuple[str, str], ...],
) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Compile a frozen (name, pattern) tuple → ((compiled, literal), ...), memoised.

    Keyed by the pattern CONTENT, so an unchanged tenant config reuses the compiled
    result across every request and a config edit keys to a fresh entry automatically
    (no explicit invalidation). Bounded LRU (512) caps memory; entries are tiny.
    """
    compiled: list[tuple[re.Pattern[str], str]] = []
    for name, pattern_str in items:
        try:
            compiled_pat = re.compile(pattern_str)
        except re.error:
            continue  # defensive: PUT validation should have blocked this
        compiled.append((compiled_pat, f"[{name}_REDACTED]"))
    return tuple(compiled)


def _compile_custom_patterns(
    pii_cfg: dict[str, Any],
) -> list[tuple[re.Pattern[str], str]]:
    """Compile tenant custom patterns from pii_cfg.pii_custom_patterns.

    Returns a list of (compiled_pattern, literal) pairs.
    Literal is derived server-side: f"[{name}_REDACTED]".
    Invalid patterns are silently skipped (validation is enforced at PUT time).

    The compile is memoised by pattern content (see _compile_patterns_cached): this
    function is on the per-request PII path, but the config only changes on an admin PUT,
    so the common case is a cache hit that skips the whole scan-and-compile loop.
    """
    raw_list = pii_cfg.get("pii_custom_patterns")
    if not isinstance(raw_list, list) or not raw_list:
        return []

    # Freeze to a hashable, order-preserving key of the (name, pattern) pairs that
    # actually contribute; anything malformed is dropped before the cache key is built.
    items = tuple(
        (item["name"], item["pattern"])
        for item in raw_list
        if isinstance(item, dict) and item.get("name") and item.get("pattern")
    )
    return list(_compile_patterns_cached(items))


def _apply_capped_pattern(
    text: str, pattern: re.Pattern[str], literal: str
) -> tuple[str, bool]:
    """Apply one custom pattern to `text`, scanning only the first
    `_CUSTOM_INPUT_CAP` bytes (the uncapped remainder is preserved verbatim).
    Returns (new_text, changed)."""
    capped = text[:_CUSTOM_INPUT_CAP]
    new_text = pattern.sub(literal, capped)
    remainder = text[_CUSTOM_INPUT_CAP:]
    full_new = new_text + remainder
    return full_new, full_new != text


def _apply_capped_pattern_to_tool_calls(
    tool_calls: Any, pattern: re.Pattern[str], literal: str
) -> tuple[Any, bool]:
    """Apply one custom pattern (capped to `_CUSTOM_INPUT_CAP` bytes per field)
    to each `tool_calls[*].function`'s `name`/`arguments` (HOLE 1). Mirrors
    `_apply_capped_pattern`'s per-field cap discipline, applied to the two
    additional maskable text fields tool_calls carries.
    """
    if not isinstance(tool_calls, list):
        return tool_calls, False
    changed = False
    new_calls: list[Any] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            new_calls.append(call)
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            new_calls.append(call)
            continue
        new_function = function
        function_changed = False
        name = function.get("name")
        if isinstance(name, str) and name:
            new_name, name_changed = _apply_capped_pattern(name, pattern, literal)
            if name_changed:
                new_function = {**new_function, "name": new_name}
                function_changed = True
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            new_arguments, arguments_changed = _apply_capped_pattern(arguments, pattern, literal)
            if arguments_changed:
                new_function = {**new_function, "arguments": new_arguments}
                function_changed = True
        if function_changed:
            changed = True
            new_calls.append({**call, "function": new_function})
        else:
            new_calls.append(call)
    return new_calls, changed


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
    Each field is capped to _CUSTOM_INPUT_CAP bytes before scanning. Handles
    both plain str content and list-shaped content (Anthropic content-blocks /
    OpenAI vision parts) — only recognized `{"type": "text", "text": <str>}`
    parts are scanned; every other part is carried through unchanged.
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
            updates: dict[str, Any] = {}
            msg_changed = False

            msg_content = msg.get("content", "")
            if isinstance(msg_content, str):
                if msg_content:
                    full_new, part_changed = _apply_capped_pattern(msg_content, pattern, literal)
                    if part_changed:
                        updates["content"] = full_new
                        msg_changed = True
            elif isinstance(msg_content, list):
                # List-shaped content (Anthropic content-blocks / OpenAI vision
                # parts): only recognized `{"type": "text", "text": <str>}`
                # parts are scanned+capped; every other part is carried through
                # unchanged, same idiom as `_mask_message_content`.
                content_changed = False
                new_parts: list[Any] = []
                for part in msg_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            full_new, part_changed = _apply_capped_pattern(
                                text, pattern, literal
                            )
                            if part_changed:
                                content_changed = True
                                new_parts.append({**part, "text": full_new})
                                continue
                    new_parts.append(part)
                if content_changed:
                    updates["content"] = new_parts
                    msg_changed = True

            # HOLE 1: tool_calls[*].function.{name,arguments} custom-pattern
            # scan, capped per field just like content — same idiom as
            # `_mask_tool_calls`/`_apply_capped_pattern_to_tool_calls`.
            if "tool_calls" in msg:
                new_tool_calls, tool_calls_changed = _apply_capped_pattern_to_tool_calls(
                    msg.get("tool_calls"), pattern, literal
                )
                if tool_calls_changed:
                    updates["tool_calls"] = new_tool_calls
                    msg_changed = True

            if msg_changed:
                any_replaced = True
                new_result.append({**msg, **updates})
            else:
                new_result.append(msg)
        result = new_result

    return result, any_replaced, budget_exceeded


def _apply_custom_patterns_to_body(
    response_body: dict[str, Any],
    custom_compiled: list[tuple[re.Pattern[str], str]],
    deadline: float,
) -> tuple[dict[str, Any], bool]:
    """Apply custom patterns to response body choices[*].message.

    HOLE 2 / HOLE 1 response-side (adversarial-verification follow-up, audit-
    remediation package A1 rework): reuses `_apply_custom_patterns_to_messages`
    — the SAME per-message custom-pattern application the request-side uses —
    by treating each choice's `message` dict as a message-shaped dict, so
    list-shaped content and `tool_calls[*].function.{name,arguments}` get the
    identical capped-scan treatment as the request leg, with no parallel
    reimplementation.

    Returns (modified_body, budget_exceeded).
    Each content/tool_calls text field is capped to _CUSTOM_INPUT_CAP bytes.
    On budget exceeded: skip remaining patterns, fail-OPEN (return what was done so far).
    """
    if not custom_compiled:
        return response_body, False

    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return response_body, False

    messages: list[dict[str, Any]] = []
    message_indices: list[int] = []
    for idx, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            messages.append(message)
            message_indices.append(idx)

    if not messages:
        return response_body, False

    new_messages, any_changed, budget_exceeded = _apply_custom_patterns_to_messages(
        messages, custom_compiled, deadline, "mask", []
    )

    if not any_changed:
        return response_body, budget_exceeded

    new_choices: list[Any] = list(choices)
    for new_message, idx in zip(new_messages, message_indices, strict=True):
        new_choices[idx] = {**choices[idx], "message": new_message}

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

                # Fail-CLOSED: mode=mask promises to strip PII before the request
                # is relayed/logged, but a message may carry a block that
                # self-identifies as `type == "text"` while its `text` field is
                # missing/non-str — a shape this evaluator cannot read, let alone
                # rewrite. Silently masking everything ELSE and relaying such a
                # message would risk leaking whatever PII lives in the unreadable
                # field. Mirrors the MCP-connector `mask_unresolved` idiom
                # (mcp_connector/application/use_cases.py): when a masked value
                # cannot be safely substituted back into the message structure,
                # block the relay instead of guessing. Only applies to mode=mask
                # — audit mode never substitutes anything, so there is nothing to
                # "unresolve".
                unresolved = mode == "mask" and any(
                    _message_has_unresolvable_text_block(m) for m in messages
                )

                # Emit the primary pii_mask event based on detection result
                if unresolved:
                    blocked = True
                    blocked_by = "pii_mask"
                    masked_messages = None
                    events.append(
                        GuardrailEvent(
                            guardrail="pii_mask",
                            action="blocked",
                            detail=(
                                "Unmaskable text block present "
                                "(masked value cannot be safely substituted back); "
                                "relay blocked (fail-closed)"
                            ),
                        )
                    )
                elif any_replaced:
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
