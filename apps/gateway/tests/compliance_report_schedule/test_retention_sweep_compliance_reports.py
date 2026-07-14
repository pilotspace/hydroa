"""RED->GREEN suite for RetentionSweeper's compliance_report_runs extension
(compliance-report-center TASK.md §3 M21 — FROZEN @ v1, additive to
gateway.usage.application.retention_sweep.RetentionSweeper).

Scenarios (one test per bullet):
  - tenant-window pass: a compliance_report_runs row older than
    tenants.retention_window_days is purged (row + its s3 object); a fresh row for the
    same tenant, and any row for a tenant with retention_window_days IS NULL, survive
  - ZDR purge pass: EVERY compliance_report_runs row for a zdr_enabled=true tenant is
    purged unconditionally (regardless of age/window), object store delete called
  - object-store delete failure defers the row (never orphans bytes, never crashes
    the sweep) — mirrors the existing artifacts purge-pass precedent exactly
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.compliance_report_schedule.conftest import (
    FakeObjectStore,
    make_sweeper_with_store,
    set_retention_window,
    set_tenant_zdr,
    signup_tenant,
)

pytestmark = pytest.mark.asyncio


def _settings_for(**overrides: Any) -> Any:
    """Minimal settings-like object — every table-window knob OFF by default so only
    the window-pass/ZDR-pass under test fires (mirrors test_retention_sweep.py's own
    `_settings_for` verbatim)."""
    s = MagicMock()
    s.retention_check_interval_seconds = overrides.get("interval", 86400)
    s.retention_usage_records_days = overrides.get("usage_days", 0)
    s.retention_alert_events_days = overrides.get("alert_days", 0)
    s.retention_audit_events_days = overrides.get("audit_days", 0)
    s.retention_audit_floor_days = overrides.get("audit_floor", 0)
    s.retention_batch_size = overrides.get("batch_size", 1000)
    return s


async def _seed_run(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    object_key: str,
    generated_at: datetime.datetime,
) -> str:
    row_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO compliance_report_runs"
            " (id, tenant_id, period_start, period_end, generated_at, object_key,"
            "  size_bytes, format_version, source)"
            " VALUES (:id, :tid, :ps, :pe, :ga, :key, 10, '1', 'scheduled')"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "ps": generated_at - datetime.timedelta(days=31),
            "pe": generated_at,
            "ga": generated_at,
            "key": object_key,
        },
    )
    await db_session.commit()
    return row_id


async def _row_exists(db_session: AsyncSession, row_id: str) -> bool:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM compliance_report_runs WHERE id = :id"), {"id": row_id}
    )
    return bool(result.scalar())


async def test_window_pass_purges_aged_report_and_its_object(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    await set_retention_window(db_session, tenant_id, days=30)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    old_key = f"compliance-reports/{tenant_id}/old.json"
    fresh_key = f"compliance-reports/{tenant_id}/fresh.json"
    old_id = await _seed_run(
        db_session,
        tenant_id=tenant_id,
        object_key=old_key,
        generated_at=now - datetime.timedelta(days=45),
    )
    fresh_id = await _seed_run(
        db_session,
        tenant_id=tenant_id,
        object_key=fresh_key,
        generated_at=now - datetime.timedelta(days=1),
    )

    store = FakeObjectStore()
    await store.put(old_key, b"old", "application/json")
    await store.put(fresh_key, b"fresh", "application/json")

    sweeper = make_sweeper_with_store(app, _settings_for(), object_store=store)
    result = await sweeper.sweep_once()

    assert result.get("compliance_report_runs", 0) == 1, result
    assert not await _row_exists(db_session, old_id), "the aged row must be purged"
    assert await _row_exists(db_session, fresh_id), "the fresh row must survive"
    assert old_key in store.deleted
    assert fresh_key not in store.deleted
    assert old_key not in store.store
    assert fresh_key in store.store


async def test_window_pass_ignores_tenant_with_no_retention_window(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    # retention_window_days left NULL (unset) — window pass must never touch this tenant.
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    key = f"compliance-reports/{tenant_id}/ancient.json"
    row_id = await _seed_run(
        db_session,
        tenant_id=tenant_id,
        object_key=key,
        generated_at=now - datetime.timedelta(days=3650),
    )

    store = FakeObjectStore()
    await store.put(key, b"x", "application/json")
    sweeper = make_sweeper_with_store(app, _settings_for(), object_store=store)
    result = await sweeper.sweep_once()

    assert result.get("compliance_report_runs", 0) == 0
    assert await _row_exists(db_session, row_id)


async def test_zdr_purge_pass_removes_every_report_regardless_of_age(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(
        client, tenant_name="ZdrCo", email="owner@zdrco.example"
    )
    await set_tenant_zdr(db_session, tenant_id, enabled=True)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    key = f"compliance-reports/{tenant_id}/recent.json"
    row_id = await _seed_run(db_session, tenant_id=tenant_id, object_key=key, generated_at=now)

    store = FakeObjectStore()
    await store.put(key, b"x", "application/json")
    sweeper = make_sweeper_with_store(app, _settings_for(), object_store=store)
    result = await sweeper.sweep_once()

    assert result.get("compliance_report_runs", 0) == 1, result
    assert not await _row_exists(db_session, row_id), (
        "a ZDR tenant's compliance report run must be purged unconditionally,"
        " even though it is far younger than any retention window"
    )
    assert key in store.deleted


async def test_object_store_delete_failure_defers_row(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    _owner, tenant_id = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    await set_retention_window(db_session, tenant_id, days=30)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    key = f"compliance-reports/{tenant_id}/stuck.json"
    row_id = await _seed_run(
        db_session,
        tenant_id=tenant_id,
        object_key=key,
        generated_at=now - datetime.timedelta(days=45),
    )

    store = FakeObjectStore()
    await store.put(key, b"x", "application/json")
    store.fail_keys.add(key)

    sweeper = make_sweeper_with_store(app, _settings_for(), object_store=store)
    result = await sweeper.sweep_once()
    assert isinstance(result, dict), "sweep_once must never raise, even on object-delete failure"
    assert await _row_exists(db_session, row_id), (
        "a row whose object delete failed must be DEFERRED, never dropped"
    )

    # outage clears -> next tick recovers
    store.fail_keys.discard(key)
    await sweeper.sweep_once()
    assert not await _row_exists(db_session, row_id)
    assert key in store.deleted
