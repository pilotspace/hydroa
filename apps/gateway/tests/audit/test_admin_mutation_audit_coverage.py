"""RED->GREEN suite: audit-coverage-remediation (HIGH/LOW findings, fix/audit-remediation).

Covers state-changing admin endpoints that today write NO audit_events row at all:
  - tenants/api/guardrail_router.py           put_guardrails            (HIGH)
  - logs/api/capture_config_router.py         put_capture               (HIGH)
  - tenants/api/platform_service_tier_router.py put_platform_service_tiers (HIGH)
  - tenants/api/rate_card_router.py           put_rate_card_entry / delete_rate_card_entry (LOW)
  - tenants/api/region_pricing_router.py      put_region_pricing_entry / delete_region_pricing_entry (LOW)
  - tenants/api/cache_router.py               put_cache                 (LOW)
  - tenants/api/batch_policy_router.py        put_batch_policy          (LOW)
  - tenants/api/service_tier_router.py        put_default_tier / put_priority_markup (LOW)

Pattern replicated verbatim from teams/api/router.py's `add_member` (the one already-correct
precedent): a fire-and-forget `asyncio.ensure_future(record_audit(request.app.state.sessionmaker,
AuditEvent(...)))` call scheduled AFTER the mutation's own commit — a SEPARATE session/transaction
from the action's own (matches budgets/api/router.py's `budget.update` and proxy/api/
routing_admin_router.py's `routing.update`, both of which use the identical idiom and comment
"NEVER in the action's own transaction").

RED before BUILD: none of the above call sites emit any audit_events row today, so every
`fetch_one_audit_row(..., action=...)` assertion below fails with `row is None`.

Fixture/helper provenance (suite-local convention — this repo does not cross-import test
helpers between suites):
  - `signup_and_login` mirrors tests/teams/test_teams_core.py's own helper.
  - `fetch_one_audit_row` / `count_audit_rows` / `_drain_fire_and_forget` mirror
    tests/admin_console_audit/test_admin_console_audit.py's own helpers.
  - `superadmin_token` / `platform_tenant_id` mirror the same file's fixtures (needed for the
    one FLEET-WIDE, cross-tenant route in this package: platform_service_tier_router).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id)."""
    sr = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action`, or None."""
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "
            "target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def count_audit_rows(session: AsyncSession, *, action: str) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
        {"action": action},
    )
    return int(result.scalar() or 0)


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete."""
    await asyncio.sleep(0.05)


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
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


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=platform_tenant_id,
        role=Role.SUPERADMIN,
        email="root@auditcoverage.internal",
    )
    return token


@pytest.fixture
def superadmin_user_id(app: Any, superadmin_token: str) -> uuid.UUID:
    identity = app.state.token_service.decode(superadmin_token)
    return identity.user_id  # type: ignore[no-any-return]


# ===========================================================================
# guardrail_router.py — put_guardrails
# ===========================================================================


async def test_put_guardrails_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="GuardrailAuditCo", email="owner@guardrailaudit.io"
    )

    resp = await client.put(
        "/admin/guardrails",
        json={"prompt_injection": {"enabled": True, "mode": "block"}},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="guardrails.update")
    assert row is not None, "expected exactly one guardrails.update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "guardrails"
    assert target_id == "config"
    assert result == "success"
    assert metadata == {"fields_changed": ["prompt_injection"]}


# ===========================================================================
# capture_config_router.py — put_capture
# ===========================================================================


async def test_put_capture_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="CaptureAuditCo", email="owner@captureaudit.io"
    )

    resp = await client.put("/admin/capture", json={"enabled": True}, headers=_bearer(jwt))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="capture.update")
    assert row is not None, "expected exactly one capture.update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "capture"
    assert target_id == "config"
    assert result == "success"
    assert metadata == {"enabled": True}


# ===========================================================================
# platform_service_tier_router.py — put_platform_service_tiers (FLEET-WIDE)
# ===========================================================================


async def test_put_platform_service_tiers_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    resp = await client.put(
        "/admin/platform/service-tiers",
        json={"cluster_cap": 42},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.service_tier.update")
    assert row is not None, "expected exactly one platform.service_tier.update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert row_tenant_id is None, "fleet-wide config has no single target tenant"
    assert actor_user_id == superadmin_user_id
    assert target_type == "service_tier"
    assert target_id == "config"
    assert result == "success"
    assert metadata.get("cluster_cap") == 42


# ===========================================================================
# rate_card_router.py — put_rate_card_entry / delete_rate_card_entry
# ===========================================================================


async def test_put_rate_card_entry_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RateCardAuditCo", email="owner@ratecardaudit.io"
    )

    resp = await client.put(
        "/admin/rate-cards/gpt-4o-audit-test",
        json={"markup_pct": "15.0000"},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="rate_card.upsert")
    assert row is not None, "expected exactly one rate_card.upsert audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "rate_card"
    assert target_id == "gpt-4o-audit-test"
    assert result == "success"
    assert metadata == {"markup_pct": "15.0000"}


async def test_delete_rate_card_entry_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RateCardDeleteAuditCo", email="owner@ratecarddeleteaudit.io"
    )
    put_resp = await client.put(
        "/admin/rate-cards/gpt-4o-audit-delete-test",
        json={"markup_pct": "10.0000"},
        headers=_bearer(jwt),
    )
    assert put_resp.status_code == 200, put_resp.text
    await _drain_fire_and_forget()

    resp = await client.delete(
        "/admin/rate-cards/gpt-4o-audit-delete-test", headers=_bearer(jwt)
    )
    assert resp.status_code == 204, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="rate_card.delete")
    assert row is not None, "expected exactly one rate_card.delete audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, _metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "rate_card"
    assert target_id == "gpt-4o-audit-delete-test"
    assert result == "success"


# ===========================================================================
# region_pricing_router.py — put_region_pricing_entry / delete_region_pricing_entry
# ===========================================================================


async def test_put_region_pricing_entry_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RegionPricingAuditCo", email="owner@regionpricingaudit.io"
    )

    resp = await client.put(
        "/admin/region-pricing/eu",
        json={"multiplier": "1.2500"},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="region_pricing.upsert")
    assert row is not None, "expected exactly one region_pricing.upsert audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "region_pricing"
    assert target_id == "eu"
    assert result == "success"
    assert metadata == {"multiplier": "1.2500"}


async def test_delete_region_pricing_entry_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RegionPricingDeleteAuditCo", email="owner@regionpricingdeleteaudit.io"
    )
    put_resp = await client.put(
        "/admin/region-pricing/eu",
        json={"multiplier": "1.1000"},
        headers=_bearer(jwt),
    )
    assert put_resp.status_code == 200, put_resp.text
    await _drain_fire_and_forget()

    resp = await client.delete("/admin/region-pricing/eu", headers=_bearer(jwt))
    assert resp.status_code == 204, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="region_pricing.delete")
    assert row is not None, "expected exactly one region_pricing.delete audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, _metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "region_pricing"
    assert target_id == "eu"
    assert result == "success"


# ===========================================================================
# cache_router.py — put_cache
# ===========================================================================


async def test_put_cache_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="CacheAuditCo", email="owner@cacheaudit.io"
    )

    resp = await client.put(
        "/admin/cache", json={"enabled": True, "semantic_enabled": True}, headers=_bearer(jwt)
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="cache.update")
    assert row is not None, "expected exactly one cache.update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "cache"
    assert target_id == "config"
    assert result == "success"
    assert metadata == {"enabled": True, "semantic_enabled": True}


# ===========================================================================
# batch_policy_router.py — put_batch_policy
# ===========================================================================


async def test_put_batch_policy_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="BatchPolicyAuditCo", email="owner@batchpolicyaudit.io"
    )

    resp = await client.put("/admin/batch-policy", json={"enabled": True}, headers=_bearer(jwt))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="batch_policy.update")
    assert row is not None, "expected exactly one batch_policy.update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "batch_policy"
    assert target_id == "config"
    assert result == "success"
    assert metadata == {"enabled": True}


# ===========================================================================
# service_tier_router.py — put_default_tier / put_priority_markup
# ===========================================================================


async def test_put_default_tier_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="ServiceTierAuditCo", email="owner@servicetieraudit.io"
    )

    resp = await client.put(
        "/admin/service-tiers/default-tier",
        json={"default_tier": "priority"},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="service_tier.default_tier_update")
    assert row is not None, "expected exactly one service_tier.default_tier_update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "service_tier"
    assert target_id == "default_tier"
    assert result == "success"
    assert metadata == {"default_tier": "priority"}


async def test_put_priority_markup_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="PriorityMarkupAuditCo", email="owner@prioritymarkupaudit.io"
    )

    resp = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": "30.0000"},
        headers=_bearer(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="service_tier.priority_markup_update")
    assert row is not None, "expected exactly one service_tier.priority_markup_update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "service_tier"
    assert target_id == "priority_markup"
    assert result == "success"
    assert metadata == {"markup_pct": "30.0000"}


# ===========================================================================
# Audit-write failure never blocks the caller's HTTP response (fail-open, M12-style)
# ===========================================================================


async def test_audit_write_failure_never_blocks_put_cache_response(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Mirrors admin_console_audit's own M12 proof: a failing audit-write sessionmaker
    (2nd+ call) must never change the route's own HTTP outcome — the audit write happens
    on a SEPARATE session/transaction from the route's own `Depends(get_session)`."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="CacheAuditFailOpenCo", email="owner@cacheauditfailopen.io"
    )

    real_sessionmaker = app.state.sessionmaker
    call_count = {"n": 0}

    def _fail_after_first_call(*args: object, **kwargs: object) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_sessionmaker(*args, **kwargs)
        raise RuntimeError("audit db unreachable (simulated outage)")

    app.state.sessionmaker = _fail_after_first_call
    try:
        resp = await client.put("/admin/cache", json={"enabled": True}, headers=_bearer(jwt))
        assert resp.status_code == 200, resp.text
        await _drain_fire_and_forget()
    finally:
        app.state.sessionmaker = real_sessionmaker

    assert call_count["n"] >= 2, (
        "expected at least 2 sessionmaker calls (request's own get_session + the "
        "fire-and-forget audit write) — got fewer, meaning the audit call was never reached"
    )
