"""RED suite for drift-alert (TASK.md §4, scenarios DA1-DA7).

ReconciliationDriftChecker is a periodic, OPERATOR-WIDE leak monitor: check_once() reconciles
today's UTC window across all tenants and, when unbilled_upstream_cost > threshold, emits ONE
deduped system event (event_type='reconciliation_drift', tenant_id NULL) via the existing
alert_events + dispatcher seam. Default-OFF (both knobs 0). check_once/run_forever never raise.

RED before BUILD: gateway.usage.application.drift_checker does not exist yet → the module import
below fails (ImportError) — the honest missing-implementation red for the whole suite.
"""

from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import Any

import httpx

from gateway.usage.application.drift_checker import ReconciliationDriftChecker

from .conftest import (
    BoomSessionFactory,
    count_usage_rows,
    get_drift_rows,
    seed_unbilled,
    signup_tenant,
    today_dedupe_key,
)
from tests import _redis_env

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.

_TEST_DB_URL = _redis_env.TEST_DATABASE_URL
_TEST_JWT = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# DA1 — crossing the threshold fires exactly one alert
# ---------------------------------------------------------------------------
async def test_da1_crossing_fires_one(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A1", email="d1@drift.io")
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("2.00"))
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("3.00"))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()

    rows = await get_drift_rows(app.state.sessionmaker)
    assert len(rows) == 1, f"expected exactly one drift alert; got {len(rows)}"
    row = rows[0]
    assert row["event_type"] == "reconciliation_drift"
    assert row["tenant_id"] is None  # system event
    assert row["key_id"] is None
    assert row["dedupe_key"] == today_dedupe_key()
    assert row["delivered_at"] is None  # undelivered → the existing dispatcher picks it up


# ---------------------------------------------------------------------------
# DA2 — below the threshold fires nothing (reject), ledger untouched
# ---------------------------------------------------------------------------
async def test_da2_below_threshold_silent(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A2", email="d2@drift.io")
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("0.50"))
    await db_session.commit()
    before = await count_usage_rows(app.state.sessionmaker)

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()

    assert await get_drift_rows(app.state.sessionmaker) == []
    assert await count_usage_rows(app.state.sessionmaker) == before  # ledger unchanged


# ---------------------------------------------------------------------------
# DA3 — a second crossing the same day is deduped to one alert
# ---------------------------------------------------------------------------
async def test_da3_same_day_deduped(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A3", email="d3@drift.io")
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("5.00"))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()
    await checker.check_once()  # same UTC day → ON CONFLICT dedup

    rows = await get_drift_rows(app.state.sessionmaker)
    assert len(rows) == 1, f"second crossing the same day must dedup to one row; got {len(rows)}"


# ---------------------------------------------------------------------------
# DA4 — the monitor is operator-wide (aggregates across tenants)
# ---------------------------------------------------------------------------
async def test_da4_operator_wide(client: Any, db_session: Any, app: Any) -> None:
    tid_a, kid_a = await signup_tenant(client, tenant_name="Drift A4a", email="d4a@drift.io")
    tid_b, kid_b = await signup_tenant(client, tenant_name="Drift A4b", email="d4b@drift.io")
    # each tenant alone is below the 1.00 threshold; operator-wide sum 1.20 crosses it
    await seed_unbilled(db_session, tenant_id=tid_a, key_id=kid_a, provider_cost=Decimal("0.60"))
    await seed_unbilled(db_session, tenant_id=tid_b, key_id=kid_b, provider_cost=Decimal("0.60"))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()

    rows = await get_drift_rows(app.state.sessionmaker)
    assert len(rows) == 1, (
        "operator-wide leak (0.60 + 0.60 > 1.00) must fire even though no single tenant crosses"
    )


# ---------------------------------------------------------------------------
# DA5 — the alert payload carries the leak numbers and window
# ---------------------------------------------------------------------------
async def test_da5_payload_numbers(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A5", email="d5@drift.io")
    for amt in ("2.00", "2.00", "1.00"):  # 3 rows summing 5.00
        await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal(amt))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()

    rows = await get_drift_rows(app.state.sessionmaker)
    assert len(rows) == 1
    payload = rows[0]["payload"]
    # money is NUMERIC(20,10)/(14,8) so SUM carries the column scale -> compare by VALUE
    # (Decimal), not exact string form ("5.0000000000" != "5.00" though numerically equal).
    assert Decimal(payload["unbilled_upstream_cost"]) == Decimal("5.00")
    assert payload["unbilled_rows"] == 3
    # §3 REQUIRES a `drift` field: drift = sum(provider_cost) - sum(billed) over the provider
    # rows; here all 3 rows are unbilled provider rows so drift == unbilled == 5.00.
    assert "drift" in payload, "the §3 contract requires a `drift` field in the alert payload"
    assert Decimal(payload["drift"]) == Decimal("5.00")
    assert payload["threshold"] == "1.00"
    assert "window_from" in payload and "window_to" in payload


# ---------------------------------------------------------------------------
# DA6 — a check-cycle error is swallowed, never raised (reject)
# ---------------------------------------------------------------------------
async def test_da6_check_error_swallowed(app: Any) -> None:
    checker = ReconciliationDriftChecker(
        session_factory=BoomSessionFactory(), threshold=Decimal("1.00")
    )
    # must NOT raise even though the session factory blows up on use
    await checker.check_once()

    # and no alert row was written for the failed cycle
    assert await get_drift_rows(app.state.sessionmaker) == []


# ---------------------------------------------------------------------------
# DA7 — default-OFF: the checker is not started unless both knobs are set
# ---------------------------------------------------------------------------
async def test_da7_default_off_not_wired() -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app

    # default knobs (reconciliation_drift_threshold=0, reconciliation_check_interval_seconds=0)
    settings = Settings(environment="test", jwt_secret=_TEST_JWT, database_url=_TEST_DB_URL)
    assert settings.reconciliation_drift_threshold == Decimal("0")
    assert settings.reconciliation_check_interval_seconds == 0

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/health")
        assert resp.status_code == 200
        sentinel = object()
        drift_task = getattr(app.state, "drift_checker_task", sentinel)
        assert drift_task is None, (
            f"drift_checker_task must be None when knobs are 0 (default-off); got {drift_task!r}"
        )
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# DA8 — at EXACTLY the threshold fires nothing (strict `>`, not `>=`)
# ---------------------------------------------------------------------------
async def test_da8_at_threshold_does_not_fire(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A8", email="d8@drift.io")
    # unbilled-upstream sum == threshold exactly → must NOT fire (contract says EXCEEDS)
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("1.00"))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    await checker.check_once()

    assert await get_drift_rows(app.state.sessionmaker) == [], (
        "at-exactly-threshold must not fire — the trigger is strictly greater-than, not >="
    )


# ---------------------------------------------------------------------------
# DA9 — run_forever() invokes check_once() and cancels cleanly (never raises)
# ---------------------------------------------------------------------------
async def test_da9_run_forever_ticks_then_cancels(client: Any, db_session: Any, app: Any) -> None:
    tid, kid = await signup_tenant(client, tenant_name="Drift A9", email="d9@drift.io")
    await seed_unbilled(db_session, tenant_id=tid, key_id=kid, provider_cost=Decimal("5.00"))
    await db_session.commit()

    checker = ReconciliationDriftChecker(
        session_factory=app.state.sessionmaker, threshold=Decimal("1.00")
    )
    task = asyncio.create_task(checker.run_forever(interval_seconds=0.02))
    try:
        # the first loop iteration must emit the alert; poll up to ~2s to avoid timing flakiness
        for _ in range(100):
            if await get_drift_rows(app.state.sessionmaker):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError(
                "run_forever did not emit within the timeout — check_once not invoked"
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task  # cancellation must surface as CancelledError only — never a real error

    # repeated loop iterations stay deduped to one row
    assert len(await get_drift_rows(app.state.sessionmaker)) == 1


# ---------------------------------------------------------------------------
# DA10 — default-OFF guard predicate: start ONLY when BOTH knobs are > 0
# ---------------------------------------------------------------------------
def test_da10_should_start_truth_table() -> None:
    from gateway.usage.application.drift_checker import should_start_drift_checker

    assert should_start_drift_checker(Decimal("0"), 0) is False  # both off (the default)
    assert should_start_drift_checker(Decimal("5"), 0) is False  # interval off → still off
    assert should_start_drift_checker(Decimal("0"), 60) is False  # threshold off → still off
    assert should_start_drift_checker(Decimal("5"), 60) is True  # both on → start
