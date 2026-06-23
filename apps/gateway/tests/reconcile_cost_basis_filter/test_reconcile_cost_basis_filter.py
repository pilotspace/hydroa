"""v33 reconcile-cost-basis-filter — audit the recorder invariant + lock the
provider-only unbilled filter.

The unbilled-upstream filter excludes cost_basis='catalog' rows; a catalog row that
nonetheless carries provider_cost is a recorder-invariant breach. audit_cost_basis_breaches
surfaces those rows, and reconcile_window must never count them as unbilled upstream.

Real Postgres:5433. RED before build: audit_cost_basis_breaches/CostBasisBreach do not exist
(ImportError) until built; the reconcile_window exclusion test locks the already-correct guard.

usage_records is NOT truncated between tests, so every assertion is scoped to the tenant the
test just signed up — never a global count.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.application.reconciliation import (
    audit_cost_basis_breaches,
    reconcile_window,
)

from .conftest import WINDOW_FROM, WINDOW_TO, seed_row, signup_tenant

pytestmark = pytest.mark.asyncio


async def test_audit_finds_catalog_row_with_provider_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tid, kid = await signup_tenant(client, tenant_name="AuditCo", email="a@auditco.io")
    # breach: catalog row carrying provider_cost
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0.20",
                   provider_cost="0.50", cost_basis="catalog")
    # clean catalog row (no provider_cost) — must NOT be flagged
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0.10",
                   provider_cost=None, cost_basis="catalog")
    # provider row — must NOT be flagged
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0",
                   provider_cost="1.00", cost_basis="provider")

    breaches = await audit_cost_basis_breaches(db_session)

    mine = [b for b in breaches if str(b.tenant_id) == tid]
    assert len(mine) == 1
    assert mine[0].provider_cost == Decimal("0.50")


async def test_audit_empty_when_invariant_holds(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tid, kid = await signup_tenant(client, tenant_name="CleanCo", email="c@cleanco.io")
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0",
                   provider_cost="1.00", cost_basis="provider")
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0.10",
                   provider_cost=None, cost_basis="catalog")

    breaches = await audit_cost_basis_breaches(db_session)

    mine = [b for b in breaches if str(b.tenant_id) == tid]
    assert mine == []


async def test_reconcile_window_excludes_catalog_provider_cost_from_unbilled(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tid, kid = await signup_tenant(client, tenant_name="ExclCo", email="e@exclco.io")
    # provider unbilled row (counts) + catalog breach row (must be excluded)
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0",
                   provider_cost="1.00", cost_basis="provider")
    await seed_row(db_session, tenant_id=tid, key_id=kid, cost_usd="0",
                   provider_cost="0.50", cost_basis="catalog")

    summary = await reconcile_window(
        db_session, WINDOW_FROM, WINDOW_TO, tenant_id=uuid.UUID(tid)
    )

    assert summary.unbilled_upstream_cost == Decimal("1.00")  # provider only; catalog excluded
