"""Failing-first (RED) suite for readiness/liveness probes.

Scenarios covered (ops-hardening TASK.md §2):
  - Scenario: liveness probe returns 200 without touching any external dependency
  - Scenario: readiness probe returns 200 when Postgres and Redis are both healthy
  - Scenario: readiness probe returns 503 when Postgres is down
  - Scenario: readiness probe returns 503 when Redis is down
  - Scenario: legacy health endpoints unchanged after probe additions

RED reason expected:
  The new routes GET /internal/health/live and GET /internal/health/ready do not
  exist yet in main.py, so all probe tests will get 404 responses and fail.
  The legacy-health tests will pass (those routes exist), confirming RED is for
  the RIGHT reason (new routes missing, not infrastructure broken).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.main import create_app


# ── Minimal fake Redis for probe tests ────────────────────────────────────


class FakeRedisHealthy:
    """Fake Redis that answers PING and xreadgroup without real network."""

    async def ping(self) -> bool:
        return True

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        raise Exception("BUSYGROUP Consumer Group name already exists")

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def xadd(self, *args: Any, **kwargs: Any) -> bytes:
        return b"0-0"

    async def xack(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def xpending(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def incrbyfloat(self, key: str, value: float) -> float:
        return value

    async def aclose(self) -> None:
        pass


class FakeRedisDown:
    """Fake Redis that raises ConnectionError on PING."""

    async def ping(self) -> bool:
        raise ConnectionError("connection refused")

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        raise Exception("BUSYGROUP Consumer Group name already exists")

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def xadd(self, *args: Any, **kwargs: Any) -> bytes:
        return b"0-0"

    async def xack(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def xpending(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def incrbyfloat(self, key: str, value: float) -> float:
        return value

    async def aclose(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════
# Liveness probe: /internal/health/live
# ══════════════════════════════════════════════════════════════════════════


async def test_live_probe_200_no_deps() -> None:
    """GET /internal/health/live must return 200 without touching any dependency.

    RED reason: route /internal/health/live does not exist yet — returns 404.
    """
    settings = Settings(
        # Deliberately use unreachable URLs to prove the probe doesn't connect
        database_url="postgresql+asyncpg://bad:bad@127.0.0.1:19999/nonexistent",
        redis_url="redis://127.0.0.1:19998/0",
    )
    app = create_app(settings)
    # Inject a fake redis so create_app infra doesn't fail on construction
    app.state.redis_client = FakeRedisDown()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/live")

    assert response.status_code == 200, (
        f"Expected 200 from /internal/health/live, got {response.status_code}. "
        "Route does not exist yet (RED expected)."
    )
    body = response.json()
    assert body == {"status": "ok", "service": "gateway"}, (
        f"Unexpected body: {body}"
    )


# ══════════════════════════════════════════════════════════════════════════
# Readiness probe: /internal/health/ready
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
async def test_ready_probe_200_both_healthy() -> None:
    """GET /internal/health/ready must return 200 when both Postgres and Redis are up.

    Requires real Postgres on localhost:5433 and real Redis on localhost:6380.
    RED reason: route does not exist yet.
    """
    settings = Settings()  # defaults to dev localhost addresses
    app = create_app(settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/ready")

    assert response.status_code == 200, (
        f"Expected 200 from /internal/health/ready, got {response.status_code}. "
        "Route does not exist yet (RED expected)."
    )
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_ready_probe_503_db_down() -> None:
    """GET /internal/health/ready must return 503 with db error detail when Postgres is down.

    RED reason: route does not exist yet — returns 404.
    """
    settings = Settings(
        database_url="postgresql+asyncpg://bad:password123@127.0.0.1:19999/nonexistent",
        redis_url="redis://localhost:6380/0",
    )
    app = create_app(settings)
    # Inject a healthy fake redis so the redis check passes
    app.state.redis_client = FakeRedisHealthy()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/ready")

    assert response.status_code == 503, (
        f"Expected 503 from /internal/health/ready with bad DB, got {response.status_code}. "
        "Route does not exist yet (RED expected)."
    )
    body = response.json()
    assert body["status"] == "not_ready"
    assert "error:" in body["checks"]["db"].lower(), (
        f"Expected db check to contain 'error:', got: {body['checks']['db']!r}"
    )
    assert body["checks"]["redis"] == "ok"
    # Credential safety: connection URL password must not appear in response
    assert "password123" not in response.text, (
        "Credentials must not appear in the readiness probe response body"
    )


async def test_ready_probe_503_redis_down() -> None:
    """GET /internal/health/ready must return 503 with redis error detail when Redis is down.

    RED reason: route does not exist yet — returns 404.
    """
    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        redis_url="redis://:secretpassword@127.0.0.1:19998/0",
    )
    app = create_app(settings)
    # Inject a broken redis to guarantee the redis check fails
    app.state.redis_client = FakeRedisDown()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health/ready")

    assert response.status_code == 503, (
        f"Expected 503 from /internal/health/ready with bad Redis, got {response.status_code}. "
        "Route does not exist yet (RED expected)."
    )
    body = response.json()
    assert body["status"] == "not_ready"
    assert "error:" in body["checks"]["redis"].lower(), (
        f"Expected redis check to contain 'error:', got: {body['checks']['redis']!r}"
    )
    # db check: may be ok or error depending on whether local postgres is reachable
    assert "redis" in body["checks"], "checks.redis must be present"
    # Credential safety
    assert "secretpassword" not in response.text, (
        "Redis password must not appear in the readiness probe response body"
    )


# ══════════════════════════════════════════════════════════════════════════
# Legacy health endpoints: must be unchanged
# ══════════════════════════════════════════════════════════════════════════


async def test_legacy_health_unchanged_get_health() -> None:
    """GET /health must still return 200 {"status":"ok","service":"gateway"}.

    This test must pass GREEN even before build (legacy behavior frozen).
    If it fails RED, that is a wrong-reason failure — investigate.
    """
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gateway"}


async def test_legacy_health_unchanged_internal() -> None:
    """GET /internal/health must still return 200 {"status":"ok","service":"gateway"}.

    This test must pass GREEN even before build (legacy behavior frozen).
    """
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gateway"}
