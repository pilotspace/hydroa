"""RED suite for platform-tenant-directory (TASK.md §3 CONTRACT FROZEN @ v1).

Covers GET /admin/platform/tenants (list) and GET /admin/platform/tenants/{tenant_id}
(get-one) — the superadmin-only, cross-tenant directory. Wires authorize_tenant_scope's
first real caller (get-one, which has a natural target_tenant_id) and introduces a new
role-only require_superadmin dependency (bulk list, which has no single target).

RED before BUILD: neither route exists yet (404), require_superadmin does not exist yet
(ImportError), TENANT_NOT_FOUND does not exist yet (ImportError).

Superadmin/owner identities are minted directly via app.state.token_service.issue(...) —
no DB user row needed, since these tests exercise the directory endpoints, not the login
flow itself (mirrors tests/superadmin_login/test_superadmin_login.py Scenarios 4/5, which
mirrors tests/rbac_roles/test_rbac_roles.py::_issue_token).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors
    tests/superadmin_login/test_superadmin_login.py's fixture of the same name)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


async def _seed_customer_tenant(
    db_session: AsyncSession,
    *,
    name: str,
    markup_pct: float | None = None,
    budget_usd_monthly: float | None = None,
) -> uuid.UUID:
    """Insert a kind='customer' tenant row, optionally with config/budget set — the M2 test
    needs one WITH config set, to prove the directory response excludes those fields."""
    tid = uuid.uuid4()
    if markup_pct is not None or budget_usd_monthly is not None:
        await db_session.execute(
            text(
                """
                INSERT INTO tenants (id, name, kind, markup_pct, budget_usd_monthly)
                VALUES (:id, :name, 'customer', :markup_pct, :budget)
                """
            ),
            {
                "id": tid,
                "name": name,
                "markup_pct": markup_pct if markup_pct is not None else 20.0,
                "budget": budget_usd_monthly,
            },
        )
    else:
        await db_session.execute(
            text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
            {"id": tid, "name": name},
        )
    await db_session.commit()
    return tid


def _issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


# ---------------------------------------------------------------------------
# M1 — bulk list returns every tenant across both kinds
# ---------------------------------------------------------------------------


async def test_superadmin_lists_every_tenant_across_kinds(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    platform_tenant_id: uuid.UUID,
    superadmin_token: str,
) -> None:
    await _seed_customer_tenant(db_session, name="Tenant One")
    await _seed_customer_tenant(db_session, name="Tenant Two")
    await _seed_customer_tenant(db_session, name="Tenant Three")

    resp = await client.get(
        "/admin/platform/tenants", headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {t["id"] for t in body["tenants"]}
    assert str(platform_tenant_id) in ids
    assert len(ids) >= 4  # platform + the 3 customer tenants just seeded
    kinds = {t["kind"] for t in body["tenants"]}
    assert "platform" in kinds
    assert "customer" in kinds


# ---------------------------------------------------------------------------
# M2 — list entry is the directory-summary shape only
# ---------------------------------------------------------------------------


async def test_list_entry_is_directory_summary_shape_only(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="ConfiguredCo", markup_pct=25.0, budget_usd_monthly=500
    )

    resp = await client.get(
        "/admin/platform/tenants", headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    entry = next(t for t in resp.json()["tenants"] if t["id"] == str(tid))
    assert set(entry.keys()) == {"id", "name", "kind", "created_at"}


# ---------------------------------------------------------------------------
# M3 — search narrows by name substring; limit caps the page
# ---------------------------------------------------------------------------


async def test_search_and_limit_narrow_results(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    await _seed_customer_tenant(db_session, name="Acme Corp")
    await _seed_customer_tenant(db_session, name="Acme Labs")
    await _seed_customer_tenant(db_session, name="Globex")

    resp = await client.get(
        "/admin/platform/tenants",
        params={"q": "acme", "limit": 1},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["tenants"]) == 1
    assert "acme" in body["tenants"][0]["name"].lower()


# ---------------------------------------------------------------------------
# M4 — superadmin opens any single tenant, not just their own
# ---------------------------------------------------------------------------


async def test_superadmin_opens_any_single_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    other_id = await _seed_customer_tenant(db_session, name="SomeOtherTenant")

    resp = await client.get(
        f"/admin/platform/tenants/{other_id}",
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(other_id)
    assert body["name"] == "SomeOtherTenant"


# ---------------------------------------------------------------------------
# M5, R2 — bulk list rejects a non-superadmin even with full permissions in their own tenant
# ---------------------------------------------------------------------------


async def test_bulk_list_rejects_non_superadmin_owner(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@ownerco.io"
    )

    resp = await client.get(
        "/admin/platform/tenants", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    # T_owner's own tenant-scoped surface remains unaffected by this new gate.
    users_resp = await client.get(
        "/admin/users", headers={"Authorization": f"Bearer {owner_token}"}
    )
    assert users_resp.status_code == 200, users_resp.text


# ---------------------------------------------------------------------------
# M6, R2 — get-one rejects a non-superadmin targeting a tenant that isn't their own
# ---------------------------------------------------------------------------


async def test_get_one_rejects_non_superadmin_targeting_other_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    other_tenant_id = await _seed_customer_tenant(db_session, name="NotYourTenant")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@ownerco2.io"
    )

    other_resp = await client.get(
        f"/admin/platform/tenants/{other_tenant_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert other_resp.status_code == 403, other_resp.text
    assert other_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    # This route tree is SUPERADMIN-only — unlike authorize_tenant_scope's general
    # same-tenant allowance, even the caller's OWN tenant_id 403s here.
    own_resp = await client.get(
        f"/admin/platform/tenants/{owner_tenant_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert own_resp.status_code == 403, own_resp.text


# ---------------------------------------------------------------------------
# R1 — missing bearer token is rejected
# ---------------------------------------------------------------------------


async def test_missing_bearer_token_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/admin/platform/tenants")
    assert resp.status_code == 401, resp.text
    assert resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"
    assert "tenants" not in resp.json()


# ---------------------------------------------------------------------------
# R3 — get-one against a tenant_id that does not exist
# ---------------------------------------------------------------------------


async def test_get_one_nonexistent_tenant_404s(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    missing_id = uuid.uuid4()
    resp = await client.get(
        f"/admin/platform/tenants/{missing_id}",
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"


# ---------------------------------------------------------------------------
# R4 — a limit above the maximum clamps rather than erroring
# ---------------------------------------------------------------------------


async def test_limit_above_max_clamps_not_errors(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    for i in range(5):
        await _seed_customer_tenant(db_session, name=f"ClampCo{i}")

    resp = await client.get(
        "/admin/platform/tenants",
        params={"limit": 9999},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["tenants"]) <= 200
