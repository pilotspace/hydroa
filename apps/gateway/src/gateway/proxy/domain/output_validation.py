# apps/gateway/src/gateway/proxy/domain/output_validation.py — NEW, pure, no IO
"""Opt-in response JSON-schema validation — output-schema-validation TASK.md §3 (FROZEN @ v1).

Pure domain helpers consumed by CompletionUseCase.complete() (application layer owns
the bounded-retry loop, billing, and error raising — nothing here does IO or retries).

SUPERSEDES the v11 "translate-don't-enforce" pin (response_format_translation.py)
ONLY when a request opts in (validate_output:true + the operator kill-switch). This
module does NOT edit response_format_translation.py — it is a sibling, additive seam.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

import jsonschema
from jsonschema.validators import validator_for


class ValidationOutcome(TypedDict):
    valid: bool
    parsed: object | None  # the FIRST choice's parsed JSON, for observability only
    errors: list[str]  # bounded to _MAX_ERRORS, human-readable


_MAX_ERRORS = 20
_MAX_RAW_OUTPUT_CHARS = 32_000


def check_schema_well_formed(schema: dict[str, Any]) -> str | None:
    """Meta-validate the caller's JSON Schema BEFORE any upstream call (M3).

    Returns None when well-formed, else a short reason string. Never raises —
    a malformed schema must be a clean pre-flight ERR_INVALID_JSON_SCHEMA, not
    an upstream-call-wasting 500.
    """
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
    except Exception as exc:
        return str(exc)
    return None


def validate_model_output(
    schema: dict[str, Any], response_body: dict[str, Any]
) -> ValidationOutcome:
    """Validate every choice's message.content against schema (M4).

    Pure -- no IO, no retry logic (the use case owns the retry loop, M5). A
    choice whose content is missing or not valid JSON counts as a validation
    failure, same as a schema mismatch (never silently pass unparseable content).
    """
    errors: list[str] = []
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ValidationOutcome(valid=False, parsed=None, errors=["ERR_NO_CHOICES"])
    parsed_first: object | None = None
    validator = jsonschema.Draft202012Validator(schema)
    for i, choice in enumerate(choices):
        message = (choice or {}).get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            errors.append(f"choice[{i}]: message.content missing or not a string")
            continue
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            errors.append(f"choice[{i}]: not valid JSON ({exc})")
            continue
        if i == 0:
            parsed_first = parsed
        for err in validator.iter_errors(parsed):
            errors.append(f"choice[{i}]: {err.message}")
            if len(errors) >= _MAX_ERRORS:
                break
    return ValidationOutcome(valid=not errors, parsed=parsed_first, errors=errors[:_MAX_ERRORS])


def truncate_raw_output(response_body: dict[str, Any]) -> str:
    """Serialize + size-cap the terminal-failure response body for the 422 (M12).

    Uses attempt 2's message.content when present (a plain string, no re-wrapping);
    falls back to a compact JSON dump of the whole body when content is absent/
    non-string (e.g. a malformed response) so the caller still sees SOMETHING
    diagnostic, never a blank field. Caps at _MAX_RAW_OUTPUT_CHARS with a
    '...[truncated]' suffix when cut — never written to application logs (M12).
    """
    choices = response_body.get("choices")
    raw: str
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        raw = content if isinstance(content, str) else json.dumps(response_body, default=str)
    else:
        raw = json.dumps(response_body, default=str)
    if len(raw) > _MAX_RAW_OUTPUT_CHARS:
        return raw[:_MAX_RAW_OUTPUT_CHARS] + "...[truncated]"
    return raw


__all__ = [
    "ValidationOutcome",
    "check_schema_well_formed",
    "truncate_raw_output",
    "validate_model_output",
]
