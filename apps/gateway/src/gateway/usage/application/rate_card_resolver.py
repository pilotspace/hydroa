"""resolve_markup_pct — the single source of truth for the effective per-(tenant,
model) markup (tiered-rate-cards TASK.md §3 CONTRACT — FROZEN @ v1).

Layered rule: a per-model override in `tenant_rate_card_entries` wins; ELSE the
tenant's flat `tenants.markup_pct` (the EXACT pre-existing fallback query — text
preserved verbatim so the ~8 markup-mocking regression suites that match on
`SELECT markup_pct FROM tenants` stay byte-identical; TASK.md §0 "Regression
blast radius" honor).

THREE callers resolve the identical rate through this rule (TASK.md §3
"single effective-rate resolver, THREE callers" — no third-site drift):
  - usage/application/recorder.py `_fetch_markup_pct` (billing) — calls this
    function directly (an existing open AsyncSession is already in scope).
  - usage/application/cost_recovery.py `_fetch_markup` (disconnect/OpenRouter
    recovery) — opens its own session, then calls this function.
  - catalog/infrastructure/repository.py `list_active_models_with_markup` — a
    bulk LEFT JOIN + COALESCE(entry.markup_pct, TenantRow.markup_pct) form of
    the SAME rule (a per-row scalar call would be N+1; the join is the
    equivalent bulk expression of this exact two-step resolution).

INVARIANT (no third-site drift): for any (tenant, model), catalog multiplier ==
billing multiplier == recovery multiplier.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ZERO = Decimal("0")


async def resolve_markup_pct(session: AsyncSession, tenant_id: uuid.UUID, model_id: str) -> Decimal:
    """Return the effective markup_pct for (tenant_id, model_id).

    1. SELECT markup_pct FROM tenant_rate_card_entries WHERE tenant_id=:t AND
       model_id=:m — the per-model override, if one exists.
    2. ELSE the EXACT existing fallback: SELECT markup_pct FROM tenants WHERE
       id = :tid — 0 if the tenant row is absent (unchanged pre-existing
       behavior).
    """
    override_row = (
        await session.execute(
            text(
                "SELECT markup_pct FROM tenant_rate_card_entries"
                " WHERE tenant_id = :t AND model_id = :m"
            ),
            {"t": str(tenant_id), "m": model_id},
        )
    ).fetchone()
    if override_row is not None:
        return Decimal(str(override_row[0]))

    # Fallback query — text preserved verbatim (immutable; TASK.md §5 Safety rule).
    fallback_row = (
        await session.execute(
            text("SELECT markup_pct FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    if fallback_row is None:
        return _ZERO
    return Decimal(str(fallback_row[0]))
