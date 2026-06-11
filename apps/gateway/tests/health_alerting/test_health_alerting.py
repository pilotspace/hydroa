"""Failing-first (RED) suite for health-alerting (contract DRAFT, TASK.md §4).

One test per scenario in TASK.md §2 SCENARIOS.

Right-reason RED targets per test (summary — see inline per test):
  S01: ImportError — gateway.alerting.application.dispatcher does not exist yet
  S02: ImportError — same; RETRY_MAX semantics not implemented
  S03: ImportError — AlertDispatcher does not exist; webhook_url="" idle path not built
  S04: ImportError — payload schema not implemented
  S05: ImportError — URL logging redaction not implemented
  S06: ImportError — circuit_breaker has no event-emission hook; alerting module absent
  S07: ImportError — same; episode deduplication not implemented
  S08: ImportError — drain_until_empty has no drain_timeout emission yet
  S09: ImportError — gateway.alerting.application.health_checker does not exist yet
  S10: ImportError — health_checker module absent; recovery event not implemented
  S11: ImportError — same; new-episode logic not implemented
  S12: ImportError — AlertDispatcher retry-then-succeed not implemented
  S13: AttributeError — Settings has no alert_webhook_url / alert_retry_max fields yet;
       also migration M15 not applied (tenant_id still NOT NULL)
  S14: AttributeError — Settings missing three new fields
  S15: AttributeError/KeyError — Settings missing health_check_interval_seconds; lifespan
       does not yet wire HealthChecker; app.state.health_checker_task absent

All arrangements use CANONICAL routes for HTTP setup where needed:
  /admin/auth/signup, /admin/auth/login, /admin/keys
Database: real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
Redis DB index 7 (no collision with existing suites: ops=default, key_gov=9, rate_limits=8, spend=9-alt)

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - httpx.ASGITransport (no network) for HTTP-arranged tests
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
  - Fakes: FakeWebhookSink (records POSTs), FakeUpstreamPinger (configurable pass/fail)
  - Real DB for all DB-persisted assertions (confirms schema + dedupe constraints live)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"

_TEST_DB_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"

# ---------------------------------------------------------------------------
# Fakes — injected via ports (WebhookSink / UpstreamPinger Protocols)
# ---------------------------------------------------------------------------


class FakeWebhookSink:
    """Records all POST calls; configurable per-call status sequence."""

    def __init__(self, status_sequence: list[int] | None = None) -> None:
        # status_sequence: status codes returned per call in order; last value repeated
        self._statuses: list[int] = status_sequence if status_sequence is not None else [200]
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        idx = min(len(self.calls), len(self._statuses) - 1)
        status = self._statuses[idx]
        self.calls.append({"url": url, "payload": dict(payload)})
        return status


class AlwaysFailSink(FakeWebhookSink):
    """Always returns 500."""

    def __init__(self) -> None:
        super().__init__(status_sequence=[500])


class RetryThenSucceedSink(FakeWebhookSink):
    """Returns 500 on first call, 200 on subsequent calls."""

    def __init__(self) -> None:
        super().__init__(status_sequence=[500, 200])


class FakeUpstreamPinger:
    """Configurable: always succeed or always fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.call_count = 0

    async def ping(self) -> None:
        self.call_count += 1
        if self._fail:
            raise ConnectionError("fake upstream unreachable")


class SwitchablePinger:
    """Starts failing; can be switched to succeed mid-test."""

    def __init__(self) -> None:
        self.fail = True
        self.call_count = 0

    async def ping(self) -> None:
        self.call_count += 1
        if self.fail:
            raise ConnectionError("fake upstream unreachable")


# ---------------------------------------------------------------------------
# DB fixtures (real Postgres — same pattern as conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine():  # type: ignore[return]
    """Create a fresh-schema engine for the test DB."""
    from gateway.core.db import Base

    engine = create_async_engine(_TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine):  # type: ignore[return]
    """Return an async_sessionmaker bound to the test engine."""
    yield async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_session(session_factory) -> AsyncIterator[AsyncSession]:  # type: ignore[return]
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_alert_row(
    session_factory: Any,
    *,
    event_type: str = "soft_budget_exceeded",
    tenant_id: uuid.UUID | None = None,
    key_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert a minimal alert_events row and return its id."""
    import json

    row_id = uuid.uuid4()
    if dedupe_key is None:
        dedupe_key = f"test:{row_id}"
    if payload is None:
        payload = {"test": "true"}
    # Use a real tenant_id only when supplied; NULL otherwise (requires M15).
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO alert_events"
                " (id, tenant_id, key_id, event_type, payload, dedupe_key)"
                " VALUES (:id, :tenant_id, :key_id, :event_type, :payload::jsonb, :dedupe_key)"
            ),
            {
                "id": str(row_id),
                "tenant_id": str(tenant_id) if tenant_id is not None else None,
                "key_id": str(key_id) if key_id is not None else None,
                "event_type": event_type,
                "payload": json.dumps(payload),
                "dedupe_key": dedupe_key,
            },
        )
        await session.commit()
    return row_id


async def _get_alert_row(session_factory: Any, row_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch one alert_events row by id; return dict or None."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT * FROM alert_events WHERE id = :id"),
            {"id": str(row_id)},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def _count_alert_rows(
    session_factory: Any,
    *,
    event_type: str | None = None,
) -> int:
    """Count alert_events rows, optionally filtered by event_type."""
    if event_type is not None:
        result_iter = (
            await (
                (await session_factory()).__aenter__()
            ).execute(
                text("SELECT COUNT(*) FROM alert_events WHERE event_type = :et"),
                {"et": event_type},
            )
        )
    # Use a simpler pattern consistent with other test helpers
    async with session_factory() as session:
        if event_type is not None:
            result = await session.execute(
                text("SELECT COUNT(*) FROM alert_events WHERE event_type = :et"),
                {"et": event_type},
            )
        else:
            result = await session.execute(text("SELECT COUNT(*) FROM alert_events"))
        count: int = result.scalar_one()
        return count


async def _get_alert_rows_by_type(
    session_factory: Any, event_type: str
) -> list[dict[str, Any]]:
    """Return all alert_events rows with the given event_type."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT * FROM alert_events WHERE event_type = :et ORDER BY created_at"),
            {"et": event_type},
        )
        return [dict(row) for row in result.mappings().all()]


# ============================================================================
# S01 — undelivered row delivered on run_once
# ============================================================================


async def test_s01_undelivered_row_delivered_on_run_once(session_factory: Any) -> None:
    """RED reason: gateway.alerting.application.dispatcher module does not exist yet.
    ImportError on the import line is the expected failure mode.

    Target behavior: AlertDispatcher.run_once() POSTs one undelivered row to the webhook
    sink and sets delivered_at on the row.
    """
    # This import will fail with ImportError until Build phase creates the module.
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    row_id = await _insert_alert_row(
        session_factory,
        event_type="soft_budget_exceeded",
        tenant_id=tenant_id,
        key_id=key_id,
        dedupe_key=f"soft_budget:{key_id}:202606",
        payload={"soft_budget_usd": "10.00", "key_spend_usd": "10.50"},
    )

    sink = FakeWebhookSink(status_sequence=[200])
    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )

    await dispatcher.run_once()

    # Exactly one POST was made
    assert sink.call_count == 1, (
        f"expected 1 POST to webhook sink, got {sink.call_count}"
    )
    # The row now has delivered_at set
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None
    assert row["delivered_at"] is not None, (
        "delivered_at must be set after successful delivery"
    )
    # Row is not deleted
    assert row["id"] is not None


# ============================================================================
# S02 — webhook 500 leaves row undelivered, never drops
# ============================================================================


async def test_s02_webhook_500_leaves_row_undelivered_never_drops(
    session_factory: Any,
) -> None:
    """RED reason: AlertDispatcher module does not exist yet (ImportError).

    Target behavior: 500 responses cause RETRY_MAX POST attempts; row stays undelivered.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    row_id = await _insert_alert_row(session_factory, dedupe_key="s02-test-row")
    sink = AlwaysFailSink()

    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )

    await dispatcher.run_once()

    # Exactly RETRY_MAX attempts
    assert sink.call_count == 3, (
        f"expected exactly 3 POST attempts (retry_max=3), got {sink.call_count}"
    )
    # Row still undelivered
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None, "row must not be deleted on failure"
    assert row["delivered_at"] is None, (
        "delivered_at must remain NULL when all attempts fail"
    )


# ============================================================================
# S03 — no URL → events persist, dispatcher idles without error
# ============================================================================


async def test_s03_no_url_events_persist_dispatcher_idles(session_factory: Any) -> None:
    """RED reason: AlertDispatcher module does not exist yet (ImportError).

    Target behavior: empty webhook_url → dispatcher does nothing; row persists undelivered.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    row_id = await _insert_alert_row(session_factory, dedupe_key="s03-no-url-row")
    sink = FakeWebhookSink(status_sequence=[200])

    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="",  # disabled
        retry_max=3,
    )

    # Must not raise
    await dispatcher.run_once()

    assert sink.call_count == 0, (
        f"dispatcher with empty URL must make no POST calls; got {sink.call_count}"
    )
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None, "row must still exist when dispatcher is disabled"
    assert row["delivered_at"] is None, "delivered_at must remain NULL when dispatcher disabled"


# ============================================================================
# S04 — webhook payload schema is exact
# ============================================================================


async def test_s04_webhook_payload_schema_exact(session_factory: Any) -> None:
    """RED reason: AlertDispatcher module does not exist yet (ImportError).

    Target behavior: posted JSON has exactly the 6 contracted keys, no extras, correct types.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    s04_dedupe = f"s04-schema-{uuid.uuid4()}"
    row_id = await _insert_alert_row(
        session_factory,
        event_type="soft_budget_exceeded",
        tenant_id=tenant_id,
        key_id=key_id,
        dedupe_key=s04_dedupe,
        payload={"soft_budget_usd": "10.00", "key_spend_usd": "10.50"},
    )

    sink = FakeWebhookSink(status_sequence=[200])
    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )

    await dispatcher.run_once()

    assert sink.call_count == 1
    posted: dict[str, Any] = sink.calls[0]["payload"]

    # Exactly these 6 keys — no more, no less
    expected_keys = {"event_id", "event_type", "tenant_id", "key_id", "created_at", "payload"}
    actual_keys = set(posted.keys())
    assert actual_keys == expected_keys, (
        f"webhook payload must have exactly {expected_keys}, got {actual_keys}"
    )

    # Types
    assert isinstance(posted["event_id"], str), "event_id must be a string"
    assert isinstance(posted["event_type"], str), "event_type must be a string"
    assert isinstance(posted["tenant_id"], str), (
        "tenant_id must be serialised as string (not UUID object)"
    )
    assert isinstance(posted["key_id"], str), (
        "key_id must be serialised as string (not UUID object)"
    )
    assert isinstance(posted["created_at"], str), "created_at must be ISO 8601 string"
    assert isinstance(posted["payload"], dict), "payload must be a dict"


# ============================================================================
# S05 — webhook URL never logged in full
# ============================================================================


async def test_s05_webhook_url_not_logged_in_full(session_factory: Any) -> None:
    """RED reason: AlertDispatcher module does not exist yet (ImportError).

    Target behavior: log output contains only the host portion of the webhook URL;
    credential/path/token fragments are not logged.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    # URL with embedded credentials and a token in the path
    webhook_url = "https://user:s3cr3t@hooks.example.com/abc123?token=xyz789"

    await _insert_alert_row(session_factory, dedupe_key="s05-log-test")

    sink = FakeWebhookSink(status_sequence=[200])
    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url=webhook_url,
        retry_max=1,
    )

    # Capture log output from the alerting dispatcher logger
    log_records: list[str] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(self.format(record))

    handler = _CapturingHandler()
    # The dispatcher should log under gateway.alerting or similar
    root_logger = logging.getLogger("gateway")
    root_logger.addHandler(handler)
    try:
        await dispatcher.run_once()
    finally:
        root_logger.removeHandler(handler)

    all_log = " ".join(log_records)

    # Must contain host
    assert "hooks.example.com" in all_log, (
        "log output must contain the webhook host for observability"
    )
    # Must NOT contain secrets / path tokens
    assert "s3cr3t" not in all_log, "password must not appear in log output"
    assert "abc123" not in all_log, "path token must not appear in log output"
    assert "xyz789" not in all_log, "query token must not appear in log output"


# ============================================================================
# S06 — breaker OPEN emits exactly one event per episode
# ============================================================================


async def test_s06_breaker_open_emits_exactly_one_row_per_episode(
    session_factory: Any,
) -> None:
    """RED reason: CircuitBreaker has no event-emission hook and no _open_episode_id.
    ImportError or AttributeError is the expected failure.

    Target behavior: the 5th consecutive failure trips the breaker OPEN and persists
    exactly one circuit_breaker_open row; a 6th call (already OPEN) adds no new row.
    """
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    # CircuitBreaker must accept session_factory for event emission after Build.
    # Currently it does NOT — this will raise TypeError until Build.
    breaker = CircuitBreaker(session_factory=session_factory)  # type: ignore[call-arg]

    # Drive to threshold - 1 failures (threshold=5 per source)
    for _ in range(4):
        breaker.record_failure()

    # 5th failure → OPEN transition → should emit event
    breaker.record_failure()
    # Allow fire-and-forget task to complete
    await asyncio.sleep(0.1)

    rows = await _get_alert_rows_by_type(session_factory, "circuit_breaker_open")
    assert len(rows) == 1, (
        f"expected exactly 1 circuit_breaker_open row after OPEN transition, got {len(rows)}"
    )

    row = rows[0]
    assert row["tenant_id"] is None, "system event must have tenant_id NULL"
    assert row["key_id"] is None, "breaker event must have key_id NULL"
    assert row["dedupe_key"].startswith("breaker_open:"), (
        f"dedupe_key must start with 'breaker_open:'; got {row['dedupe_key']!r}"
    )
    # Validate the episode UUID portion is a valid UUID
    episode_part = row["dedupe_key"].removeprefix("breaker_open:")
    uuid.UUID(episode_part)  # raises ValueError if not valid UUID

    # 6th failure (already OPEN) must not add a second row
    breaker.record_failure()
    await asyncio.sleep(0.1)

    rows_after = await _get_alert_rows_by_type(session_factory, "circuit_breaker_open")
    assert len(rows_after) == 1, (
        f"second record_failure() while OPEN must NOT produce a second row; got {len(rows_after)}"
    )


# ============================================================================
# S07 — new episode after recovery produces a new row with different dedupe_key
# ============================================================================


async def test_s07_breaker_new_episode_after_recovery_produces_new_row(
    session_factory: Any,
) -> None:
    """RED reason: CircuitBreaker has no session_factory parameter (TypeError).

    Target behavior: two separate open episodes produce two rows with different dedupe_keys.
    """
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(session_factory=session_factory)  # type: ignore[call-arg]

    # Episode 1: trip open
    for _ in range(5):
        breaker.record_failure()
    await asyncio.sleep(0.1)

    # Recover to CLOSED
    breaker.record_success()

    # Episode 2: trip open again
    for _ in range(5):
        breaker.record_failure()
    await asyncio.sleep(0.1)

    rows = await _get_alert_rows_by_type(session_factory, "circuit_breaker_open")
    assert len(rows) == 2, (
        f"expected 2 circuit_breaker_open rows (two episodes), got {len(rows)}"
    )

    dedupe_keys = {row["dedupe_key"] for row in rows}
    assert len(dedupe_keys) == 2, (
        f"the two open-episode rows must have DIFFERENT dedupe_keys; got {dedupe_keys}"
    )


# ============================================================================
# S08 — drain timeout emits drain_timeout event
# ============================================================================


async def test_s08_drain_timeout_emits_event(session_factory: Any) -> None:
    """RED reason: drain_until_empty has no event emission logic yet.
    Currently it just logs a warning (no DB write). The assertion that a row
    exists in alert_events will fail because no INSERT is made.

    Target behavior: timeout inside drain_until_empty persists one drain_timeout row.
    """
    import redis.asyncio as aioredis

    from gateway.usage.application.flusher import UsageLedgerFlusher

    # Use real Redis on db 7 (dedicated for this suite)
    redis_client = aioredis.from_url("redis://localhost:6380/7", decode_responses=False)
    await redis_client.flushdb()

    try:
        # Add a pending entry that will never be flushed (no real session for this Redis-only test)
        # The flusher must accept session_factory for the drain_timeout event.
        # Currently it does NOT accept session_factory in drain_until_empty — this is new wiring.
        flusher = UsageLedgerFlusher(
            redis=redis_client,
            session_factory=session_factory,
        )

        # Add one pending entry to ensure drain doesn't complete immediately
        await redis_client.xadd(
            "usage:events",
            {
                "tenant_id": str(uuid.uuid4()),
                "key_id": str(uuid.uuid4()),
                "model_id": "test/model",
                "prompt_tokens": "10",
                "completion_tokens": "5",
                "cost_usd": "0.001",
                "pricing_snapshot_id": str(uuid.uuid4()),
                "status": "200",
                "raw": "{}",
            },
        )

        import time
        start = time.monotonic()
        # Extremely short timeout → guaranteed to time out with pending events
        await flusher.drain_until_empty(timeout=0.01)
        elapsed = time.monotonic() - start

        # Must not block forever
        assert elapsed < 2.0, f"drain_until_empty took {elapsed:.2f}s — should be < 2s"

        # The drain_timeout event must be persisted — this assertion FAILS RED because
        # drain_until_empty currently only logs a warning, does not write to alert_events
        rows = await _get_alert_rows_by_type(session_factory, "drain_timeout")
        assert len(rows) == 1, (
            f"drain_until_empty timeout must persist exactly 1 drain_timeout row; got {len(rows)}"
        )
        row = rows[0]
        assert row["tenant_id"] is None, "drain_timeout event must have tenant_id NULL (requires M15)"
        assert row["key_id"] is None
        assert row["dedupe_key"] == "drain_timeout"
        payload = row["payload"]
        assert "timeout_seconds" in payload, (
            f"drain_timeout payload must contain 'timeout_seconds'; got {payload}"
        )

    finally:
        await redis_client.flushdb()
        await redis_client.aclose()


# ============================================================================
# S09 — 3 consecutive health failures emit one upstream_health_fail event
# ============================================================================


async def test_s09_health_3_consecutive_failures_emit_one_fail_event(
    session_factory: Any,
) -> None:
    """RED reason: gateway.alerting.application.health_checker does not exist (ImportError).

    Target behavior: 3 consecutive ping failures trigger one upstream_health_fail row;
    4th failure (same episode) adds no new row.
    """
    from gateway.alerting.application.health_checker import UpstreamHealthChecker  # type: ignore[import-not-found]

    pinger = FakeUpstreamPinger(fail=True)
    checker = UpstreamHealthChecker(
        session_factory=session_factory,
        pinger=pinger,
        fail_threshold=3,
    )

    # 3 failures → should emit on the 3rd
    for i in range(3):
        await checker.check_once()
        await asyncio.sleep(0.05)

    rows = await _get_alert_rows_by_type(session_factory, "upstream_health_fail")
    assert len(rows) == 1, (
        f"expected exactly 1 upstream_health_fail row after 3 consecutive failures; got {len(rows)}"
    )
    row = rows[0]
    assert row["tenant_id"] is None, "system event must have tenant_id NULL"
    assert row["key_id"] is None
    assert row["dedupe_key"].startswith("health_fail:"), (
        f"dedupe_key must start with 'health_fail:'; got {row['dedupe_key']!r}"
    )
    payload = row["payload"]
    assert payload.get("consecutive_failures") == 3, (
        f"payload.consecutive_failures must be 3; got {payload}"
    )

    # 4th failure (same episode) — must NOT produce a second row
    await checker.check_once()
    await asyncio.sleep(0.05)
    rows_after = await _get_alert_rows_by_type(session_factory, "upstream_health_fail")
    assert len(rows_after) == 1, (
        f"4th failure in same episode must NOT produce a second row; got {len(rows_after)}"
    )


# ============================================================================
# S10 — health recovery emits upstream_health_recovered with matching episode_id
# ============================================================================


async def test_s10_health_recovery_emits_recovered_with_matching_episode_id(
    session_factory: Any,
) -> None:
    """RED reason: health_checker module does not exist (ImportError).

    Target behavior: after a fail episode, one successful ping emits upstream_health_recovered
    with dedupe_key matching the paired fail event's episode UUID.
    """
    from gateway.alerting.application.health_checker import UpstreamHealthChecker  # type: ignore[import-not-found]

    pinger = SwitchablePinger()
    pinger.fail = True

    checker = UpstreamHealthChecker(
        session_factory=session_factory,
        pinger=pinger,
        fail_threshold=3,
    )

    # Trigger fail episode
    for _ in range(3):
        await checker.check_once()
    await asyncio.sleep(0.05)

    fail_rows = await _get_alert_rows_by_type(session_factory, "upstream_health_fail")
    assert len(fail_rows) == 1
    fail_dedupe_key: str = fail_rows[0]["dedupe_key"]
    # episode_id is the UUID suffix after "health_fail:"
    episode_id = fail_dedupe_key.removeprefix("health_fail:")
    uuid.UUID(episode_id)  # validate it's a UUID

    # Now recover
    pinger.fail = False
    await checker.check_once()
    await asyncio.sleep(0.05)

    recovered_rows = await _get_alert_rows_by_type(session_factory, "upstream_health_recovered")
    assert len(recovered_rows) == 1, (
        f"expected exactly 1 upstream_health_recovered row; got {len(recovered_rows)}"
    )
    recovered_row = recovered_rows[0]
    expected_recovered_key = f"health_recovered:{episode_id}"
    assert recovered_row["dedupe_key"] == expected_recovered_key, (
        f"recovered dedupe_key must be {expected_recovered_key!r}; "
        f"got {recovered_row['dedupe_key']!r}"
    )
    payload = recovered_row["payload"]
    assert "recovered_after_failures" in payload, (
        f"recovered payload must contain 'recovered_after_failures'; got {payload}"
    )


# ============================================================================
# S11 — new failure run after recovery starts a new episode
# ============================================================================


async def test_s11_health_new_failure_run_after_recovery_new_episode(
    session_factory: Any,
) -> None:
    """RED reason: health_checker module does not exist (ImportError).

    Target behavior: two separate failure episodes produce two distinct upstream_health_fail rows.
    """
    from gateway.alerting.application.health_checker import UpstreamHealthChecker  # type: ignore[import-not-found]

    pinger = SwitchablePinger()
    pinger.fail = True

    checker = UpstreamHealthChecker(
        session_factory=session_factory,
        pinger=pinger,
        fail_threshold=3,
    )

    # Episode 1
    for _ in range(3):
        await checker.check_once()
    await asyncio.sleep(0.05)

    # Recover
    pinger.fail = False
    await checker.check_once()
    await asyncio.sleep(0.05)

    # Episode 2
    pinger.fail = True
    for _ in range(3):
        await checker.check_once()
    await asyncio.sleep(0.05)

    rows = await _get_alert_rows_by_type(session_factory, "upstream_health_fail")
    assert len(rows) == 2, (
        f"expected 2 upstream_health_fail rows (two episodes); got {len(rows)}"
    )
    dedupe_keys = {row["dedupe_key"] for row in rows}
    assert len(dedupe_keys) == 2, (
        f"the two fail-episode rows must have DIFFERENT dedupe_keys; got {dedupe_keys}"
    )


# ============================================================================
# S12 — webhook retries then succeeds on 2nd attempt
# ============================================================================


async def test_s12_webhook_retries_then_succeeds(session_factory: Any) -> None:
    """RED reason: AlertDispatcher module does not exist (ImportError).

    Target behavior: sink returns 500 on attempt 1 and 200 on attempt 2;
    dispatcher retries and marks row delivered.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher  # type: ignore[import-not-found]

    row_id = await _insert_alert_row(session_factory, dedupe_key="s12-retry-succeed")
    sink = RetryThenSucceedSink()  # 500 first, 200 second

    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )

    await dispatcher.run_once()

    assert sink.call_count == 2, (
        f"expected exactly 2 POST attempts (fail then succeed); got {sink.call_count}"
    )
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None
    assert row["delivered_at"] is not None, (
        "delivered_at must be set after successful 2nd attempt"
    )


# ============================================================================
# S13 — system event with tenant_id NULL accepted by schema (M15 migration)
# ============================================================================


async def test_s13_system_event_tenant_id_null_accepted_by_schema(
    session_factory: Any,
) -> None:
    """RED reason: alert_events.tenant_id is currently NOT NULL.
    The INSERT with tenant_id=NULL will raise IntegrityError/NotNullViolationError.
    This becomes GREEN only after the M15 additive migration is applied.

    Target behavior: tenant_id NULL is accepted for system events (breaker/drain/health).
    """
    from sqlalchemy.exc import IntegrityError

    row_id = uuid.uuid4()
    async with session_factory() as session:
        # This INSERT must succeed after M15; currently it will raise because tenant_id NOT NULL
        await session.execute(
            text(
                "INSERT INTO alert_events"
                " (id, tenant_id, key_id, event_type, payload, dedupe_key)"
                " VALUES (:id, NULL, NULL, :event_type, :payload::jsonb, :dedupe_key)"
            ),
            {
                "id": str(row_id),
                "event_type": "circuit_breaker_open",
                "payload": '{"state": "open"}',
                "dedupe_key": f"breaker_open:{uuid.uuid4()}",
            },
        )
        await session.commit()

    # Row is retrievable with tenant_id IS NULL
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None, "inserted row must be retrievable"
    assert row["tenant_id"] is None, (
        f"tenant_id must be NULL for system events; got {row['tenant_id']!r}"
    )


# ============================================================================
# S14 — Settings has the three new alert/health fields with correct defaults
# ============================================================================


def test_s14_settings_new_fields_defaults() -> None:
    """RED reason: Settings has no alert_webhook_url, alert_retry_max, or
    health_check_interval_seconds fields yet. AttributeError on access.

    Target behavior: three new Settings fields with documented defaults.
    """
    from gateway.core.config import Settings

    # Use a known-safe jwt_secret to avoid the _forbid_dev_secret_outside_dev validator
    s = Settings(
        environment="test",
        jwt_secret="test-secret-not-for-production-0123456789",
        database_url=_TEST_DB_URL,
    )

    # These attributes do not exist yet → AttributeError until Build
    assert s.alert_webhook_url == "", (  # type: ignore[attr-defined]
        f"alert_webhook_url default must be '' (empty = disabled); got {s.alert_webhook_url!r}"  # type: ignore[attr-defined]
    )
    assert s.alert_retry_max == 3, (  # type: ignore[attr-defined]
        f"alert_retry_max default must be 3; got {s.alert_retry_max!r}"  # type: ignore[attr-defined]
    )
    assert s.health_check_interval_seconds == 60, (  # type: ignore[attr-defined]
        f"health_check_interval_seconds default must be 60; got {s.health_check_interval_seconds!r}"  # type: ignore[attr-defined]
    )


# ============================================================================
# S15 — health_check_interval_seconds=0 disables health checker in lifespan
# ============================================================================


async def test_s15_health_check_interval_zero_disables_checker() -> None:
    """RED reason: Settings does not have health_check_interval_seconds field yet.
    Settings(..., health_check_interval_seconds=0) raises a pydantic ValidationError
    or TypeError because the field does not exist, which is the right RED reason for S14.

    Additionally — even if the field existed — the lifespan does not yet wire the
    HealthChecker at all, so app.state.health_checker_task would be absent.
    We assert BOTH:
      1. Settings accepts health_check_interval_seconds=0 (AttributeError/ValidationError now)
      2. When the lifespan runs with interval=0, no health_checker_task is started

    The first assertion fails RED today; the second will fail RED once the field exists
    but the lifespan wiring is absent.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    # Step 1: Settings must accept the new field — this raises AttributeError/ValidationError
    # today because the field does not exist on the Settings class.
    settings = Settings(
        environment="test",
        jwt_secret="test-secret-not-for-production-0123456789",
        database_url=_TEST_DB_URL,
        health_check_interval_seconds=0,  # type: ignore[call-arg]
    )

    # Step 2: Confirm the field value is honoured
    assert settings.health_check_interval_seconds == 0, (  # type: ignore[attr-defined]
        f"health_check_interval_seconds must be 0; got {settings.health_check_interval_seconds!r}"  # type: ignore[attr-defined]
    )

    app = create_app(settings)

    # Step 3: lifespan must not start a health_checker_task when interval=0
    # After Build, app.state.health_checker_task must be None (or absent) when interval=0.
    # Before Build, Settings itself fails at construction above, so this line is unreachable.
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200

        health_task = getattr(app.state, "health_checker_task", _SENTINEL := object())
        # After Build with interval=0: task must be None (checker not started)
        # Before Build: Settings construction fails above; this is never reached
        assert health_task is None, (
            "health_checker_task must be None when health_check_interval_seconds=0; "
            f"got {health_task!r}"
        )

    await app.state.engine.dispose()
