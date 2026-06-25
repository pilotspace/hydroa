"""RED suite for slo-metrics (TASK.md §4).

GET /admin/slo is a tenant-scoped, OPS_READ-gated aggregate over usage_records.status.
Frozen contract (§3):
  - 200 -> { window_hours, total_requests, success_count (status < 500),
             client_error_count (4xx), server_error_count (5xx),
             availability (success/total or 1.0 if total==0),
             error_rate (server_error/total or 0.0 if total==0),
             latency_ms: null }
  - 400 -> ERR_PAYLOAD_INVALID (window_hours out of 1..720 or non-integer)
  - 403 -> ERR_AUTH_FORBIDDEN (lacks OPS_READ)
Source: usage_records WHERE tenant_id=:tid AND created_at >= now()-window.
latency_ms is NULL by design — no stored latency (HONEST).

RED before BUILD: the route does not exist yet so success cases get 404 — the honest
missing-implementation red.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    SLO,
    auth,
    create_key,
    mint_role_token,
    seed_usage,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


# ---------------------------------------------------------------------------
# test_slo_aggregates: 90×200 + 5×400 + 5×500 →
#   total=100, success=95, client=5, server=5, avail=0.95, err=0.05
# ---------------------------------------------------------------------------
async def test_slo_aggregates(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="SLO Agg", email="agg@slo.io")
    key_id = await create_key(client, token, name="agg-key")

    # Seed 90 success (200), 5 client-error (400), 5 server-error (500)
    for _ in range(90):
        await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=200)
    for _ in range(5):
        await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=400)
    for _ in range(5):
        await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=500)

    resp = await client.get(SLO, params={"window_hours": "24"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_hours"] == 24
    assert body["total_requests"] == 100
    assert body["success_count"] == 95  # 200 (90) + 400 (5) — both < 500
    assert body["client_error_count"] == 5  # 4xx only
    assert body["server_error_count"] == 5  # 5xx only
    assert abs(body["availability"] - 0.95) < 1e-9, f"avail={body['availability']}"
    assert abs(body["error_rate"] - 0.05) < 1e-9, f"err_rate={body['error_rate']}"


# ---------------------------------------------------------------------------
# test_slo_empty_window: no rows → total=0, avail=1.0, err=0.0 (no zero-div)
# ---------------------------------------------------------------------------
async def test_slo_empty_window(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="SLO Empty", email="empty@slo.io")

    resp = await client.get(SLO, headers=auth(token))  # default window_hours=24

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_requests"] == 0
    assert body["success_count"] == 0
    assert body["client_error_count"] == 0
    assert body["server_error_count"] == 0
    assert body["availability"] == 1.0, "empty window must default availability to 1.0"
    assert body["error_rate"] == 0.0, "empty window must default error_rate to 0.0"


# ---------------------------------------------------------------------------
# test_slo_ops_read_gating: member → 403; others → 200
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "role,expect_200",
    [
        (Role.OWNER, True),
        (Role.ADMIN, True),
        (Role.OPERATOR, True),
        (Role.BILLING_ADMIN, True),
        (Role.VIEWER, True),
        (Role.MEMBER, False),
    ],
)
async def test_slo_ops_read_gating(
    client: Any,
    db_session: AsyncSession,
    app: Any,
    role: Role,
    expect_200: bool,
) -> None:
    _owner, tid = await signup_tenant(
        client,
        tenant_name=f"SLO Gate {role}",
        email=f"gate-owner-{role}@slo.io",
    )
    jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=role, email=f"gate-{role}@slo.io"
    )

    resp = await client.get(SLO, headers=auth(jwt))

    if expect_200:
        assert resp.status_code == 200, (
            f"role {role} should have OPS_READ: {resp.status_code} {resp.text}"
        )
    else:
        assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# test_slo_window_bounds: 0, 1000, "abc", "-1" → ERR_PAYLOAD_INVALID (422)
# Note: PAYLOAD_INVALID ErrorSpec uses status 422 (consistent with all other
# payload validation in the codebase, e.g. GET /admin/alerts limit/offset).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_window", ["0", "1000", "abc", "-1"])
async def test_slo_window_bounds(client: Any, db_session: AsyncSession, bad_window: str) -> None:
    token, _tid = await signup_tenant(
        client, tenant_name=f"SLO Bounds {bad_window}", email=f"bounds{bad_window}@slo.io"
    )

    resp = await client.get(SLO, params={"window_hours": bad_window}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# test_slo_tenant_isolation: tenant A rows are never visible to tenant B
# ---------------------------------------------------------------------------
async def test_slo_tenant_isolation(client: Any, db_session: AsyncSession) -> None:
    token_a, tid_a = await signup_tenant(client, tenant_name="SLO Iso A", email="iso-a@slo.io")
    token_b, tid_b = await signup_tenant(client, tenant_name="SLO Iso B", email="iso-b@slo.io")
    key_a = await create_key(client, token_a, name="iso-a-key")
    key_b = await create_key(client, token_b, name="iso-b-key")

    # Seed 10 server-errors for tenant A; 5 successes for tenant B
    for _ in range(10):
        await seed_usage(db_session, tenant_id=tid_a, key_id=key_a, status=500)
    for _ in range(5):
        await seed_usage(db_session, tenant_id=tid_b, key_id=key_b, status=200)

    resp_a = await client.get(SLO, headers=auth(token_a))
    resp_b = await client.get(SLO, headers=auth(token_b))

    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    body_a = resp_a.json()
    body_b = resp_b.json()

    # A sees 10 total (all 5xx), B sees 5 total (all 2xx)
    assert body_a["total_requests"] == 10, f"A total: {body_a}"
    assert body_a["server_error_count"] == 10, f"A server_error: {body_a}"
    assert body_b["total_requests"] == 5, f"B total: {body_b}"
    assert body_b["server_error_count"] == 0, f"B server_error: {body_b}"
    # Verify tenant B's rows are not in A's aggregate
    assert body_a["success_count"] == 0, f"A must not see B's success rows: {body_a}"


# ---------------------------------------------------------------------------
# test_slo_latency_null: latency_ms field is always null (honest omission)
# ---------------------------------------------------------------------------
async def test_slo_latency_null(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="SLO Latency", email="latency@slo.io")
    key_id = await create_key(client, token, name="latency-key")
    await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=200)

    resp = await client.get(SLO, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "latency_ms" in body, "latency_ms field must be present in response"
    assert body["latency_ms"] is None, (
        f"latency_ms must be null (honest omission — no stored latency): {body['latency_ms']}"
    )


# ---------------------------------------------------------------------------
# test_slo_window_valid_edges: window_hours=1 and window_hours=720 are valid
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valid_window", ["1", "720"])
async def test_slo_window_valid_edges(
    client: Any, db_session: AsyncSession, valid_window: str
) -> None:
    token, _tid = await signup_tenant(
        client,
        tenant_name=f"SLO Valid {valid_window}",
        email=f"valid{valid_window}@slo.io",
    )

    resp = await client.get(SLO, params={"window_hours": valid_window}, headers=auth(token))

    assert resp.status_code == 200, (
        f"window_hours={valid_window} should be valid: {resp.status_code} {resp.text}"
    )
    assert resp.json()["window_hours"] == int(valid_window)


# ---------------------------------------------------------------------------
# test_slo_window_excludes_old_records: records outside the window are excluded
# ---------------------------------------------------------------------------
async def test_slo_window_excludes_old_records(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="SLO Window", email="window@slo.io")
    key_id = await create_key(client, token, name="window-key")

    # Seed one record well within the 1-hour window (30 minutes ago)
    inside = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)
    # Seed one record well outside the 1-hour window (2 hours ago)
    outside = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)

    await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=500, created_at=inside)
    await seed_usage(db_session, tenant_id=tid, key_id=key_id, status=200, created_at=outside)

    resp = await client.get(SLO, params={"window_hours": "1"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the inside record (500) should be counted
    assert body["total_requests"] == 1, f"only records in window should be counted: {body}"
    assert body["server_error_count"] == 1, f"the 500 inside should count: {body}"
    assert body["success_count"] == 0, f"the 200 outside should be excluded: {body}"
