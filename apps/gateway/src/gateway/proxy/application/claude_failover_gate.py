"""M8 non-Claude-failover gate (claude-gateway-protocol-compat TASK.md §3).

Gates ONLY the EXISTING `FallbackModelRouter` candidate-substitution mechanism for a
request that arrives Anthropic-wire (`/v1/messages`) naming (directly or via alias) a
Claude model — never touches `use_cases.py` or `fallback_router.py`'s own dial loop
(both stay byte-identical for `/v1/chat/completions` and every other wire format).

Implemented as a STATIC pre-filter of the alias's configured candidate set (mirrors
`RESIDENCY_NO_ELIGIBLE_REGION`'s own pre-loop-filter-then-refuse-if-empty shape): if
the alias key itself is claude-/anthropic--prefixed (Claude Code's own naming
convention — see model_discovery.py's identical predicate) and NONE of its configured
candidates resolve to the Anthropic provider, a tenant that has not opted in via
`allow_non_claude_failover` is refused before any dial.

DISCLOSED SCOPE (not a silently-dropped case): a MIXED alias (some Anthropic, some
non-Anthropic candidates) is never gated here — the existing router may or may not
ever reach a non-Anthropic member depending on live health/capacity, which this static
check cannot observe without reaching into FallbackModelRouter's own dial loop (exactly
the "replace or duplicate the existing mechanism" this task's Ground explicitly warns
against). Only the scenarios this task's frozen §2 actually specifies — an alias whose
candidates are ALL non-Anthropic, and a plain (non-alias) explicitly-named model — are
covered; a mixed alias is left to the router's existing (unconditional) behavior.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.infrastructure.orm import TenantRow

#: Claude Code's own naming convention (external protocol anchor, shared verbatim with
#: model_discovery.py's `_is_claude_shaped` — kept as two small copies rather than a
#: cross-module import so each stays independently reviewable against its own contract
#: clause; both are one-line predicates over the SAME two prefixes).
_CLAUDE_ID_PREFIXES = ("claude-", "anthropic-")


def is_claude_named(model_id: str) -> bool:
    """Does `model_id` follow Claude Code's own claude-/anthropic- naming convention?"""
    return model_id.startswith(_CLAUDE_ID_PREFIXES)


def has_no_eligible_anthropic_candidate(candidate_providers: dict[str, str | None]) -> bool:
    """True iff NONE of the resolved candidate->provider values is "anthropic"."""
    return not any(p == "anthropic" for p in candidate_providers.values())


async def tenant_allows_non_claude_failover(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Read the per-tenant opt-in flag (default false for every pre-existing tenant)."""
    stmt = select(TenantRow.allow_non_claude_failover).where(TenantRow.id == tenant_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return bool(row) if row is not None else False
