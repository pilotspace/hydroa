"""RED suite for reconciliation-endpoint (TASK.md §4, scenarios RE1–RE10).

GET /admin/reconciliation is a thin, owner/admin-scoped, TENANT-SCOPED read over the v29 t1
`reconcile_window` aggregate. Money is serialized as str(Decimal) — assertions compare the
Decimal VALUE (robust to Postgres NUMERIC scale) and pin that the field is a JSON string.

RED before BUILD: the route does not exist yet, so the success cases get 404 and the schema
import path is unused — the honest missing-implementation red. After BUILD each scenario
asserts the frozen ReconciliationResponse shape / status.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    INSIDE_DATE,
    RECON,
    auth,
    mint_role_token,
    recon_params,
    seed_row,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


# ---------------------------------------------------------------------------
# RE1 — owner sees their tenant's drift for a window
# ---------------------------------------------------------------------------
async def test_re1_owner_drift(client: Any, db_session: AsyncSession) -> None:
    token, tid, kid = await signup_tenant(client, tenant_name="Recon E1", email="e1@recon.io")
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.00"),
        provider_cost=Decimal("0.80"),
        cost_basis="provider",
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("2.00"),
        provider_cost=Decimal("1.20"),
        cost_basis="provider",
    )
    await db_session.commit()

    resp = await client.get(RECON, params=recon_params(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["drift"], str)  # money is str(Decimal), never float
    assert Decimal(body["provider_cost_total"]) == Decimal("2.00")
    assert Decimal(body["billed_total"]) == Decimal("3.00")
    assert Decimal(body["drift"]) == Decimal("-1.00")
    # the contracted half-open window bounds are echoed (start inclusive, end exclusive = +1 day)
    assert body["window_from"].startswith("2026-06-03")
    assert body["window_to"].startswith("2026-06-04")


# ---------------------------------------------------------------------------
# RE2 — tenant isolation: another tenant's rows are never included
# ---------------------------------------------------------------------------
async def test_re2_tenant_isolation(client: Any, db_session: AsyncSession) -> None:
    tok_a, tid_a, kid_a = await signup_tenant(client, tenant_name="Recon E2a", email="e2a@recon.io")
    _tok_b, tid_b, kid_b = await signup_tenant(
        client, tenant_name="Recon E2b", email="e2b@recon.io"
    )
    await seed_row(
        db_session,
        tenant_id=tid_a,
        key_id=kid_a,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        key_id=kid_b,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("2.00"),
        cost_basis="provider",
    )
    await db_session.commit()

    resp = await client.get(RECON, params=recon_params(), headers=auth(tok_a))

    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["provider_cost_total"]) == Decimal("1.00")  # only A; B invisible


# ---------------------------------------------------------------------------
# RE3 — an unbilled-upstream row is surfaced by usage_source
# ---------------------------------------------------------------------------
async def test_re3_unbilled_by_source(client: Any, db_session: AsyncSession) -> None:
    token, tid, kid = await signup_tenant(client, tenant_name="Recon E3", email="e3@recon.io")
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("0.50"),
        cost_basis="provider",
        usage_source="client_disconnect",
    )
    # a second source so the by_source grouping + sort-determinism contract is exercised
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("0.30"),
        cost_basis="provider",
        usage_source="stream_fallback",
    )
    await db_session.commit()

    resp = await client.get(RECON, params=recon_params(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["unbilled_upstream_cost"]) == Decimal("0.80")
    assert body["unbilled_rows"] == 2
    assert any(
        s["usage_source"] == "client_disconnect"
        and s["rows"] == 1
        and Decimal(s["provider_cost"]) == Decimal("0.50")
        for s in body["by_source"]
    )
    # by_source is sorted by usage_source for determinism (frozen §3 invariant)
    sources = [s["usage_source"] for s in body["by_source"]]
    assert sources == ["client_disconnect", "stream_fallback"] == sorted(sources)


# ---------------------------------------------------------------------------
# RE4 — an empty window returns 200 with zeros, never 404
# ---------------------------------------------------------------------------
async def test_re4_empty_window_zeros(client: Any, db_session: AsyncSession) -> None:
    token, _tid, _kid = await signup_tenant(client, tenant_name="Recon E4", email="e4@recon.io")

    resp = await client.get(RECON, params=recon_params(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["provider_cost_total"]) == Decimal("0")
    assert Decimal(body["billed_total"]) == Decimal("0")
    assert Decimal(body["drift"]) == Decimal("0")
    assert Decimal(body["unbilled_upstream_cost"]) == Decimal("0")
    assert Decimal(body["catalog_billed_total"]) == Decimal("0")
    assert body["unbilled_rows"] == 0
    assert body["by_source"] == []


# ---------------------------------------------------------------------------
# RE5 — an admin (not only an owner) may view reconciliation
# ---------------------------------------------------------------------------
async def test_re5_admin_allowed(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid, _kid = await signup_tenant(client, tenant_name="Recon E5", email="e5@recon.io")
    admin_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="e5-admin@recon.io"
    )

    resp = await client.get(RECON, params=recon_params(), headers=auth(admin_jwt))

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# RE6 — a member is forbidden
# ---------------------------------------------------------------------------
async def test_re6_member_forbidden(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid, _kid = await signup_tenant(client, tenant_name="Recon E6", email="e6@recon.io")
    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="e6-member@recon.io"
    )

    resp = await client.get(RECON, params=recon_params(), headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# RE7 — a missing or malformed Bearer is rejected
# ---------------------------------------------------------------------------
async def test_re7_no_bearer_401(client: Any) -> None:
    no_header = await client.get(RECON, params=recon_params())
    assert_problem(no_header, 401, "ERR_AUTH_INVALID_TOKEN")

    bad_scheme = await client.get(
        RECON, params=recon_params(), headers={"Authorization": "Basic Zm9vOmJhcg=="}
    )
    assert_problem(bad_scheme, 401, "ERR_AUTH_INVALID_TOKEN")


# ---------------------------------------------------------------------------
# RE8 — an invalid window value is rejected
# ---------------------------------------------------------------------------
async def test_re8_bad_window_422(client: Any) -> None:
    token, _tid, _kid = await signup_tenant(client, tenant_name="Recon E8", email="e8@recon.io")

    resp = await client.get(RECON, params={"window": "century"}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# RE9 — an invalid ISO start/end date is rejected
# ---------------------------------------------------------------------------
async def test_re9_bad_date_422(client: Any) -> None:
    token, _tid, _kid = await signup_tenant(client, tenant_name="Recon E9", email="e9@recon.io")

    resp = await client.get(
        RECON, params={"window": "month", "start": "2026-13-40"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# RE10 — catalog rows are reported separately, never folded into drift
# ---------------------------------------------------------------------------
async def test_re10_catalog_separate(client: Any, db_session: AsyncSession) -> None:
    token, tid, kid = await signup_tenant(client, tenant_name="Recon E10", email="e10@recon.io")
    # provider rows: Σ pcost 1.00 / Σ billed 1.20 → drift -0.20
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.20"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
    )
    # catalog rows: Σ billed 4.00, provider_cost NULL
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("2.50"),
        provider_cost=None,
        cost_basis="catalog",
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        key_id=kid,
        cost_usd=Decimal("1.50"),
        provider_cost=None,
        cost_basis="catalog",
    )
    await db_session.commit()

    resp = await client.get(RECON, params=recon_params(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["drift"]) == Decimal("-0.20")  # provider-only
    assert Decimal(body["catalog_billed_total"]) == Decimal("4.00")
