"""RED suite for reconciliation-aggregate (TASK.md §4, scenarios RA1–RA8).

The pure `reconcile_window` aggregate over the append-only `usage_records` ledger computes,
for a half-open [from, to) window: Σ(provider_cost) vs Σ(billed cost_usd) → drift over
`cost_basis='provider'` rows, the unbilled-upstream breakdown (provider_cost>0 ∧ cost_usd=0)
by `usage_source`, and the separate catalog billed total.

RED before BUILD: `gateway.usage.application.reconciliation` does not exist yet, so the
module import below fails (ImportError) — the honest missing-implementation red for the whole
suite. After BUILD each scenario asserts the frozen `ReconciliationSummary` shape.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.application.reconciliation import (
    ReconciliationSummary,
    SourceBreakdown,
    reconcile_window,
)

from .conftest import (
    INSIDE,
    WINDOW_FROM,
    WINDOW_TO,
    count_rows,
    seed_row,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


# ---------------------------------------------------------------------------
# RA1 — drift over provider-basis rows
# ---------------------------------------------------------------------------
async def test_ra1_drift_provider_rows(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, key_id = await signup_tenant(client, tenant_name="Recon A1", email="a1@recon.io")
    # provider rows: Σ provider_cost = 2.00, Σ cost_usd = 3.00
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("1.00"),
                   provider_cost=Decimal("0.80"), cost_basis="provider", created_at=INSIDE)
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("2.00"),
                   provider_cost=Decimal("1.20"), cost_basis="provider", created_at=INSIDE)

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert isinstance(summary, ReconciliationSummary)
    assert summary.provider_cost_total == Decimal("2.00")
    assert summary.billed_total == Decimal("3.00")
    assert summary.drift == Decimal("-1.00")  # billed > upstream cost = healthy margin


# ---------------------------------------------------------------------------
# RA2 — an unbilled-upstream row is counted, never absorbed
# ---------------------------------------------------------------------------
async def test_ra2_unbilled_row_counted(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, key_id = await signup_tenant(client, tenant_name="Recon A2", email="a2@recon.io")
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("0"),
                   provider_cost=Decimal("0.50"), cost_basis="provider",
                   usage_source="client_disconnect", created_at=INSIDE)

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert summary.unbilled_upstream_cost == Decimal("0.50")
    assert summary.unbilled_rows == 1
    assert any(
        b.usage_source == "client_disconnect" and b.rows == 1 and b.provider_cost == Decimal("0.50")
        for b in summary.by_source
    )


# ---------------------------------------------------------------------------
# RA3 — catalog rows are excluded from drift, surfaced separately
# ---------------------------------------------------------------------------
async def test_ra3_catalog_excluded_from_drift(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, key_id = await signup_tenant(client, tenant_name="Recon A3", email="a3@recon.io")
    # catalog rows: provider_cost NULL, Σ cost_usd = 4.00
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("1.50"),
                   provider_cost=None, cost_basis="catalog", created_at=INSIDE)
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("2.50"),
                   provider_cost=None, cost_basis="catalog", created_at=INSIDE)

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert summary.provider_cost_total == Decimal("0")
    assert summary.billed_total == Decimal("0")
    assert summary.drift == Decimal("0")
    assert summary.catalog_billed_total == Decimal("4.00")


# ---------------------------------------------------------------------------
# RA4 — an empty window returns explicit zeros and never raises
# ---------------------------------------------------------------------------
async def test_ra4_empty_window_zeros(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, _ = await signup_tenant(client, tenant_name="Recon A4", email="a4@recon.io")

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert summary.provider_cost_total == Decimal("0")
    assert summary.billed_total == Decimal("0")
    assert summary.drift == Decimal("0")
    assert summary.unbilled_upstream_cost == Decimal("0")
    assert summary.catalog_billed_total == Decimal("0")
    assert summary.unbilled_rows == 0
    assert summary.by_source == ()


# ---------------------------------------------------------------------------
# RA5 — the window is half-open [from, to)
# ---------------------------------------------------------------------------
async def test_ra5_window_half_open(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, key_id = await signup_tenant(client, tenant_name="Recon A5", email="a5@recon.io")
    # at the inclusive lower bound → counted
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("0"),
                   provider_cost=Decimal("0.11"), cost_basis="provider", created_at=WINDOW_FROM)
    # at the exclusive upper bound → NOT counted
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("0"),
                   provider_cost=Decimal("0.99"), cost_basis="provider", created_at=WINDOW_TO)

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert summary.provider_cost_total == Decimal("0.11")  # only the from-row, to is exclusive


# ---------------------------------------------------------------------------
# RA6 — tenant scoping vs operator-wide
# ---------------------------------------------------------------------------
async def test_ra6_tenant_scoping(
    client: Any, db_session: AsyncSession
) -> None:
    tid_a, key_a = await signup_tenant(client, tenant_name="Recon A6a", email="a6a@recon.io")
    tid_b, key_b = await signup_tenant(client, tenant_name="Recon A6b", email="a6b@recon.io")
    await seed_row(db_session, tenant_id=tid_a, key_id=key_a, cost_usd=Decimal("0"),
                   provider_cost=Decimal("1.00"), cost_basis="provider", created_at=INSIDE)
    await seed_row(db_session, tenant_id=tid_b, key_id=key_b, cost_usd=Decimal("0"),
                   provider_cost=Decimal("2.00"), cost_basis="provider", created_at=INSIDE)

    scoped = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tid_a)
    operator_wide = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=None)

    assert scoped.provider_cost_total == Decimal("1.00")
    assert operator_wide.provider_cost_total == Decimal("3.00")  # all tenants


# ---------------------------------------------------------------------------
# RA7 — an inverted window is rejected (reject)
# ---------------------------------------------------------------------------
async def test_ra7_inverted_window_raises(
    client: Any, db_session: AsyncSession
) -> None:
    before = await count_rows(db_session)

    with pytest.raises(ValueError):
        await reconcile_window(db_session, WINDOW_TO, WINDOW_FROM, tenant_id=None)  # to < from

    assert await count_rows(db_session) == before  # read-only: nothing written


# ---------------------------------------------------------------------------
# RA8 — by_source groups unbilled rows by usage_source
# ---------------------------------------------------------------------------
async def test_ra8_by_source_grouping(
    client: Any, db_session: AsyncSession
) -> None:
    tenant_id, key_id = await signup_tenant(client, tenant_name="Recon A8", email="a8@recon.io")
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("0"),
                   provider_cost=Decimal("0.10"), cost_basis="provider",
                   usage_source="client_disconnect", created_at=INSIDE)
    await seed_row(db_session, tenant_id=tenant_id, key_id=key_id, cost_usd=Decimal("0"),
                   provider_cost=Decimal("0.20"), cost_basis="provider",
                   usage_source="stream_fallback", created_at=INSIDE)

    summary = await reconcile_window(db_session, WINDOW_FROM, WINDOW_TO, tenant_id=tenant_id)

    assert summary.unbilled_rows == 2
    assert summary.unbilled_upstream_cost == Decimal("0.30")
    by_src = {b.usage_source: b for b in summary.by_source}
    assert isinstance(summary.by_source[0], SourceBreakdown)
    assert by_src["client_disconnect"].rows == 1
    assert by_src["client_disconnect"].provider_cost == Decimal("0.10")
    assert by_src["stream_fallback"].rows == 1
    assert by_src["stream_fallback"].provider_cost == Decimal("0.20")
