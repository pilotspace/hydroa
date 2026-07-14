"""M2/M3 Claude gateway protocol header passthrough — captured at the boundary this
task owns (claude-gateway-protocol-compat TASK.md §3).

DISCLOSED RESIDUAL GAP (§7 change-request against anthropic-messages-ingress, that
adapter never edited by this task per Ground's "this task likewise never edits it"):
reaching the ACTUAL outbound Anthropic HTTP call requires two seams neither of which
exists in the frozen sibling today —
  1. `anthropic_upstream.py::AnthropicCompletionUpstream._auth_headers()` hardcodes
     `anthropic-version` from OPERATOR config and has no merge point for a client's own
     `anthropic-beta` / `anthropic-workspace-id` / overriding `anthropic-version`.
  2. `anthropic_ingress.py::anthropic_messages_request_to_openai()` is an explicit
     field allowlist — an unknown body field such as `context_management` is silently
     dropped before it ever reaches `internal_body`, so this module's pairing-drop
     logic (`drop_unpaired_beta_fields`) has nothing to act on yet for THAT specific
     field until the sibling's own translator grows a passthrough allowlist entry.

This module builds the part that IS achievable within this task's Ground constraints:
  - `parse_anthropic_beta` — open-list parse (never allowlisted to today's known values).
  - `BETA_BODY_FIELD_PAIRS` — the beta-value <-> body-field pairing table (§3), so a
    future consumer (this router, or the sibling once extended) can enforce "both
    forwarded together, or both dropped together, never split" mechanically.
  - `AnthropicPassthroughHeaders` + a request-scoped ContextVar, mirroring the EXACT
    per-request-credential-contextvar convention every adapter in this codebase already
    uses (`get_provider_credential()` and its Azure/Bedrock/Gemini siblings) for
    additive, zero-blast-radius per-request extension — so the day `_auth_headers()`
    grows the merge point, it reads a ContextVar exactly like it already does for auth.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

#: §3 feature pass-through table: a beta value paired with a body field travels WITH
#: it, or both are dropped together — never split. Extend this table, never branch
#: per-field ad hoc, so a future beta/field pair is one line, not a new code path.
BETA_BODY_FIELD_PAIRS: dict[str, str] = {
    "context-management-2025-06-27": "context_management",
    "output-config-2025-08-01": "output_config",
}


@dataclass(frozen=True, slots=True)
class AnthropicPassthroughHeaders:
    """The inbound Claude-gateway-protocol headers captured at the /v1/messages
    boundary for a single request — `anthropic-beta` values, `anthropic-version`,
    and `anthropic-workspace-id`, ALL still exactly as the client sent them."""

    anthropic_version: str | None = None
    #: Open list — never validated against a known-value set (§3 M2).
    anthropic_beta: tuple[str, ...] = field(default_factory=tuple)
    anthropic_workspace_id: str | None = None


_EMPTY = AnthropicPassthroughHeaders()

#: Request-scoped publish/consume seam, mirroring `get_provider_credential()`'s own
#: ContextVar convention (anthropic_upstream.py / azure_upstream.py / bedrock_upstream.py
#: / gemini_upstream.py all read a request-scoped credential ContextVar this exact way).
#: Unset (default) ⇒ every reader sees `_EMPTY` ⇒ byte-identical to pre-this-task behavior.
_passthrough_headers_ctx: contextvars.ContextVar[AnthropicPassthroughHeaders] = (
    contextvars.ContextVar("_anthropic_passthrough_headers_ctx", default=_EMPTY)
)


def parse_anthropic_beta(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated `anthropic-beta` header value into an ordered tuple.

    Open list (§3 M2): every value is kept, including one Hydroa has never seen before
    ("the set changes with Claude Code releases" — external protocol anchor). Never
    allowlisted, never dropped for being unrecognized.
    """
    if not raw:
        return ()
    return tuple(v.strip() for v in raw.split(",") if v.strip())


def capture_passthrough_headers(headers: dict[str, str]) -> AnthropicPassthroughHeaders:
    """Build an `AnthropicPassthroughHeaders` from a lowercased request-headers dict.

    Pure — does not set the ContextVar (the caller decides scope/lifetime, mirroring
    `_tags = _parse_tags_header(...)` being a pure pre-step before its own wiring).
    """
    return AnthropicPassthroughHeaders(
        anthropic_version=headers.get("anthropic-version"),
        anthropic_beta=parse_anthropic_beta(headers.get("anthropic-beta")),
        anthropic_workspace_id=headers.get("anthropic-workspace-id"),
    )


def set_passthrough_headers(value: AnthropicPassthroughHeaders) -> None:
    """Publish this request's captured headers onto the ContextVar (call once, early,
    same async context an eventual upstream dial runs in — mirrors `_credit_hold_ctx`'s
    own publish-before-ensure_future discipline)."""
    _passthrough_headers_ctx.set(value)


def get_passthrough_headers() -> AnthropicPassthroughHeaders:
    """Read this request's captured headers (or the empty sentinel if never set)."""
    return _passthrough_headers_ctx.get()


def reset_passthrough_headers() -> None:
    """Explicit reset to the empty sentinel — used by tests to avoid cross-test leakage
    when NOT running through a fresh asyncio task per test."""
    _passthrough_headers_ctx.set(_EMPTY)


def is_claude_platform_aws_provider(provider: str) -> bool:
    """§3 M3: is `provider` a Claude-Platform-on-AWS-shaped adapter?

    Disclosed gap: no such adapter exists yet in this codebase (Bedrock's own Anthropic
    models are a DIFFERENT, `provider="bedrock"` shape — Bedrock's native API, not
    Anthropic's "Claude Platform on AWS" marketplace product). Always False today;
    kept as a single named predicate (never inlined string-compares) so the day such
    an adapter ships, one line changes here and every M3 call site updates for free.
    """
    return False


def drop_unpaired_beta_fields(
    beta_values: tuple[str, ...], body: dict[str, object]
) -> tuple[str, ...]:
    """§3 M2: for a non-Anthropic-resolved request, drop BOTH a beta value and its
    paired body field together — never split.

    Returns the beta tuple with any paired value removed; mutates `body` in place to
    pop the paired field. A beta value with no entry in `BETA_BODY_FIELD_PAIRS` (or
    whose paired field is absent from `body`) is unaffected — this function only acts
    on KNOWN pairs, per the §3 table.
    """
    survivors: list[str] = []
    for value in beta_values:
        field_name = BETA_BODY_FIELD_PAIRS.get(value)
        if field_name is not None:
            body.pop(field_name, None)
            continue
        survivors.append(value)
    return tuple(survivors)
