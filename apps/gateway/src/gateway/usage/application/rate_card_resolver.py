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

# region-pricing (TASK.md §3, DECIDED at freeze): the seed multiplier keyed by a
# model's region — eu is the only premium region today; us/ap/global (and any
# NULL/unrecognized region) all fall through to the identity multiplier below.
_REGION_MULTIPLIER_SEEDS: dict[str, Decimal] = {"eu": Decimal("1.1")}
_DEFAULT_REGION_MULTIPLIER = Decimal("1.0")


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


async def resolve_region_multiplier(
    session: AsyncSession, tenant_id: uuid.UUID, model_id: str
) -> Decimal:
    """Return the effective region multiplier for (tenant_id, model_id) (region-pricing
    TASK.md §3 CONTRACT — FROZEN @ v1, the SINGLE resolution point for the region
    factor).

    1. SELECT multiplier FROM tenant_region_multiplier_overrides WHERE tenant_id=:t
       AND region=(SELECT region FROM models WHERE id=:m) — the per-(tenant, region)
       override, if one exists. A HIT resolves in this ONE query (no second round trip).
    2. ELSE (miss, or the model itself is unknown): a second query fetches the model's
       `region` so the DECIDED seed can be keyed by it: {"eu": Decimal("1.1")}.get(
       region, Decimal("1.0")) — us/ap/global/NULL/unrecognized all -> 1.0. Pricing is
       fail-OPEN (never blocks a request) — only residency POLICY fail-closes.

    Never raises for a missing/unrecognized model or region — always resolves to a
    safe multiplier (TASK.md §2 "Unrecognized or NULL region resolves to the safe
    default, never blocks a request").
    """
    override_row = (
        await session.execute(
            text(
                "SELECT multiplier FROM tenant_region_multiplier_overrides"
                " WHERE tenant_id = :t AND region = (SELECT region FROM models WHERE id = :m)"
            ),
            {"t": str(tenant_id), "m": model_id},
        )
    ).fetchone()
    if override_row is not None:
        return Decimal(str(override_row[0]))

    region_row = (
        await session.execute(
            text("SELECT region FROM models WHERE id = :m"),
            {"m": model_id},
        )
    ).fetchone()
    region = str(region_row[0]) if region_row is not None and region_row[0] is not None else None
    if region is None:
        return _DEFAULT_REGION_MULTIPLIER
    return _REGION_MULTIPLIER_SEEDS.get(region, _DEFAULT_REGION_MULTIPLIER)


async def resolve_tier_multiplier(
    session: AsyncSession, tenant_id: uuid.UUID, model_id: str, tier: str
) -> Decimal:
    """RESERVED extension point (region-pricing TASK.md §3 M9) — signature only.

    NOT implemented by this task; service-tiers' own contract fills this body when
    it lands. Raises loudly on any accidental early call rather than silently
    returning an identity multiplier that would mask a wiring bug.
    """
    raise NotImplementedError(
        "resolve_tier_multiplier is a reserved extension point (region-pricing "
        "TASK.md §3 M9) — its body is filled by the service-tiers task, not yet landed."
    )
