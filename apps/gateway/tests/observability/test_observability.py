"""Failing-first (RED) suite for observability (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/observability/TASK.md §2.

Design notes:
  - All tests use FastAPI TestClient via httpx.ASGITransport — zero real infra.
  - Log capture uses a custom structlog test processor (list accumulator) injected
    via gateway.observability.logging_config.configure_structlog(processor=...) —
    this is the BUILD hook; tests fail RED because gateway.observability does not exist yet.
  - Metrics assertions parse the Prometheus text body as plain text (HELP/TYPE/#/metric lines).
  - Fake Redis (FakeRedis) replaces app.state.redis_client for flusher-lag tests.
  - Per-app CollectorRegistry is injected at create_app(); tests must not share a registry.

RED reason expected: ModuleNotFoundError: No module named 'gateway.observability'
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

# ── The import that must fail RED ──────────────────────────────────────────
# All tests in this module exercise the gateway.observability module which does
# not yet exist.  The suite is expected to fail with ModuleNotFoundError or
# AttributeError during collection / setup for every test.
from gateway.observability.logging_config import configure_structlog  # RED
from gateway.observability.metrics import MetricsRegistry  # RED
from gateway.observability.middleware import RequestIdMiddleware  # RED

# ── Re-use existing helpers ────────────────────────────────────────────────
from gateway.core.config import Settings
from gateway.main import create_app
from tests import _redis_env


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeRedisForMetrics:
    """Fake Redis that satisfies xlen() for pending-events tests."""

    def __init__(self, xlen_value: int = 0) -> None:
        self._xlen_value = xlen_value
        # Minimal stubs so create_app() infra doesn't explode during test setup.
        self._store: dict[str, Any] = {}

    async def xlen(self, key: str) -> int:
        return self._xlen_value

    async def xadd(self, *args: Any, **kwargs: Any) -> bytes:
        return b"0-0"

    async def incrbyfloat(self, key: str, value: float) -> float:
        current = float(self._store.get(key, 0))
        result = current + value
        self._store[key] = result
        return result

    async def get(self, key: str) -> bytes | None:
        v = self._store.get(key)
        return str(v).encode() if v is not None else None

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def xack(self, *args: Any, **kwargs: Any) -> int:
        return 1

    async def aclose(self) -> None:
        pass


class BrokenRedisForMetrics(FakeRedisForMetrics):
    """Fake Redis whose xlen() raises — simulates Redis down during scrape."""

    async def xlen(self, key: str) -> int:
        raise ConnectionError("Redis unavailable (BrokenRedisForMetrics)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metrics_settings() -> Settings:
    """Minimal Settings that bypass real DB/Redis for metrics-only tests."""
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url=_redis_env.TEST_REDIS_URL,
    )


def _make_app(
    *,
    redis: Any = None,
    log_capture: list[dict[str, Any]] | None = None,
) -> Any:
    """Create an isolated app instance for observability tests.

    - Injects a per-app CollectorRegistry (the build must wire this).
    - Optionally injects a fake Redis for XLEN control.
    - Optionally injects a list accumulator for log capture.
    """
    settings = _metrics_settings()
    app = create_app(settings)

    # Build hook: these attributes must be set by the build.
    # If gateway.observability doesn't exist, the module-level imports above
    # already cause RED at collection time — these lines are belt-and-suspenders.
    assert hasattr(app.state, "metrics_registry"), (
        "Build must set app.state.metrics_registry (a MetricsRegistry instance)"
    )

    if redis is not None:
        app.state.redis_client = redis
        # Also propagate to usage_recorder and budget_guard that hold a redis ref
        app.state.usage_recorder._redis = redis  # type: ignore[attr-defined]

    if log_capture is not None:
        # Build must expose a way to inject a test processor into structlog.
        # Calling configure_structlog with a capture list is the contracted hook.
        configure_structlog(test_capture=log_capture)

    return app


def _http_client(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _find_metric_line(body: str, metric_name: str) -> list[str]:
    """Return all non-comment lines in a Prometheus body that start with metric_name."""
    return [
        line
        for line in body.splitlines()
        if line.startswith(metric_name) and not line.startswith("#")
    ]


def _metric_value(line: str) -> float:
    """Parse the float value at the end of a Prometheus metric line."""
    parts = line.rsplit(" ", 1)
    return float(parts[-1])


# ---------------------------------------------------------------------------
# §2 Must — M1: access log line per request with required fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_log_fields() -> None:
    """M1 — every request produces a JSON access log line with required fields."""
    captured: list[dict[str, Any]] = []
    app = _make_app(log_capture=captured)

    async with _http_client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200

    # Find the access log entry for this request
    access_lines = [e for e in captured if e.get("event") == "http_request"]
    assert len(access_lines) >= 1, f"Expected http_request log line; got: {captured}"

    entry = access_lines[-1]
    assert "method" in entry, f"Missing 'method' in log: {entry}"
    assert "path" in entry, f"Missing 'path' in log: {entry}"
    assert "status_code" in entry, f"Missing 'status_code' in log: {entry}"
    assert "duration_ms" in entry, f"Missing 'duration_ms' in log: {entry}"
    assert "request_id" in entry, f"Missing 'request_id' in log: {entry}"

    # request_id must be a valid UUID4 string
    rid = entry["request_id"]
    parsed = uuid.UUID(rid)
    assert parsed.version == 4, f"request_id must be UUID4, got version {parsed.version}"

    # duration_ms must be non-negative
    assert float(entry["duration_ms"]) >= 0.0


# ---------------------------------------------------------------------------
# §2 Must — M2: tenant_id present on authed paths, absent on public paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_id_present_on_authenticated_path(
    app: Any,
    client: httpx.AsyncClient,
) -> None:
    """M2 — tenant_id appears in access log for /v1/* paths after auth succeeds.

    Uses the existing conftest `app` + `client` fixtures (real Postgres/Redis
    on compose stack).  We sign up, get a key, make a proxy call, assert log.
    """
    captured: list[dict[str, Any]] = []
    configure_structlog(test_capture=captured)

    # Signup → login → create key via the CANONICAL /admin/auth routes.
    # (Disposition 2026-06-11: the original arrange posted to /tenants/signup,
    # a route that has never existed — assertions below are unchanged.)
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ObsM2Co",
            "email": "obs-m2@example.com",
            "password": "Password1!",
        },
    )
    assert signup.status_code == 201
    tenant_id = signup.json()["tenant_id"]

    login = await client.post(
        "/admin/auth/login",
        json={"email": "obs-m2@example.com", "password": "Password1!"},
    )
    token = login.json()["access_token"]

    key_resp = await client.post(
        "/admin/keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "obs-m2-key"},
    )
    assert key_resp.status_code == 201
    raw_key = key_resp.json()["key"]

    # Use fake upstream to avoid real OpenRouter calls
    from tests.proxy.test_proxy_completions import FakeCompletionUpstream, FakeUsageRecorder

    app.state.completion_upstream = FakeCompletionUpstream()  # type: ignore[attr-defined]
    app.state.usage_recorder = FakeUsageRecorder()  # type: ignore[attr-defined]

    captured.clear()
    await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )

    access_lines = [e for e in captured if e.get("event") == "http_request"]
    assert any(
        str(e.get("tenant_id")) == str(tenant_id) for e in access_lines
    ), f"Expected tenant_id={tenant_id} in access log; captured: {access_lines}"


@pytest.mark.asyncio
async def test_tenant_id_absent_on_public_path() -> None:
    """M2 / R2 — tenant_id must NOT appear in access log for /health."""
    captured: list[dict[str, Any]] = []
    app = _make_app(log_capture=captured)

    async with _http_client(app) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    access_lines = [e for e in captured if e.get("event") == "http_request"]
    assert access_lines, "Expected at least one http_request log line"

    entry = access_lines[-1]
    assert "tenant_id" not in entry, (
        f"tenant_id must be absent on public path but found in: {entry}"
    )
    assert "request_id" in entry, "request_id must be present on all paths"


# ---------------------------------------------------------------------------
# §2 Must/Reject — M3/R1: Authorization header never logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorization_header_never_logged() -> None:
    """M3 / R1 — Authorization header value must not appear in any log line."""
    secret_token = "supersecrettoken99"
    captured: list[dict[str, Any]] = []
    app = _make_app(log_capture=captured)

    async with _http_client(app) as client:
        # Make a request with a recognisable secret in the Authorization header.
        # The endpoint doesn't matter — 401 is fine; we only care about log hygiene.
        await client.get(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {secret_token}"},
        )

    # Search ALL captured log entries (not just access lines) for the secret value
    for entry in captured:
        entry_str = json.dumps(entry)
        assert secret_token not in entry_str, (
            f"Secret token leaked into log entry: {entry_str}"
        )


# ---------------------------------------------------------------------------
# §2 Must — M4: GET /internal/metrics returns 200 Prometheus text 0.0.4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_200_text_format() -> None:
    """M4 — /internal/metrics returns 200 with correct Content-Type."""
    app = _make_app()

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    ct = resp.headers.get("content-type", "")
    assert "text/plain" in ct, f"Expected text/plain content-type; got: {ct}"
    assert "version=0.0.4" in ct, f"Expected version=0.0.4 in Content-Type; got: {ct}"
    assert len(resp.text) > 0, "Metrics body must not be empty"


# ---------------------------------------------------------------------------
# §2 Must — M5: breaker state gauge reflects live state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_breaker_state_closed() -> None:
    """M5 — breaker starts CLOSED; gauge reports 0.0."""
    app = _make_app()
    # Breaker starts in CLOSED state by default
    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    lines = _find_metric_line(resp.text, "gateway_circuit_breaker_state")
    assert lines, f"gateway_circuit_breaker_state not found in:\n{resp.text}"
    assert _metric_value(lines[0]) == 0.0, (
        f"Expected 0.0 (CLOSED) but got: {lines[0]}"
    )


@pytest.mark.asyncio
async def test_metrics_breaker_state_open() -> None:
    """M5 — after tripping the breaker to OPEN, gauge reports 2.0."""
    app = _make_app()
    breaker = app.state.circuit_breaker

    # Trip the breaker: record enough failures to exceed threshold
    from gateway.proxy.infrastructure.circuit_breaker import _FAILURE_THRESHOLD

    for _ in range(_FAILURE_THRESHOLD):
        breaker.record_failure()

    assert breaker.is_open(), "Precondition: breaker must be OPEN after threshold failures"

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    lines = _find_metric_line(resp.text, "gateway_circuit_breaker_state")
    assert lines, f"gateway_circuit_breaker_state not found"
    assert _metric_value(lines[0]) == 2.0, (
        f"Expected 2.0 (OPEN) but got: {lines[0]}"
    )


# ---------------------------------------------------------------------------
# §2 Must — M6: http_requests_total counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_requests_counter_2xx() -> None:
    """M6 — 200 responses increment the counter under the exact status_code label."""
    app = _make_app()

    async with _http_client(app) as client:
        await client.get("/health")  # should yield 200
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    body = resp.text

    # Find lines for method=GET, status_code=200 (label order not assumed)
    pattern = re.compile(
        r'gateway_http_requests_total\{(?=[^}]*method="GET")(?=[^}]*status_code="200")[^}]*\}\s+([\d.eE+]+)'
    )
    matches = pattern.findall(body)
    assert matches, (
        f"Expected gateway_http_requests_total{{method=GET,status_code=200}} in:\n{body}"
    )
    assert float(matches[0]) >= 1.0, f"Counter must be >= 1.0 after one 200 GET; got {matches[0]}"


@pytest.mark.asyncio
async def test_metrics_requests_counter_4xx_exact_code() -> None:
    """M6 — a 4xx rejection increments the counter under its EXACT status code.

    The v2 exit criterion requires the 402 (budget) rate to be observable
    distinctly from 429 (rate limit). Exact-code labelling is what makes
    rate(gateway_http_requests_total{status_code="402"}) expressible; this
    test proves the labelling mechanism using the cheapest 4xx to provoke
    (401 invalid key — no live infra needed).
    """
    app = _make_app()

    async with _http_client(app) as client:
        rejected = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid-key"},
            json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        resp = await client.get("/internal/metrics")

    assert 400 <= rejected.status_code < 500, (
        f"Precondition: invalid key must yield 4xx; got {rejected.status_code}"
    )
    body = resp.text
    pattern = re.compile(
        r'gateway_http_requests_total\{(?=[^}]*status_code="'
        + str(rejected.status_code)
        + r'")[^}]*\}\s+([\d.eE+]+)'
    )
    matches = pattern.findall(body)
    assert matches, (
        f"Expected gateway_http_requests_total{{status_code={rejected.status_code}}} in:\n{body}"
    )
    assert float(matches[0]) >= 1.0


# ---------------------------------------------------------------------------
# §2 Must — M7: flusher_pending_events gauge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_flusher_pending_events_count() -> None:
    """M7 — gauge reflects the Redis stream XLEN value."""
    fake_redis = FakeRedisForMetrics(xlen_value=42)
    app = _make_app(redis=fake_redis)

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    lines = _find_metric_line(resp.text, "gateway_usage_flusher_pending_events")
    assert lines, f"gateway_usage_flusher_pending_events not found in:\n{resp.text}"
    assert _metric_value(lines[0]) == 42.0, (
        f"Expected 42.0 but got: {lines[0]}"
    )


@pytest.mark.asyncio
async def test_metrics_flusher_pending_events_zero_when_empty() -> None:
    """M7 — gauge is 0.0 when stream is empty."""
    fake_redis = FakeRedisForMetrics(xlen_value=0)
    app = _make_app(redis=fake_redis)

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    lines = _find_metric_line(resp.text, "gateway_usage_flusher_pending_events")
    assert lines, "gauge not found"
    assert _metric_value(lines[0]) == 0.0


# ---------------------------------------------------------------------------
# §2 Must — M8: request_duration_seconds histogram
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_duration_histogram_present() -> None:
    """M8 — histogram bucket/sum/count lines present after at least one request."""
    app = _make_app()

    async with _http_client(app) as client:
        await client.get("/health")
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    body = resp.text
    assert "gateway_request_duration_seconds_bucket" in body, (
        f"_bucket lines missing from:\n{body}"
    )
    assert "gateway_request_duration_seconds_sum" in body, (
        f"_sum line missing"
    )
    assert "gateway_request_duration_seconds_count" in body, (
        f"_count line missing"
    )


# ---------------------------------------------------------------------------
# §2 Must — M10 / R4: per-app registry prevents duplicate-registration crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_app_registry_no_duplicate_error() -> None:
    """M10 / R4 — two create_app() calls in the same process must not raise ValueError."""
    app_a = _make_app()
    app_b = _make_app()  # second create_app() — must not raise

    async with _http_client(app_a) as ca:
        resp_a = await ca.get("/internal/metrics")
    async with _http_client(app_b) as cb:
        resp_b = await cb.get("/internal/metrics")

    assert resp_a.status_code == 200, f"App A metrics failed: {resp_a.text}"
    assert resp_b.status_code == 200, f"App B metrics failed: {resp_b.text}"


# ---------------------------------------------------------------------------
# §2 Reject — R3: metrics 200 even when Redis XLEN fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_200_when_redis_xlen_fails() -> None:
    """R3 — /internal/metrics returns 200 and sentinel -1.0 when Redis XLEN raises."""
    broken_redis = BrokenRedisForMetrics()
    app = _make_app(redis=broken_redis)

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200, f"Expected 200 even with broken Redis; got {resp.status_code}"
    body = resp.text

    # Sentinel -1.0 for unavailable
    lines = _find_metric_line(body, "gateway_usage_flusher_pending_events")
    assert lines, "gauge must still appear on Redis failure"
    assert _metric_value(lines[0]) == -1.0, (
        f"Expected -1.0 sentinel on Redis failure; got: {lines[0]}"
    )

    # Other metrics must still be present
    assert "gateway_circuit_breaker_state" in body, (
        "breaker gauge must still be present when Redis is down"
    )
    assert "gateway_http_requests_total" in body, (
        "requests counter must still be present when Redis is down"
    )


# ---------------------------------------------------------------------------
# §2 Reject — R5: metrics 200 regardless of DB state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_200_regardless_of_db_state() -> None:
    """R5 — /internal/metrics must return 200 even when DB is not reachable.

    We use _make_app() (no real DB session needed for the metrics endpoint)
    and verify the response. The metrics endpoint must never touch Postgres.
    """
    app = _make_app()

    async with _http_client(app) as client:
        resp = await client.get("/internal/metrics")

    assert resp.status_code == 200
    assert "gateway_circuit_breaker_state" in resp.text, (
        "breaker gauge must appear without DB"
    )


# ---------------------------------------------------------------------------
# Additional: R2 / M2 combined — tenant_id absence (alias of public-path test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_id_absent_on_health_path() -> None:
    """R2 — access log on /health must not contain tenant_id key."""
    captured: list[dict[str, Any]] = []
    app = _make_app(log_capture=captured)

    async with _http_client(app) as client:
        await client.get("/health")

    access_lines = [e for e in captured if e.get("event") == "http_request"]
    assert access_lines, "Expected at least one access log line"
    for entry in access_lines:
        assert "tenant_id" not in entry, (
            f"tenant_id must not appear on /health log: {entry}"
        )
