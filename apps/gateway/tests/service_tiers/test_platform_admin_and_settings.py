"""RED-first suite for service-tiers R3 (superadmin-only), R5 (malformed
config), M13 (live reconfiguration without a restart), and the REQUIRED
startup warning (service-tiers TASK.md §4, contract FROZEN @ v1, DECIDED
at freeze-review 2026-07-12).

RED reason before BUILD: /admin/platform/service-tiers is not mounted (404);
create_app never emits the startup warning; RedisTierCapacityGuard has no
reconfigure() to prove live-without-restart.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.proxy.infrastructure.tier_capacity_guard import RedisTierCapacityGuard
from gateway.tenants.domain.entities import Role
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver
from tests.service_tiers.conftest import assert_problem, bearer
from tests import _redis_env


# ---------------------------------------------------------------------------
# R3 — the platform route is superadmin-only; even a tenant OWNER is 403
# ---------------------------------------------------------------------------


async def test_tenant_owner_403_on_platform_route(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    resp = await client.get("/admin/platform/service-tiers", headers=bearer(api_key["jwt"]))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    put_resp = await client.put(
        "/admin/platform/service-tiers",
        json={"cluster_cap": 100},
        headers=bearer(api_key["jwt"]),
    )
    assert_problem(put_resp, 403, "ERR_AUTH_FORBIDDEN")


async def test_superadmin_can_read_and_write_platform_route(
    client: httpx.AsyncClient, superadmin_token: str
) -> None:
    get_resp = await client.get("/admin/platform/service-tiers", headers=bearer(superadmin_token))
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["cluster_cap"] == 0  # default = disabled

    put_resp = await client.put(
        "/admin/platform/service-tiers",
        json={"cluster_cap": 50, "priority_reserved_pct": 0.3, "standard_reserved_pct": 0.3},
        headers=bearer(superadmin_token),
    )
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["cluster_cap"] == 50
    assert Decimal(body["priority_reserved_pct"]) == Decimal("0.3")
    assert Decimal(body["standard_reserved_pct"]) == Decimal("0.3")


# ---------------------------------------------------------------------------
# R5 — the live-write path REJECTS a >1.0 pct sum loudly (422); env boot coerces
# ---------------------------------------------------------------------------


async def test_platform_route_rejects_pct_sum_over_one(
    client: httpx.AsyncClient, superadmin_token: str
) -> None:
    resp = await client.put(
        "/admin/platform/service-tiers",
        json={"priority_reserved_pct": 0.7, "standard_reserved_pct": 0.5},
        headers=bearer(superadmin_token),
    )
    assert resp.status_code == 422, resp.text

    unchanged = await client.get("/admin/platform/service-tiers", headers=bearer(superadmin_token))
    assert Decimal(unchanged.json()["priority_reserved_pct"]) == Decimal("0.2")
    assert Decimal(unchanged.json()["standard_reserved_pct"]) == Decimal("0.2")


async def test_platform_route_rejects_merged_sum_over_one_against_stale_partner(
    client: httpx.AsyncClient, superadmin_token: str
) -> None:
    first = await client.put(
        "/admin/platform/service-tiers",
        json={"priority_reserved_pct": 0.6},
        headers=bearer(superadmin_token),
    )
    assert first.status_code == 200, first.text

    second = await client.put(
        "/admin/platform/service-tiers",
        json={"standard_reserved_pct": 0.6},
        headers=bearer(superadmin_token),
    )
    assert second.status_code == 422, second.text


def test_env_boot_coerces_invalid_pct_sum_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        settings = Settings(
            database_url=TEST_DATABASE_URL,
            jwt_secret=TEST_JWT_SECRET,
            redis_url=_redis_env.TEST_REDIS_URL,
            tier_priority_reserved_pct=0.7,  # type: ignore[call-arg]
            tier_standard_reserved_pct=0.5,  # type: ignore[call-arg]
        )
    assert settings.tier_priority_reserved_pct == 0.20
    assert settings.tier_standard_reserved_pct == 0.20
    assert any("INVALID_TIER_RESERVED_PCT_SUM" in r.message for r in caplog.records)


def test_env_boot_coerces_negative_pct_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        settings = Settings(
            database_url=TEST_DATABASE_URL,
            jwt_secret=TEST_JWT_SECRET,
            redis_url=_redis_env.TEST_REDIS_URL,
            tier_priority_reserved_pct=-0.5,  # type: ignore[call-arg]
        )
    assert settings.tier_priority_reserved_pct == 0.20
    assert any("INVALID_TIER_RESERVED_PCT" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Startup warning — fires exactly when cluster_cap>0 and cluster_cap<max_concurrent
# ---------------------------------------------------------------------------


def _boot_with_capture(settings: Settings) -> list[dict[str, Any]]:
    """create_app() emits the startup warning via structlog, not stdlib logging —
    caplog cannot see it (structlog's default sink prints straight to stdout).
    configure_structlog(test_capture=...) is the contracted test hook (mirrors
    tests/observability/test_observability.py). Force _configured=True first via a
    bare call so create_app()'s own internal bare configure_structlog() call
    (test_capture=None) becomes an idempotent no-op and does not clobber this
    capture sink before the startup-warning log line fires.
    """
    from gateway.observability.logging_config import configure_structlog

    configure_structlog()
    captured: list[dict[str, Any]] = []
    configure_structlog(test_capture=captured)
    create_app(settings)
    return captured


def test_startup_warns_when_cluster_cap_below_max_concurrent_requests() -> None:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        tier_capacity_cluster_cap=5,  # type: ignore[call-arg]
        max_concurrent_requests=10,  # type: ignore[call-arg]
    )
    captured = _boot_with_capture(settings)
    assert any(
        e.get("event") == "tier_capacity_cluster_cap_below_max_concurrent_requests"
        for e in captured
    )


def test_startup_does_not_warn_when_cluster_cap_sufficient() -> None:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        tier_capacity_cluster_cap=20,  # type: ignore[call-arg]
        max_concurrent_requests=10,  # type: ignore[call-arg]
    )
    captured = _boot_with_capture(settings)
    assert not any(
        e.get("event") == "tier_capacity_cluster_cap_below_max_concurrent_requests"
        for e in captured
    )


def test_startup_does_not_warn_when_tiering_disabled() -> None:
    """cluster_cap=0 (the default, disabled) has nothing to misconfigure against."""
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        max_concurrent_requests=10,  # type: ignore[call-arg]
    )
    captured = _boot_with_capture(settings)
    assert not any(
        e.get("event") == "tier_capacity_cluster_cap_below_max_concurrent_requests"
        for e in captured
    )


# ---------------------------------------------------------------------------
# M13 — superadmin-adjustable split takes effect live, without a worker restart
# ---------------------------------------------------------------------------


async def test_platform_put_reconfigures_live_guard_without_restart() -> None:
    """A REAL RedisTierCapacityGuard, wired at boot with a small cap, is live-mutated
    by PUT /admin/platform/service-tiers — the very NEXT admission decision (no new
    process, no app.state.tier_capacity_guard reassignment) must honor the new cap.
    """
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        tier_capacity_cluster_cap=2,  # type: ignore[call-arg]
        tier_priority_reserved_pct=0.5,  # type: ignore[call-arg]
        standard_reserved_pct=0.0,  # type: ignore[call-arg]
        public_signup_enabled=True,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    install_stub_resolver(app)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    guard = app.state.tier_capacity_guard
    assert isinstance(guard, RedisTierCapacityGuard)
    assert guard._priority_cap == 1  # round(2 * 0.5)

    from gateway.tenants.infrastructure.repository import get_platform_tenant

    async with app.state.sessionmaker() as session:
        tenant = await get_platform_tenant(session)
        if tenant is None:
            platform_tenant_id = uuid.uuid4()
            await session.execute(
                text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
                {"id": platform_tenant_id},
            )
            await session.commit()
        else:
            platform_tenant_id = tenant.id

    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=platform_tenant_id,
        role=Role.SUPERADMIN,
        email="root@platform.internal",
    )

    redis_client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL)
    await redis_client.flushdb()
    try:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            put_resp = await c.put(
                "/admin/platform/service-tiers",
                json={"cluster_cap": 8, "priority_reserved_pct": 0.5, "standard_reserved_pct": 0.0},
                headers=bearer(token),
            )
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()
        await engine.dispose()

    # The SAME guard instance was mutated in place — no restart, no reassignment.
    assert guard._priority_cap == 4  # round(8 * 0.5)
    assert put_resp.status_code == 200, put_resp.text
