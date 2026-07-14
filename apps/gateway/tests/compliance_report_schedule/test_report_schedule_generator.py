"""RED->GREEN suite for ReportScheduleGenerator (compliance-report-center TASK.md §3
M15-M23 — FROZEN @ v1).

Direct unit-level exercise of the generator (mirrors tests/invoice_generation/
test_generation.py's own `generator.generate_for_tenant(...)` idiom — calling the
per-tenant method directly rather than always going through the due-schedule select).

Scenarios (one test per bullet):
  - a due tick (via generate_due_schedules) generates + persists + inserts a run row
    + advances next_run_at/last_run_status='success'
  - a due tick for a ZDR tenant skips entirely: no object written, no
    compliance_report_runs row, last_run_status='skipped_zdr', next_run_at still
    advances (self-healing)
  - object-store unavailable (put raises) -> 'deferred': schedule row LEFT UNCHANGED
    (retried next tick), no compliance_report_runs row
  - object-store unconfigured (None) -> 'deferred', same as above
  - idempotent re-tick: calling generate_for_tenant twice for the same period never
    double-inserts (ON CONFLICT DO NOTHING, M16/R14)
  - should_start_report_schedule_generator(settings) reflects interval > 0
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.compliance.application.report_schedule_generator import (
    previous_completed_month,
    should_start_report_schedule_generator,
)
from gateway.objectstore.errors import ObjectStoreUnavailableError
from tests.compliance_report_schedule.conftest import (
    FakeObjectStore,
    count_report_runs,
    fetch_schedule_row,
    make_generator,
    seed_audit_event,
    set_schedule_due,
    set_tenant_zdr,
    signup_tenant,
)

pytestmark = pytest.mark.asyncio


async def test_should_start_report_schedule_generator_reflects_interval() -> None:
    class _S:
        compliance_report_schedule_interval_seconds = 0

    assert should_start_report_schedule_generator(_S()) is False

    class _S2:
        compliance_report_schedule_interval_seconds = 3600

    assert should_start_report_schedule_generator(_S2()) is True


async def test_due_tick_generates_persists_run_and_advances(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    period_start, _period_end = previous_completed_month(datetime.datetime.now(datetime.UTC))
    event_ts = period_start + datetime.timedelta(days=1)
    await seed_audit_event(db_session, tenant_id=tenant_id, created_at=event_ts)

    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)
    counts = await generator.generate_due_schedules()

    assert counts["success"] == 1, counts
    assert counts["skipped_zdr"] == 0
    assert counts["deferred"] == 0

    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 1, "exactly one compliance_report_runs row must be inserted"

    assert len(store.store) == 1, "exactly one object must be written to the store"
    (only_key,) = store.store.keys()
    assert only_key.startswith(f"compliance-reports/{tenant_id}/")
    payload = store.store[only_key]
    assert b'"audit_events"' in payload
    assert b'"usage_lineage"' in payload
    assert b'"request_log_metadata"' in payload

    row = await fetch_schedule_row(db_session, tenant_id)
    assert row is not None
    enabled, last_run_at, last_run_status, next_run_at = row
    assert enabled is True
    assert last_run_status == "success"
    assert last_run_at is not None
    assert next_run_at is not None
    assert next_run_at > datetime.datetime.now(datetime.UTC).replace(tzinfo=None), (
        "next_run_at must advance into the future after a successful generation"
    )


async def test_due_tick_zdr_tenant_skips_no_object_no_row(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="ZdrCo", email="owner@zdrco.example"
    )
    await set_tenant_zdr(db_session, tenant_id, enabled=True)
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)
    counts = await generator.generate_due_schedules()

    assert counts["skipped_zdr"] == 1, counts
    assert counts["success"] == 0
    assert len(store.store) == 0, "a ZDR tenant's bundle must NEVER be assembled or written"

    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0, "a ZDR tenant must never get a compliance_report_runs row"

    row = await fetch_schedule_row(db_session, tenant_id)
    assert row is not None
    _enabled, last_run_at, last_run_status, next_run_at = row
    assert last_run_status == "skipped_zdr"
    assert last_run_at is not None
    assert next_run_at is not None, (
        "next_run_at must still advance for a ZDR skip (self-healing next tick)"
    )
    assert next_run_at > datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def test_object_store_unavailable_defers_row_unchanged(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    store.put_fail = True
    generator = make_generator(app, object_store=store)
    counts = await generator.generate_due_schedules()

    assert counts["deferred"] == 1, counts
    assert counts["success"] == 0

    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0, "no row may be inserted when the object put failed"

    row = await fetch_schedule_row(db_session, tenant_id)
    assert row is not None
    _enabled, last_run_at, last_run_status, next_run_at = row
    assert last_run_status is None, "a deferred tick must NOT set last_run_status"
    assert last_run_at is None, "a deferred tick must NOT advance last_run_at"
    assert next_run_at == past.replace(tzinfo=None), (
        "a deferred tick must leave next_run_at UNCHANGED so the same period retries"
    )


async def test_object_store_unconfigured_defers_row_unchanged(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    generator = make_generator(app, object_store=None)
    counts = await generator.generate_due_schedules()

    assert counts["deferred"] == 1, counts
    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0


async def test_idempotent_retick_never_double_inserts(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)

    first = await generator.generate_for_tenant(uuid.UUID(tenant_id), 1)
    assert first == "success"
    second = await generator.generate_for_tenant(uuid.UUID(tenant_id), 1)
    assert second == "success", "a second tick for the same period is still a 'success' outcome"

    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 1, "ON CONFLICT (tenant_id, period_start) DO NOTHING must prevent a 2nd row"


async def test_per_tenant_isolation_one_failure_never_blocks_another(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """One tenant's ZDR-skip and another's success must both complete in the same tick
    (mirrors RetentionSweeper's own per-tenant isolation discipline)."""
    _owner_a, tenant_a = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    _owner_b, tenant_b = await signup_tenant(
        client, tenant_name="Globex", email="owner@globex.example"
    )
    await set_tenant_zdr(db_session, tenant_a, enabled=True)
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_a, day_of_month=1, next_run_at=past)
    await set_schedule_due(db_session, tenant_b, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)
    counts = await generator.generate_due_schedules()

    assert counts["skipped_zdr"] == 1
    assert counts["success"] == 1
    assert await count_report_runs(db_session, tenant_a) == 0
    assert await count_report_runs(db_session, tenant_b) == 1


async def test_object_store_unavailable_error_class_is_caught(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """Direct check that ObjectStoreUnavailableError (not just the boolean put_fail flag)
    is the exact exception type the generator catches around ObjectStore.put()."""
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")

    class _RaisingStore(FakeObjectStore):
        async def put(self, key: str, data: bytes, content_type: str) -> None:
            raise ObjectStoreUnavailableError("outage")

    generator = make_generator(app, object_store=_RaisingStore())
    outcome = await generator.generate_for_tenant(uuid.UUID(tenant_id), 1)
    assert outcome == "deferred"
