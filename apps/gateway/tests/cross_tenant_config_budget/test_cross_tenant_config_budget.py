"""RED suite for cross-tenant-config-budget (TASK.md §3 CONTRACT FROZEN @ v1).

Covers the 6 superadmin-only cross-tenant routes under
/admin/platform/tenants/{tenant_id}/{cache,guardrails,budget} — GET/PUT for each of the 3
existing self-service resources (cache_router.py, guardrail_router.py, budgets/api/router.py),
extended with a target tenant_id path segment instead of the caller's own identity.tenant_id.

RED before BUILD: `platform_tenant_config_router.py` does not exist yet. This directory's own
conftest.py imports it at module scope to register it onto the test `app` (see that file's
docstring for why main.py itself is never touched) — so EVERY test in this file fails at
collection time with ImportError until the router module exists, the missing-implementation
reason for this suite (in place of the per-route 404 platform_tenant_directory's RED phase
used, since that task's router WAS registered directly in main.py by its own build).

Superadmin/owner identities are minted directly via app.state.token_service.issue(...) — no
DB user row needed (mirrors tests/platform_tenant_directory/test_platform_tenant_directory.py).
Target tenants are seeded directly via SQL INSERT — no signup flow needed for tenants this
task's SUPERADMIN caller does not own.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _cache_url(tenant_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/cache"


def _guardrails_url(tenant_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/guardrails"


def _budget_url(tenant_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/budget"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors
    tests/platform_tenant_directory/test_platform_tenant_directory.py's fixture of the
    same name, which mirrors tests/superadmin_login/test_superadmin_login.py)."""
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


async def _get_tenant(db_session: AsyncSession, tenant_id: uuid.UUID) -> Any:
    """Fetch a TenantRow via the SAME repository function the router under test uses —
    avoids the raw-SQL JSONB str-vs-dict ambiguity `_fetch_guardrail_configs` defends
    against (TASK.md §0 GROUND) when a test needs to read back guardrail_configs."""
    from gateway.tenants.infrastructure.repository import get_tenant_by_id

    return await get_tenant_by_id(db_session, tenant_id)


async def _seed_customer_tenant(
    db_session: AsyncSession,
    *,
    name: str,
    cache_enabled: bool = False,
    semantic_cache_enabled: bool = False,
    guardrail_configs: dict[str, Any] | None = None,
    budget_usd_monthly: float | None = None,
    markup_pct: float = 20.0,
) -> uuid.UUID:
    """Insert a kind='customer' tenant row with explicit config/budget/markup columns —
    covers every field this task's 6 routes read or write, plus markup_pct (M9's
    never-exposed field, seeded with a non-default value to prove the response omits it)."""
    tid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO tenants
                (id, name, kind, cache_enabled, semantic_cache_enabled,
                 guardrail_configs, budget_usd_monthly, markup_pct)
            VALUES
                (:id, :name, 'customer', :cache_enabled, :semantic_cache_enabled,
                 :guardrail_configs::jsonb, :budget_usd_monthly, :markup_pct)
            """
        ),
        {
            "id": tid,
            "name": name,
            "cache_enabled": cache_enabled,
            "semantic_cache_enabled": semantic_cache_enabled,
            "guardrail_configs": json.dumps(guardrail_configs or {}),
            "budget_usd_monthly": budget_usd_monthly,
            "markup_pct": markup_pct,
        },
    )
    await db_session.commit()
    return tid


async def _seed_usage_record(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, cost_usd: str
) -> None:
    """Insert a minimal usage_records row costing `cost_usd`, timestamped `now()` (current
    month, matching the budget GET route's date_trunc('month', now()) window)."""
    await db_session.execute(
        text(
            """
            INSERT INTO usage_records (id, tenant_id, key_id, model_id, cost_usd, status, raw)
            VALUES (:id, :tenant_id, :key_id, :model_id, :cost_usd, :status, :raw::jsonb)
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "key_id": uuid.uuid4(),
            "model_id": "gpt-test",
            "cost_usd": cost_usd,
            "status": 200,
            "raw": json.dumps({}),
        },
    )
    await db_session.commit()


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
# M1 — superadmin views a target tenant's cache config
# ---------------------------------------------------------------------------


async def test_superadmin_views_target_tenant_cache_config(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="CacheCo", cache_enabled=True, semantic_cache_enabled=False
    )

    resp = await client.get(
        _cache_url(tid), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True, "semantic_enabled": False}


# ---------------------------------------------------------------------------
# M4 — superadmin updates a target tenant's cache config, partial-update semantics
# ---------------------------------------------------------------------------


async def test_superadmin_updates_target_tenant_cache_partial(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="CachePartialCo", cache_enabled=False, semantic_cache_enabled=False
    )

    resp = await client.put(
        _cache_url(tid),
        json={"enabled": True},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True, "semantic_enabled": False}

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.cache_enabled is True
    assert row.semantic_cache_enabled is False  # absent field was not touched


# ---------------------------------------------------------------------------
# M2 — superadmin views a target tenant's guardrail config
# ---------------------------------------------------------------------------


async def test_superadmin_views_target_tenant_guardrail_config(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session,
        name="GuardCo",
        guardrail_configs={"prompt_injection": {"enabled": True, "mode": "block"}},
    )

    resp = await client.get(
        _guardrails_url(tid), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "prompt_injection": {"enabled": True, "mode": "block"},
        "pii_mask": None,
        "ml_moderation": None,
    }


# ---------------------------------------------------------------------------
# M5 — superadmin updates a target tenant's guardrail config, partial-merge semantics
# ---------------------------------------------------------------------------


async def test_superadmin_updates_target_tenant_guardrails_partial_merge(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session,
        name="GuardMergeCo",
        guardrail_configs={"prompt_injection": {"enabled": True, "mode": "block"}},
    )

    resp = await client.put(
        _guardrails_url(tid),
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prompt_injection"] == {"enabled": True, "mode": "block"}
    assert body["pii_mask"] == {"enabled": True, "mode": "mask"}

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.guardrail_configs["prompt_injection"] == {"enabled": True, "mode": "block"}
    assert row.guardrail_configs["pii_mask"] == {"enabled": True, "mode": "mask"}


# ---------------------------------------------------------------------------
# M3 — superadmin views a target tenant's budget and spend-to-date
# ---------------------------------------------------------------------------


async def test_superadmin_views_target_tenant_budget_and_spend(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="BudgetViewCo", budget_usd_monthly=500.00)
    await _seed_usage_record(db_session, tenant_id=tid, cost_usd="120.00")

    resp = await client.get(
        _budget_url(tid), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"budget_usd_monthly": "500.00", "spent_usd_month": "120.00"}


# ---------------------------------------------------------------------------
# M6 — superadmin sets a target tenant's budget ceiling
# ---------------------------------------------------------------------------


async def test_superadmin_sets_target_tenant_budget_ceiling(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="BudgetSetCo")

    resp = await client.put(
        _budget_url(tid),
        json={"budget_usd_monthly": "250.00"},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"budget_usd_monthly": "250.00"}

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert str(row.budget_usd_monthly) == "250.00"


# ---------------------------------------------------------------------------
# M6 — superadmin clears a target tenant's budget ceiling
# ---------------------------------------------------------------------------


async def test_superadmin_clears_target_tenant_budget_ceiling(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="BudgetClearCo", budget_usd_monthly=250.00)

    resp = await client.put(
        _budget_url(tid),
        json={"budget_usd_monthly": None},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"budget_usd_monthly": None}

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.budget_usd_monthly is None


# ---------------------------------------------------------------------------
# R1 — missing bearer token is rejected on every new route
# ---------------------------------------------------------------------------


async def test_missing_bearer_token_rejected_on_every_new_route(
    client: httpx.AsyncClient,
) -> None:
    tid = uuid.uuid4()
    cases: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", _cache_url(tid), None),
        ("PUT", _cache_url(tid), {"enabled": True}),
        ("GET", _guardrails_url(tid), None),
        ("PUT", _guardrails_url(tid), {"prompt_injection": {"enabled": True, "mode": "block"}}),
        ("GET", _budget_url(tid), None),
        ("PUT", _budget_url(tid), {"budget_usd_monthly": "1.00"}),
    ]
    for method, url, json_body in cases:
        resp = await client.request(method, url, json=json_body)
        assert resp.status_code == 401, f"{method} {url}: {resp.text}"
        body = resp.json()
        assert body.get("code") == "ERR_AUTH_INVALID_TOKEN"
        assert "enabled" not in body
        assert "budget_usd_monthly" not in body
        assert "prompt_injection" not in body


# ---------------------------------------------------------------------------
# M7, R2 — non-superadmin rejected even when targeting a different tenant
# ---------------------------------------------------------------------------


async def test_non_superadmin_rejected_targeting_different_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    other_tid = await _seed_customer_tenant(db_session, name="OtherTenantCo")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@crosstenant1.io"
    )

    resp = await client.put(
        _budget_url(other_tid),
        json={"budget_usd_monthly": "999.00"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    row = await _get_tenant(db_session, other_tid)
    assert row is not None
    assert row.budget_usd_monthly is None  # unchanged


# ---------------------------------------------------------------------------
# M7, R2 — non-superadmin rejected even when targeting THEIR OWN tenant via the new route
# ---------------------------------------------------------------------------


async def test_non_superadmin_rejected_targeting_own_tenant_via_new_route(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = await _seed_customer_tenant(db_session, name="OwnTenantCo")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@crosstenant2.io"
    )

    resp = await client.put(
        _budget_url(owner_tenant_id),
        json={"budget_usd_monthly": "999.00"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    # The SAME owner calling the ORIGINAL self-service PUT /admin/budget (no path
    # tenant_id) still succeeds — this route tree is SUPERADMIN-only, not a bypass.
    self_service_resp = await client.put(
        "/admin/budget",
        json={"budget_usd_monthly": "999.00"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert self_service_resp.status_code == 200, self_service_resp.text
    assert self_service_resp.json() == {"budget_usd_monthly": "999.00"}


# ---------------------------------------------------------------------------
# M8, R3 — GET against a nonexistent target tenant_id 404s
# ---------------------------------------------------------------------------


async def test_get_against_nonexistent_tenant_404s(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    missing_id = uuid.uuid4()
    resp = await client.get(
        _guardrails_url(missing_id), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"


# ---------------------------------------------------------------------------
# M8, R3 — PUT against a nonexistent target tenant_id 404s and writes nothing
# ---------------------------------------------------------------------------


async def test_put_against_nonexistent_tenant_404s_and_writes_nothing(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    missing_id = uuid.uuid4()
    resp = await client.put(
        _budget_url(missing_id),
        json={"budget_usd_monthly": "100.00"},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"

    row = await _get_tenant(db_session, missing_id)
    assert row is None  # the UPDATE never ran — no silent 0-row no-op 200


# ---------------------------------------------------------------------------
# R4 — PUT guardrails rejects an invalid mode value
# ---------------------------------------------------------------------------


async def test_put_guardrails_rejects_invalid_mode_value(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="BadModeCo")

    resp = await client.put(
        _guardrails_url(tid),
        json={"prompt_injection": {"enabled": True, "mode": "mask"}},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.guardrail_configs == {}


# ---------------------------------------------------------------------------
# R5 — PUT guardrails rejects a custom pii pattern that fails V1-V7 validation, atomically
# ---------------------------------------------------------------------------


async def test_put_guardrails_rejects_invalid_custom_pattern_atomically(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(
        db_session,
        name="BackrefCo",
        guardrail_configs={"pii_mask": {"enabled": True, "mode": "mask"}},
    )

    resp = await client.put(
        _guardrails_url(tid),
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [{"name": "BADREF", "pattern": r"(\w+)\1"}],
            }
        },
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.guardrail_configs == {"pii_mask": {"enabled": True, "mode": "mask"}}


# ---------------------------------------------------------------------------
# R6 — PUT budget rejects a non-decimal string
# ---------------------------------------------------------------------------


async def test_put_budget_rejects_non_decimal_string(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="NonDecimalCo", budget_usd_monthly=42.00)

    resp = await client.put(
        _budget_url(tid),
        json={"budget_usd_monthly": "not-a-number"},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert str(row.budget_usd_monthly) == "42.00"


# ---------------------------------------------------------------------------
# R7 — PUT budget rejects a negative value
# ---------------------------------------------------------------------------


async def test_put_budget_rejects_negative_value(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="NegativeCo", budget_usd_monthly=42.00)

    resp = await client.put(
        _budget_url(tid),
        json={"budget_usd_monthly": "-10.00"},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert str(row.budget_usd_monthly) == "42.00"


# ---------------------------------------------------------------------------
# R8 — a malformed tenant_id path segment is rejected before any handler logic runs
# ---------------------------------------------------------------------------


async def test_malformed_tenant_id_path_segment_rejected(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    resp = await client.get(
        "/admin/platform/tenants/not-a-uuid/cache",
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    # FastAPI's automatic path-param validation (tenant_id: uuid.UUID) rejects this before
    # the endpoint body — and therefore get_tenant_by_id — ever runs; no bespoke ErrorSpec.
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# M9 — markup_pct never appears in any cross-tenant config/budget response
# ---------------------------------------------------------------------------


async def test_markup_pct_never_appears_in_any_response(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="MarkupCo", markup_pct=35.5)
    headers = {"Authorization": f"Bearer {superadmin_token}"}

    cache_resp = await client.get(_cache_url(tid), headers=headers)
    guardrails_resp = await client.get(_guardrails_url(tid), headers=headers)
    budget_resp = await client.get(_budget_url(tid), headers=headers)

    for resp in (cache_resp, guardrails_resp, budget_resp):
        assert resp.status_code == 200, resp.text
        assert "markup_pct" not in resp.text  # substring check — catches nesting too


# ---------------------------------------------------------------------------
# M10 — cross-tenant budget writes are now audited (admin-console-audit TASK.md §3,
# FROZEN @ v1, closes the regression this task's own §0 GROUND flagged as "unaudited
# today" — see that task's §2 Scenario 1 "closing the flagged regression")
# ---------------------------------------------------------------------------


async def test_cross_tenant_budget_write_is_now_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Updated by admin-console-audit (TASK.md §3, FROZEN @ v1): this route now calls
    emit_platform_audit() on its success path (action="platform.budget.update"), closing
    the regression this test used to pin (0 rows). This is the one sibling-suite test that
    admin-console-audit's own build updates, in a Scope expansion disclosed at that task's
    §5/§6 — the assertion below hard-coded a PRE-admin-console-audit fact this task's own
    frozen contract deliberately changes, not an independent invariant.
    """
    tid = await _seed_customer_tenant(db_session, name="NowAuditedCo")

    resp = await client.put(
        _budget_url(tid),
        json={"budget_usd_monthly": "77.00"},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text

    # Let the fire-and-forget audit write complete (admin-console-audit's own
    # asyncio.sleep(0.05) drain convention — a bare sleep(0) no longer suffices now that a
    # real audit write is expected to finish, not just "prove nothing stray fired").
    await asyncio.sleep(0.05)
    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_events"
                " WHERE tenant_id = :tid AND action = 'platform.budget.update'"
            ),
            {"tid": tid},
        )
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# M11 — the three existing self-service routers remain byte-identical for a non-superadmin
# ---------------------------------------------------------------------------


async def test_self_service_routers_remain_byte_identical_for_non_superadmin(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """With this task's router registered (via this directory's own conftest.py `app`
    override), self-service /admin/cache, /admin/guardrails, /admin/budget behave exactly
    as they did before this task shipped. The file-level half of this guarantee — zero
    lines changed in cache_router.py/guardrail_router.py/budgets/api/router.py/schemas.py —
    is confirmed separately via `git diff` at VERIFY time, not by this test.
    """
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = await _seed_customer_tenant(db_session, name="SelfServiceCo")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@selfservice.io"
    )
    headers = {"Authorization": f"Bearer {owner_token}"}

    cache_get = await client.get("/admin/cache", headers=headers)
    assert cache_get.status_code == 200, cache_get.text
    assert cache_get.json() == {"enabled": False, "semantic_enabled": False}

    cache_put = await client.put("/admin/cache", json={"enabled": True}, headers=headers)
    assert cache_put.status_code == 200, cache_put.text
    assert cache_put.json() == {"enabled": True, "semantic_enabled": False}

    guardrails_get = await client.get("/admin/guardrails", headers=headers)
    assert guardrails_get.status_code == 200, guardrails_get.text
    assert guardrails_get.json() == {
        "prompt_injection": None,
        "pii_mask": None,
        "ml_moderation": None,
    }

    guardrails_put = await client.put(
        "/admin/guardrails",
        json={"prompt_injection": {"enabled": True, "mode": "audit"}},
        headers=headers,
    )
    assert guardrails_put.status_code == 200, guardrails_put.text
    assert guardrails_put.json()["prompt_injection"] == {"enabled": True, "mode": "audit"}

    budget_get = await client.get("/admin/budget", headers=headers)
    assert budget_get.status_code == 200, budget_get.text
    assert budget_get.json() == {"budget_usd_monthly": None, "spent_usd_month": "0.00"}

    budget_put = await client.put(
        "/admin/budget", json={"budget_usd_monthly": "300.00"}, headers=headers
    )
    assert budget_put.status_code == 200, budget_put.text
    assert budget_put.json() == {"budget_usd_monthly": "300.00"}
