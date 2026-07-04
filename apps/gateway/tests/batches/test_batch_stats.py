"""Red/Green test suite for batch-dashboard-surface — GET /admin/batches/stats
(batch-dashboard-surface TASK.md §3, DRAFT — see Status note on the contract for the
still-open lowest-confidence flags this suite does not need resolved to be correct).

Contract under test:
  GET /admin/batches/stats   — session-JWT auth (owner or admin only; member -> 403)
       -> 200 {savings_usd, total_requests, status_counts:{pending,succeeded,errored,
               canceled,expired}}
       -> 403 ERR_AUTH_FORBIDDEN (non-owner/admin)
       -> 401 (missing/invalid JWT)

savings_usd is ALWAYS "0.00" today — an explicit constant, NOT a query. list_price_usd does
not exist in usage_records yet (confirmed repo-wide, 2026-07-03) — that column is
batch-billing-accuracy's job (a separate, not-yet-started task). total_requests +
status_counts ARE real queries over BatchJobItemRow, tenant-scoped.

TENANT ISOLATION: stats never include another tenant's batch items (BatchJobItemRow.tenant_id
is denormalized on the item row itself — no join needed).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

_ITEM_VOCAB = ("pending", "succeeded", "errored", "canceled", "expired")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _line_item(
    custom_id: str, *, model: str = "test-model", content: str = "hello"
) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "body": {"model": model, "messages": [{"role": "user", "content": content}]},
    }


async def _signup_owner(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a new tenant (owner), log in, create an API key.

    Returns {tenant_id, jwt, key} — jwt for the new /admin/batches/stats endpoint
    (session auth), key for the existing /v1/batches endpoint (API-key auth) so a single
    fixture can both create batch data AND read the stats for the SAME tenant.
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, f"login failed: {login.text}"
    jwt = login.json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers=_bearer(jwt),
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {"tenant_id": tenant_id, "jwt": jwt, "key": created.json()["key"]}


def _issue_role_token(app: Any, *, tenant_id: str, role_str: str) -> str:
    """Issue a JWT for the GIVEN tenant with an arbitrary role — same live token_service
    pattern as tests/rbac_roles/test_rbac_roles.py, but pinned to a real tenant_id so the
    caller is scoped to the SAME tenant whose batch data the test created."""
    from gateway.tenants.domain.entities import Role

    role = Role(role_str)
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=f"{role_str}@batch-stats-test.local",
    )
    return token


async def _await_all_batch_tasks(app: Any) -> None:
    """Wait for all tracked in-process batch job tasks to complete (same helper as
    test_batch_jobs.py — the no-processor-configured worker cascades items to errored)."""
    tasks: set[asyncio.Task[None]] = getattr(app.state, "batch_jobs_tasks", set())
    if tasks:
        pending = set(tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
async def owner(client: Any) -> dict[str, str]:
    return await _signup_owner(
        client,
        tenant_name="BatchStatsTest",
        email="batch-stats@example.io",
        password="batch-stats-battery",
    )


# ---------------------------------------------------------------------------
# Scenario: Admin views the batches stats page (M1)
# Scenario: Savings StatCard shows the honest current value (M2)
# Scenario: Volume and status-breakdown StatCards show real, currently-zero numbers (M3-M5)
# ---------------------------------------------------------------------------


class TestOwnerOrAdminGetsHonestZeroStats:
    async def test_owner_gets_all_zero_stats(self, client: Any, owner: dict[str, str]) -> None:
        resp = await client.get("/admin/batches/stats", headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["savings_usd"] == "0.00"
        assert body["total_requests"] == 0
        assert body["status_counts"] == dict.fromkeys(_ITEM_VOCAB, 0)

    async def test_admin_role_also_gets_stats(
        self, app: Any, client: Any, owner: dict[str, str]
    ) -> None:
        admin_token = _issue_role_token(app, tenant_id=owner["tenant_id"], role_str="admin")
        resp = await client.get("/admin/batches/stats", headers=_bearer(admin_token))
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario: Non-admin cannot reach the batches nav entry or its data (M1, R1)
# ---------------------------------------------------------------------------


class TestNonAdminForbidden:
    async def test_member_role_403(self, app: Any, client: Any, owner: dict[str, str]) -> None:
        member_token = _issue_role_token(app, tenant_id=owner["tenant_id"], role_str="member")
        resp = await client.get("/admin/batches/stats", headers=_bearer(member_token))
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    async def test_viewer_role_403(self, app: Any, client: Any, owner: dict[str, str]) -> None:
        """VIEWER holds USAGE_READ/OPS_READ but NOT KEYS_MANAGE — require_owner_or_admin
        must still reject it, confirming this is deliberately narrower than the Usage
        page's own access level (Tin's own words named "admin" as the viewer)."""
        viewer_token = _issue_role_token(app, tenant_id=owner["tenant_id"], role_str="viewer")
        resp = await client.get("/admin/batches/stats", headers=_bearer(viewer_token))
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    async def test_no_bearer_401(self, client: Any) -> None:
        resp = await client.get("/admin/batches/stats")
        assert resp.status_code == 401

    async def test_invalid_bearer_401(self, client: Any) -> None:
        resp = await client.get(
            "/admin/batches/stats", headers={"Authorization": "Bearer not-a-real-jwt"}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scenario: Page reflects real batch activity once it exists (M3, M4, M5, A1)
# ---------------------------------------------------------------------------


class TestStatsReflectRealBatchActivity:
    async def test_stats_reflect_created_items_after_no_processor_cascade(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        """No BatchProcessor configured (this project's honest default) -> the
        background task cascades every item to errored (test_batch_jobs.py's own
        documented behavior) — stats must reflect that, not stay at all-zero."""
        submit = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("s1"), _line_item("s2"), _line_item("s3")]},
            headers=_bearer(owner["key"]),
        )
        assert submit.status_code == 200
        await _await_all_batch_tasks(app)

        resp = await client.get("/admin/batches/stats", headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_requests"] == 3
        assert sum(body["status_counts"].values()) == 3
        assert body["status_counts"]["errored"] == 3, f"expected all 3 errored: {body}"
        # savings_usd stays "0.00" regardless of activity — it's a constant until
        # batch-billing-accuracy lands list_price_usd, not derived from these items.
        assert body["savings_usd"] == "0.00"

    async def test_total_requests_sums_across_multiple_jobs(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        """total_requests is a tenant-wide sum across ALL jobs, not just the latest one."""
        r1 = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("m1"), _line_item("m2")]},
            headers=_bearer(owner["key"]),
        )
        assert r1.status_code == 200
        r2 = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("m3")]},
            headers=_bearer(owner["key"]),
        )
        assert r2.status_code == 200
        await _await_all_batch_tasks(app)

        resp = await client.get("/admin/batches/stats", headers=_bearer(owner["jwt"]))
        assert resp.status_code == 200
        assert resp.json()["total_requests"] == 3


# ---------------------------------------------------------------------------
# Scenario: tenant isolation — stats never leak another tenant's batch activity
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def test_stats_scoped_to_calling_tenant_only(
        self, client: Any, app: Any, owner: dict[str, str]
    ) -> None:
        other = await _signup_owner(
            client,
            tenant_name="BatchStatsOther",
            email="batch-stats-other@example.io",
            password="batch-stats-other-battery",
        )

        submit = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("iso1"), _line_item("iso2")]},
            headers=_bearer(owner["key"]),
        )
        assert submit.status_code == 200
        await _await_all_batch_tasks(app)

        other_resp = await client.get("/admin/batches/stats", headers=_bearer(other["jwt"]))
        assert other_resp.status_code == 200
        other_body = other_resp.json()
        assert other_body["total_requests"] == 0, "must never see the other tenant's items"
        assert other_body["status_counts"] == dict.fromkeys(_ITEM_VOCAB, 0)

        owner_resp = await client.get("/admin/batches/stats", headers=_bearer(owner["jwt"]))
        assert owner_resp.status_code == 200
        assert owner_resp.json()["total_requests"] == 2
