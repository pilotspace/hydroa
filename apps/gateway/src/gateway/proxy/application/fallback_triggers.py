"""The FALLBACK-TRIGGER TAXONOMY — pure classifier (error-aware-fallback §3 CONTRACT).

A model fallover is triggered by exactly one of three conditions:

  retry_exhausted  — upstream raised UpstreamUnavailableError (5xx / timeout / connect).
                     Owned by the FallbackModelRouter's existing except-branch (v6); NOT
                     classified here.
  context_window   — a 4xx error body whose prompt/context exceeds the model's window.
  content_policy   — a 4xx error body rejected by a safety / content policy.

`classify_fallback_trigger` covers ONLY the two NON-retryable, request-specific 4xx
conditions a different deployment can satisfy. It operates on the already-OpenAI-shaped
`(status, body)` every provider's `complete()` returns, so it is provider-agnostic.

Design-for-failure: the classifier is PURE, TOTAL, and NEVER raises — any odd / malformed
input degrades to `None` (a fail-safe passthrough: the router never falls over on a
response it cannot confidently classify).
"""

from __future__ import annotations

from typing import Any

# Closed taxonomy labels (also the `outcome` metric values for the two new triggers).
CONTEXT_WINDOW = "context_window"
CONTENT_POLICY = "content_policy"

# Retry-domain statuses owned by the retry seam (upstream_retry.py), NOT model fallover —
# §1 REJECT names 429 ("already retry-handled") and 408 is the request-timeout twin. Both
# are excluded from classification even if their body text incidentally matches a pattern.
_RETRY_DOMAIN_STATUSES: frozenset[int] = frozenset({408, 429})

# Case-insensitive substring patterns matched against error.code | error.type | error.message.
# OpenRouter/OpenAI emit the canonical codes; Anthropic (invalid_request_error) and Gemini
# (invalid_argument) need the message-pattern fallback. Patterns are kept SPECIFIC to the
# token/context + safety vocabulary providers actually emit so a generic validation 400
# ("field name too long", "blocked by firewall") does NOT misfire into a spurious fallover.
# A miss is fail-SAFE (passthrough); a false-positive would waste one upstream call, so the
# bias is toward precision.
_CONTEXT_PATTERNS: tuple[str, ...] = (
    "context_length",
    "context window",
    "maximum context",
    "context length",
    "exceeds the maximum",  # Gemini: "exceeds the maximum number of tokens"
    "input token count",
    "prompt is too long",  # Anthropic: "prompt is too long: N tokens > M maximum"
    "too many tokens",
    "reduce the length",
)
_POLICY_PATTERNS: tuple[str, ...] = (
    "content_filter",
    "content policy",
    "content_policy",
    "content management",  # Azure: "...triggering Azure OpenAI's content management policy"
    "safety system",  # OpenAI: "rejected as a result of our safety system"
    "responsible ai",
    "usage policies",
)


def classify_fallback_trigger(status: int, body: Any) -> str | None:
    """Classify an OpenAI-shaped `(status, body)` into a fallover trigger label, or None.

    Returns `"context_window"` | `"content_policy"` | `None`.
    PURE · TOTAL · never raises. Only a NON-retry-domain 4xx with a dict `error` sub-object
    classifies; `context_window` takes precedence over `content_policy` when both patterns
    match. Every 2xx/3xx/5xx, retry-domain 4xx (408/429), non-matching 4xx, and
    malformed/empty body returns `None`.
    """
    if not (400 <= status <= 499):
        return None
    if status in _RETRY_DOMAIN_STATUSES:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None

    # Build the lower-cased haystack from the optional string fields (any may be absent
    # or non-string — only strings contribute).
    parts: list[str] = []
    for key in ("code", "type", "message"):
        val = err.get(key)
        if isinstance(val, str):
            parts.append(val.lower())
    if not parts:
        return None
    haystack = " ".join(parts)

    # context_window FIRST (precedence): an overflow that is also worded as a policy block
    # still routes to the bigger-window deployment, not a different-provider one.
    if any(p in haystack for p in _CONTEXT_PATTERNS):
        return CONTEXT_WINDOW
    if any(p in haystack for p in _POLICY_PATTERNS):
        return CONTENT_POLICY
    return None


__all__ = ["CONTENT_POLICY", "CONTEXT_WINDOW", "classify_fallback_trigger"]
