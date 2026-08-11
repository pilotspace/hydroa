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

import asyncio
import datetime
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
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


async def test_zdr_flip_after_recheck_before_atomic_check_persists_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """audit-remediation v2 (closes HOLE 2 — residual TOCTOU between the recheck and
    the INSERT): the early-exit recheck (test above) and the load-bearing atomic
    FOR-UPDATE check further down are two SEPARATE reads. A tenant that flips
    zdr_enabled=true in the gap between them (after the recheck passes, before the
    atomic transaction's own SELECT ... FOR UPDATE) must still persist NOTHING — the
    atomic block's OWN fresh, lock-taking read is what is actually load-bearing, and
    must independently observe the flip and skip, never relying on the earlier
    recheck's now-stale answer."""
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="FlipGapCo", email="owner@flipgapco.example"
    )
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)

    # is_zdr() (the module-level import used by BOTH the M17 up-front gate and the
    # cheap early-exit recheck) is called exactly twice before the atomic block. Flip
    # zdr_enabled=true, on its own committed session, immediately AFTER the 2nd call
    # (the recheck) returns — i.e. exactly in the gap the atomic block's own
    # _is_zdr_locked() read must independently catch.
    import gateway.compliance.application.report_schedule_generator as rsg_module

    orig_is_zdr = rsg_module.is_zdr
    call_count = {"n": 0}

    async def _counting_is_zdr(session: Any, tid: Any) -> bool:
        call_count["n"] += 1
        result = await orig_is_zdr(session, tid)
        if call_count["n"] == 2:
            async with app.state.sessionmaker() as flip_session:
                await set_tenant_zdr(flip_session, tenant_id, enabled=True)
        return result

    rsg_module.is_zdr = _counting_is_zdr  # type: ignore[assignment]
    try:
        counts = await generator.generate_due_schedules()
    finally:
        rsg_module.is_zdr = orig_is_zdr  # type: ignore[assignment]

    assert counts["skipped_zdr"] == 1, counts
    assert counts["success"] == 0, counts
    assert len(store.put_calls) == 0, (
        "the atomic FOR UPDATE check must catch the flip BEFORE the INSERT — the"
        " object PUT must never even be attempted"
    )
    assert len(store.store) == 0, "nothing may be left persisted in the object store"
    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0, "no compliance_report_runs row may be written once ZDR flipped"
    row = await fetch_schedule_row(db_session, tenant_id)
    assert row is not None
    _enabled, _last_run_at, last_run_status, next_run_at = row
    assert last_run_status == "skipped_zdr", (
        "a flip landing between the recheck and the atomic check must record"
        " skipped_zdr, never success"
    )
    assert next_run_at is not None, "next_run_at must still advance (self-healing)"


async def test_zdr_tenant_never_attempts_object_store_put(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """audit-remediation v2 (closes HOLE 1 — the permanent orphan-object leak): the
    old design's ZDR-skip path called ObjectStore.delete() to clean up an
    ALREADY-WRITTEN object, and a delete failure orphaned it forever (no DB row ever
    existed to let the retention sweep rediscover that key). The new design makes
    this failure mode IMPOSSIBLE by construction: a ZDR tenant never reaches
    ObjectStore.put() in the first place. Proven here by configuring the store to
    ALWAYS raise on put() — if the generator ever attempted a put for this ZDR
    tenant, the outcome would be 'deferred' (put failure) or 'failed' (uncaught
    exception), never 'skipped_zdr' with zero put attempts."""
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="ZdrNeverPutCo", email="owner@zdrneverputco.example"
    )
    await set_tenant_zdr(db_session, tenant_id, enabled=True)
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    store.put_fail = True  # would raise ObjectStoreUnavailableError if ever called
    generator = make_generator(app, object_store=store)

    counts = await generator.generate_due_schedules()

    assert counts["skipped_zdr"] == 1, counts
    assert counts["deferred"] == 0, "a ZDR tenant must never reach the PUT call at all"
    assert counts["failed"] == 0
    assert len(store.put_calls) == 0, "ObjectStore.put() must NEVER be invoked for a ZDR tenant"
    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0


async def test_zdr_flip_blocks_on_tenant_row_lock_during_atomic_transaction(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """Concurrency proof of the 'FOR UPDATE path' itself (not just sequential
    ordering): a concurrent zdr_enabled flip, attempted WHILE generate_for_tenant's
    atomic transaction is mid-flight (holding the tenants row lock acquired by
    _is_zdr_locked), must BLOCK on ordinary Postgres row-lock contention until that
    transaction commits or rolls back — it can never observe or affect the
    in-progress decision. Proven by real DB lock contention across two separate
    sessions/connections, not by monkeypatch-injected ordering."""
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="LockRaceCo", email="owner@lockraceco.example"
    )
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)

    events: list[str] = []
    entered_put = asyncio.Event()
    release_put = asyncio.Event()

    orig_put = store.put

    async def _slow_put(key: str, data: bytes, content_type: str) -> None:
        entered_put.set()
        await release_put.wait()
        events.append("put_done")
        await orig_put(key, data, content_type)

    store.put = _slow_put  # type: ignore[method-assign]

    async def _flip_task() -> None:
        await entered_put.wait()  # the atomic tx's FOR UPDATE lock is now held
        async with app.state.sessionmaker() as flip_session:
            # This plain UPDATE must BLOCK until generate_for_tenant's transaction
            # commits/rolls back and releases the row lock.
            await flip_session.execute(
                text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await flip_session.commit()
        events.append("flip_done")

    flip = asyncio.ensure_future(_flip_task())
    gen_task = asyncio.ensure_future(generator.generate_for_tenant(uuid.UUID(tenant_id), 1))

    await asyncio.wait_for(entered_put.wait(), timeout=10)
    # NEGATIVE WAIT: a deliberate race construction. This delay lets the concurrent
    # flip's UPDATE reach Postgres and START BLOCKING on the row lock — the contention
    # window IS the subject of this test. Nothing observable exists to poll for (a
    # blocked UPDATE has no visible state), and removing the gap collapses the race.
    await asyncio.sleep(0.1)  # let the flip's UPDATE reach Postgres and start blocking
    release_put.set()

    outcome = await asyncio.wait_for(gen_task, timeout=10)
    await asyncio.wait_for(flip, timeout=10)

    assert outcome == "success", (
        "the in-flight transaction must complete successfully, unaffected by the"
        " blocked concurrent flip"
    )
    assert "put_done" in events and "flip_done" in events
    assert events.index("put_done") < events.index("flip_done"), (
        "the concurrent flip must NOT complete until AFTER the atomic transaction"
        " resolves — it was blocked on the tenants row lock the whole time"
    )
    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 1, "the report generated before the flip took effect must persist"


async def test_zdr_flip_mid_tick_persists_nothing_toctou(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    """CR (close ZDR TOCTOU, compliance-report-center §3 M17 v2): a tenant that is
    NOT zdr at the up-front M17 check but flips zdr_enabled=true DURING bundle assembly
    — the window between the up-front check and the first persistence (object PUT + row
    INSERT) — must still persist NOTHING. Pre-fix: the single up-front check let the tick
    proceed to write both an object and a row for a now-ZDR tenant. Post-fix: the
    generator re-reads is_zdr immediately before the first write and skips fail-closed."""
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="FlipCo", email="owner@flipco.example"
    )
    # NOT zdr at the up-front check; schedule due now.
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    await set_schedule_due(db_session, tenant_id, day_of_month=1, next_run_at=past)

    store = FakeObjectStore()
    generator = make_generator(app, object_store=store)

    # Simulate the tenant enabling ZDR mid-tick: flip the flag INSIDE _assemble_bundle,
    # which runs AFTER the up-front M17 check and BEFORE any persistence — precisely the
    # TOCTOU window. A separate committed session so the re-check (its own session) sees it.
    orig_assemble = generator._assemble_bundle

    async def _flip_then_assemble(session: Any, **kwargs: Any) -> Any:
        result = await orig_assemble(session, **kwargs)
        async with app.state.sessionmaker() as flip_session:
            await set_tenant_zdr(flip_session, tenant_id, enabled=True)
        return result

    generator._assemble_bundle = _flip_then_assemble  # type: ignore[method-assign]

    counts = await generator.generate_due_schedules()

    assert counts["skipped_zdr"] == 1, counts
    assert counts["success"] == 0, counts
    assert len(store.store) == 0, "no object may be persisted once ZDR flipped mid-tick"
    run_count = await count_report_runs(db_session, tenant_id)
    assert run_count == 0, "no compliance_report_runs row may be written once ZDR flipped mid-tick"
    row = await fetch_schedule_row(db_session, tenant_id)
    assert row is not None
    _enabled, _last_run_at, last_run_status, _next_run_at = row
    assert last_run_status == "skipped_zdr", (
        "a mid-tick ZDR flip must record a skipped_zdr outcome, never success"
    )
