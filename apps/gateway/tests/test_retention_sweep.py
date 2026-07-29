"""RED test suite for the data-retention-controls task (TASK.md §4).

Tests MUST fail (ImportError or NameError) BEFORE the build phase because
gateway.usage.application.retention_sweep does not exist yet.

DO NOT change these tests to make them pass — that is the Build phase's job.

Scenarios (one test per TASK.md §2 scenario):
  1. test_deletes_only_aged_rows          — deletes rows older than window; spares younger ones
  2. test_default_off_when_interval_zero  — should_start_retention_sweep returns False when interval=0
  3. test_window_zero_skips_table         — alert_events_days=0 → that table is not touched
  4. test_audit_floor_enforced            — effective_audit_window = max(knob, floor) when knob>0
  5. test_audit_purge_via_bypass_only     — audit DELETE needs SET LOCAL; plain DELETE raises
  6. test_batched_delete                  — 2500 rows deleted across multiple ≤1000-row batches
  7. test_purge_is_audited               — a data.purge audit event is emitted after each table purge
  8. test_sweep_fail_open                 — DB error during DELETE is swallowed; task survives
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncio
import datetime
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from tests._polling import poll_until
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# The imports below will fail until the retention module exists (RED gate).
# ---------------------------------------------------------------------------
from gateway.usage.application.retention_sweep import (
    RetentionSweeper,
    should_start_retention_sweep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"


async def _signup_tenant(client: Any, *, tenant_name: str, email: str) -> tuple[str, str]:
    """Sign up a tenant+owner and create one key; return (tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": "correct horse battery"},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": "correct horse battery"})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token = lr.json()["access_token"]
    kr = await client.post(
        ADMIN_KEYS,
        json={"name": f"{tenant_name}-key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert kr.status_code == 201, f"key creation failed: {kr.text}"
    return tenant_id, kr.json()["key_id"]


async def _insert_usage_row(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    created_at: datetime.datetime,
) -> str:
    """Insert one usage_records row at a controlled created_at. Returns the row id."""
    row_id = str(uuid.uuid4())
    # asyncpg: created_at is TIMESTAMP WITHOUT TIME ZONE — strip tzinfo
    ts = created_at.astimezone(datetime.UTC).replace(tzinfo=None)
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, usage_source)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0, 0, 200, '{}', :ts, 'catalog', 'frame')"
        ),
        {"id": row_id, "tid": tenant_id, "kid": key_id, "mid": "openai/gpt-4o", "ts": ts},
    )
    return row_id


async def _insert_alert_row(
    session: AsyncSession,
    *,
    created_at: datetime.datetime,
) -> str:
    """Insert one alert_events row at a controlled created_at. Returns the row id."""
    row_id = str(uuid.uuid4())
    ts = created_at.astimezone(datetime.UTC).replace(tzinfo=None)
    # dedupe_key is NOT NULL UNIQUE — use the row_id as a unique key per row
    await session.execute(
        text(
            "INSERT INTO alert_events (id, event_type, payload, created_at, dedupe_key)"
            " VALUES (:id, 'test.alert', '{}', :ts, :dk)"
        ),
        {"id": row_id, "ts": ts, "dk": f"test.alert:{row_id}"},
    )
    return row_id


async def _insert_audit_row(
    session: AsyncSession,
    *,
    created_at: datetime.datetime,
) -> str:
    """Insert one audit_events row at a controlled created_at. Returns the row id."""
    row_id = str(uuid.uuid4())
    ts = created_at.astimezone(datetime.UTC).replace(tzinfo=None)
    await session.execute(
        text(
            "INSERT INTO audit_events"
            " (id, tenant_id, actor_user_id, actor_email, action, target_type, target_id,"
            "  result, metadata, created_at)"
            " VALUES (:id, NULL, NULL, NULL, 'system.test', 'system', 'retention',"
            "  'success', '{}', :ts)"
        ),
        {"id": row_id, "ts": ts},
    )
    return row_id


def _settings_for(
    *,
    interval: int = 86400,
    usage_days: int = 365,
    alert_days: int = 90,
    audit_days: int = 730,
    audit_floor: int = 365,
    batch_size: int = 1000,
    request_logs_days: int = 0,
    tenant_ceiling_days: int = 365,
) -> Any:
    """Build a minimal settings-like object with retention knobs.

    tenant_ceiling_days defaults to 365 (matches config.py's real production default
    for retention_tenant_window_ceiling_days) — every sweep_once() call now executes
    the additive new-payload window-cutoff pass unconditionally (CRIT#2 fix), and that
    pass binds this field as a SQL parameter even for tables with zero matching rows,
    so every settings fake used with a real sweep must carry a real int here.
    """
    s = MagicMock()
    s.retention_check_interval_seconds = interval
    s.retention_usage_records_days = usage_days
    s.retention_alert_events_days = alert_days
    s.retention_audit_events_days = audit_days
    s.retention_audit_floor_days = audit_floor
    s.retention_batch_size = batch_size
    s.retention_request_logs_days = request_logs_days
    s.retention_tenant_window_ceiling_days = tenant_ceiling_days
    return s


def _make_sweeper(app: Any, settings: Any) -> RetentionSweeper:
    """Build a RetentionSweeper using the app's sessionmaker."""
    return RetentionSweeper(
        session_factory=app.state.sessionmaker,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Fixture: apply the NEW immutability TRIGGER to the test schema (create_all
# does NOT run migrations). Needed by tests that need the trigger active.
# ---------------------------------------------------------------------------


@pytest.fixture
async def audit_trigger_session(db_session: AsyncSession, app: Any) -> AsyncIterator[AsyncSession]:
    """Apply audit_events_immutable_guard trigger to the test DB.

    Base.metadata.create_all creates the table without the trigger (migration
    SQL is not executed in tests). This fixture applies the trigger manually so
    retention tests can exercise the GUC bypass path and immutability enforcement.
    """
    # Drop any old RULE-based guards (idempotent)
    await db_session.execute(text("DROP RULE IF EXISTS audit_events_no_update ON audit_events"))
    await db_session.execute(text("DROP RULE IF EXISTS audit_events_no_delete ON audit_events"))
    await db_session.execute(
        text("""
        CREATE OR REPLACE FUNCTION audit_events_immutable_guard_fn() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'audit_immutable_violation: audit_events rows are immutable';
          END IF;
          IF TG_OP = 'DELETE' THEN
            IF current_setting('app.audit_purge', true) = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_immutable_violation: audit_events rows cannot be deleted without audit_purge bypass';
          END IF;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """)
    )
    await db_session.execute(
        text("""
        CREATE OR REPLACE TRIGGER audit_events_immutable_guard
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_immutable_guard_fn()
        """)
    )
    await db_session.commit()
    try:
        yield db_session
    finally:
        # suite-stability M2: the schema is now built ONCE per xdist worker, so a
        # trigger installed here survives every later test on this worker. Under the
        # old per-test drop_all, DROP TABLE took it with them. Drop it explicitly.
        await db_session.execute(
            text("DROP TRIGGER IF EXISTS audit_events_immutable_guard ON audit_events")
        )
        await db_session.commit()


# ---------------------------------------------------------------------------
# Scenario 1: Only aged rows are deleted; younger rows survive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deletes_only_aged_rows(client: Any, db_session: AsyncSession, app: Any) -> None:
    """Rows older than the window are deleted; rows within the window survive."""
    tenant_id, key_id = await _signup_tenant(
        client, tenant_name="retention-t1", email="ret1@retention.io"
    )

    old_id = await _insert_usage_row(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400),
    )
    recent_id = await _insert_usage_row(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100),
    )
    await db_session.commit()

    sweeper = _make_sweeper(app, _settings_for(usage_days=365, alert_days=0, audit_days=0))
    result = await sweeper.sweep_once()

    assert result.get("usage_records", 0) == 1, (
        f"Expected 1 usage_records row deleted, got {result}"
    )

    # Verify: old row gone, recent row still there
    old_check = await db_session.execute(
        text("SELECT COUNT(*) FROM usage_records WHERE id = :id"), {"id": old_id}
    )
    assert old_check.scalar() == 0, "Aged row must be deleted"

    recent_check = await db_session.execute(
        text("SELECT COUNT(*) FROM usage_records WHERE id = :id"), {"id": recent_id}
    )
    assert recent_check.scalar() == 1, "Recent row must survive"


# ---------------------------------------------------------------------------
# Scenario 1b (CRIT#2 remediation): a default-posture tenant (retention_window_days
# IS NULL — never called PUT /admin/retention-policy) must still be purged, honoring
# the OPERATOR CEILING, exactly as tenants/application/retention_policy.py's
# effective_window_days() already REPORTS for that tenant. Before the fix, the
# window-cutoff SQL required `retention_window_days IS NOT NULL`, so a NULL tenant's
# aged rows were NEVER purged even though the API claimed a finite effective window.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_window_null_tenant_purged_via_operator_ceiling(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """A tenant that never overrides retention_window_days (column stays NULL) must
    still have its aged payload-table rows purged, using the operator ceiling as the
    fallback cutoff — matching what GET /admin/retention-policy's effective_window_days
    already reports for this exact tenant."""
    from gateway.memory.infrastructure.repository import MemoryRepository

    tenant_id, key_id = await _signup_tenant(
        client, tenant_name="retention-null-default", email="retnull@retention.io"
    )

    # Sanity: this tenant's retention_window_days is genuinely NULL (default posture,
    # never called PUT /admin/retention-policy).
    null_check = await db_session.execute(
        text("SELECT retention_window_days FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    )
    assert null_check.scalar() is None, "tenant must be at default posture (column NULL)"

    mem_repo = MemoryRepository(db_session)
    old_mem = await mem_repo.create(
        tenant_id=uuid.UUID(tenant_id), key_id=uuid.UUID(key_id), content="ancient", meta=None
    )
    recent_mem = await mem_repo.create(
        tenant_id=uuid.UUID(tenant_id), key_id=uuid.UUID(key_id), content="fresh", meta=None
    )
    await db_session.commit()

    async def _age(row_id: Any, days: int) -> None:
        ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).replace(
            tzinfo=None
        )
        await db_session.execute(
            text("UPDATE memories SET created_at = :ts WHERE id = :id"), {"ts": ts, "id": row_id}
        )
        await db_session.commit()

    await _age(old_mem.id, 400)  # older than the 365-day operator ceiling default
    await _age(recent_mem.id, 10)  # well within the ceiling

    # Operator-level windows for the ORIGINAL 3 tables are all OFF here so ONLY the
    # new-payload window-cutoff pass (honoring the 365-day operator ceiling) explains
    # any deletion below.
    sweeper = _make_sweeper(
        app, _settings_for(usage_days=0, alert_days=0, audit_days=0, tenant_ceiling_days=365)
    )
    result = await sweeper.sweep_once()

    old_check = await db_session.execute(
        text("SELECT COUNT(*) FROM memories WHERE id = :id"), {"id": old_mem.id}
    )
    assert old_check.scalar() == 0, (
        f"a NULL-window tenant's aged row must be purged via the operator ceiling"
        f" fallback (CRIT#2 fix) — sweep result was {result}"
    )
    recent_check2 = await db_session.execute(
        text("SELECT COUNT(*) FROM memories WHERE id = :id"), {"id": recent_mem.id}
    )
    assert recent_check2.scalar() == 1, "a recent row (within the ceiling) must survive"


# ---------------------------------------------------------------------------
# Scenario 2: Default-OFF when interval is zero (pure logic test)
# ---------------------------------------------------------------------------


def test_default_off_when_interval_zero() -> None:
    """should_start_retention_sweep returns False when interval=0."""
    settings_off = _settings_for(interval=0, usage_days=365, alert_days=90, audit_days=730)
    assert should_start_retention_sweep(settings_off) is False

    settings_on = _settings_for(interval=86400, usage_days=365, alert_days=0, audit_days=0)
    assert should_start_retention_sweep(settings_on) is True

    # ALL knobs=0 (including the newer request_logs/tenant-window-ceiling knobs, not
    # just the original 3) → OFF even when interval>0. NOTE (deliberate test update,
    # justified per remediation MED finding): this assertion previously only zeroed
    # usage/alert/audit_days and relied on _settings_for's OLD implicit default of
    # "no tenant_ceiling_days field at all" to stay OFF — that was actually the BUG
    # (should_start_retention_sweep ignored the newer knobs entirely, see
    # test_should_start_retention_sweep_includes_newer_knobs below). Now that
    # tenant_ceiling_days is itself a real gating knob, "all windows off" must
    # explicitly zero it too for this assertion to mean what its comment says.
    settings_no_windows = _settings_for(
        interval=86400,
        usage_days=0,
        alert_days=0,
        audit_days=0,
        request_logs_days=0,
        tenant_ceiling_days=0,
    )
    assert should_start_retention_sweep(settings_no_windows) is False


def test_should_start_retention_sweep_includes_newer_knobs() -> None:
    """MED fix: should_start_retention_sweep previously ignored
    retention_request_logs_days and retention_tenant_window_ceiling_days entirely — a
    deployment with only one of those newer knobs set (and the original 3 all at 0)
    would never start the sweeper, silently disabling the request_logs purge / the
    new-payload-table window-cutoff pass even though an operator explicitly configured
    a positive window for them."""
    only_request_logs = _settings_for(
        interval=86400,
        usage_days=0,
        alert_days=0,
        audit_days=0,
        request_logs_days=30,
        tenant_ceiling_days=0,
    )
    assert should_start_retention_sweep(only_request_logs) is True, (
        "a positive retention_request_logs_days alone must start the sweeper"
    )

    only_tenant_ceiling = _settings_for(
        interval=86400,
        usage_days=0,
        alert_days=0,
        audit_days=0,
        request_logs_days=0,
        tenant_ceiling_days=365,
    )
    assert should_start_retention_sweep(only_tenant_ceiling) is True, (
        "a positive retention_tenant_window_ceiling_days alone must start the sweeper"
    )


async def test_should_start_retention_sweep_with_zdr_starts_for_zdr_only_tenant(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """MED fix: should_start_retention_sweep is settings-only and therefore blind to a
    tenant with zdr_enabled=true — a deployment with every operator-level window knob
    at 0 would never start the sweeper at all, so that ZDR tenant's unconditional purge
    pass would never self-heal on any tick. should_start_retention_sweep_with_zdr must
    return True for exactly this case (settings-only answer stays False)."""
    from gateway.usage.application.retention_sweep import should_start_retention_sweep_with_zdr

    tenant_id, _key_id = await _signup_tenant(
        client, tenant_name="zdr-only-start-gate", email="zdronly@retention.io"
    )
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"), {"tid": tenant_id}
    )
    await db_session.commit()

    all_knobs_zero = _settings_for(
        interval=86400,
        usage_days=0,
        alert_days=0,
        audit_days=0,
        request_logs_days=0,
        tenant_ceiling_days=0,
    )
    # RED (pre-fix behavior): the settings-only gate is correctly False here — this is
    # not itself the bug, it documents WHY the DB-aware variant is needed.
    assert should_start_retention_sweep(all_knobs_zero) is False

    started = await should_start_retention_sweep_with_zdr(
        all_knobs_zero, session_factory=app.state.sessionmaker
    )
    assert started is True, (
        "a tenant with zdr_enabled=true must start the sweeper even when every"
        " operator-level window knob is 0"
    )

    # No zdr tenant + all knobs zero + a working session_factory -> honest False.
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = false WHERE id = :tid"), {"tid": tenant_id}
    )
    await db_session.commit()
    not_started = await should_start_retention_sweep_with_zdr(
        all_knobs_zero, session_factory=app.state.sessionmaker
    )
    assert not_started is False

    # session_factory=None honest-degrades to the settings-only answer (never raises).
    degraded = await should_start_retention_sweep_with_zdr(all_knobs_zero, session_factory=None)
    assert degraded is False


# ---------------------------------------------------------------------------
# Scenario 3: Window=0 for a table means that table is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_zero_skips_table(db_session: AsyncSession, app: Any) -> None:
    """alert_events_days=0 → alert rows are NOT deleted even if aged."""
    alert_id = await _insert_alert_row(
        db_session,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=200),
    )
    await db_session.commit()

    sweeper = _make_sweeper(app, _settings_for(usage_days=365, alert_days=0, audit_days=0))
    result = await sweeper.sweep_once()

    assert "alert_events" not in result or result.get("alert_events", 0) == 0, (
        f"alert_events must be skipped when alert_days=0, got {result}"
    )

    check = await db_session.execute(
        text("SELECT COUNT(*) FROM alert_events WHERE id = :id"), {"id": alert_id}
    )
    assert check.scalar() == 1, "Alert row must NOT be deleted when alert_days=0"


# ---------------------------------------------------------------------------
# Scenario 4: Audit floor is enforced (effective = max(knob, floor))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_floor_enforced(
    db_session: AsyncSession, app: Any, audit_trigger_session: AsyncSession
) -> None:
    """effective_audit_window = max(audit_events_days, audit_floor_days).

    With audit_events_days=30 and audit_floor_days=365 the effective window is
    365 days — a 100-day-old row survives, an 800-day-old row is purged.
    """
    sweeper = _make_sweeper(
        app, _settings_for(usage_days=0, alert_days=0, audit_days=30, audit_floor=365)
    )

    # Verify the effective_audit_window property
    assert sweeper.effective_audit_window == 365, (
        f"Expected effective_audit_window=365, got {sweeper.effective_audit_window}"
    )

    # 100-day-old row — should survive (floor=365 keeps it)
    recent_id = await _insert_audit_row(
        audit_trigger_session,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=100),
    )
    # 800-day-old row — should be purged (800 > max(30, 365) = 365)
    old_id = await _insert_audit_row(
        audit_trigger_session,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=800),
    )
    await audit_trigger_session.commit()

    result = await sweeper.sweep_once()
    assert result.get("audit_events", 0) == 1, (
        f"Expected 1 audit row purged (the 800d row), got {result}"
    )

    # 100d row must survive
    check_recent = await audit_trigger_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE id = :id"), {"id": recent_id}
    )
    assert check_recent.scalar() == 1, "100-day audit row must survive the floor"

    # 800d row must be gone
    check_old = await audit_trigger_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE id = :id"), {"id": old_id}
    )
    assert check_old.scalar() == 0, "800-day audit row must be purged"


# ---------------------------------------------------------------------------
# Scenario 5: Audit purge uses GUC bypass; plain DELETE still raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_purge_via_bypass_only(
    db_session: AsyncSession, app: Any, audit_trigger_session: AsyncSession
) -> None:
    """The sweeper uses SET LOCAL app.audit_purge='on' to purge audit rows.

    Without bypass, plain DELETE raises; the sweeper's transaction-scoped GUC
    allows the DELETE to proceed.
    """
    # Insert an 800-day-old audit row (well past the 365-day floor)
    old_id = await _insert_audit_row(
        audit_trigger_session,
        created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=800),
    )
    await audit_trigger_session.commit()

    # Verify: plain DELETE (no GUC) raises
    with pytest.raises(Exception, match="audit_immutable_violation"):
        await audit_trigger_session.execute(
            text("DELETE FROM audit_events WHERE id = :id"), {"id": old_id}
        )
        await audit_trigger_session.commit()
    await audit_trigger_session.rollback()

    # Verify: plain UPDATE still raises
    with pytest.raises(Exception, match="audit_immutable_violation"):
        await audit_trigger_session.execute(
            text("UPDATE audit_events SET result = 'tampered' WHERE id = :id"),
            {"id": old_id},
        )
        await audit_trigger_session.commit()
    await audit_trigger_session.rollback()

    # Sweeper must succeed (uses SET LOCAL bypass in its own transaction)
    sweeper = _make_sweeper(
        app, _settings_for(usage_days=0, alert_days=0, audit_days=365, audit_floor=365)
    )
    result = await sweeper.sweep_once()
    assert result.get("audit_events", 0) == 1, (
        f"Expected sweeper to delete 1 audit row via bypass, got {result}"
    )

    # Row must be gone
    check = await audit_trigger_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE id = :id"), {"id": old_id}
    )
    assert check.scalar() == 0, "Audit row must be purged by the sweeper via GUC bypass"


# ---------------------------------------------------------------------------
# Scenario 6: Batched delete — 2500 rows, batch_size=1000
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batched_delete(client: Any, db_session: AsyncSession, app: Any) -> None:
    """2500 aged rows are deleted across multiple ≤1000-row batches."""
    tenant_id, key_id = await _signup_tenant(
        client, tenant_name="retention-batch", email="batch@retention.io"
    )

    # Insert 2500 rows aged 400 days
    ids = []
    for _ in range(2500):
        rid = await _insert_usage_row(
            db_session,
            tenant_id=tenant_id,
            key_id=key_id,
            created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400),
        )
        ids.append(rid)
    await db_session.commit()

    sweeper = _make_sweeper(
        app, _settings_for(usage_days=365, alert_days=0, audit_days=0, batch_size=1000)
    )
    result = await sweeper.sweep_once()
    deleted = result.get("usage_records", 0)
    assert deleted == 2500, f"Expected all 2500 rows deleted, got {deleted}"

    # Verify all rows are gone
    count_check = await db_session.execute(
        text("SELECT COUNT(*) FROM usage_records WHERE id = ANY(:ids)"),
        {"ids": ids},
    )
    remaining = count_check.scalar()
    assert remaining == 0, f"Expected 0 remaining rows, got {remaining}"


# ---------------------------------------------------------------------------
# Scenario 7: Purge is audited via data.purge event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_is_audited(client: Any, db_session: AsyncSession, app: Any) -> None:
    """A data.purge audit event is emitted after each table purge."""
    tenant_id, key_id = await _signup_tenant(
        client, tenant_name="retention-purge-audit", email="purgeaudit@retention.io"
    )

    # Insert 5 aged usage rows
    for _ in range(5):
        await _insert_usage_row(
            db_session,
            tenant_id=tenant_id,
            key_id=key_id,
            created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400),
        )
    await db_session.commit()

    sweeper = _make_sweeper(app, _settings_for(usage_days=365, alert_days=0, audit_days=0))
    result = await sweeper.sweep_once()
    assert result.get("usage_records", 0) == 5, f"Expected 5 usage rows purged, got {result}"

    # Allow background audit tasks to complete — POLL, don't sleep a fixed 0.1 s.
    # A fixed sleep passes on an idle machine and fails under `-n 6` on a loaded
    # host; it did exactly that on 2026-07-29 ("got 0").
    async def _audit_count() -> int:
        audit_check = await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_events"
                " WHERE action = 'data.purge' AND target_type = 'retention'"
                " AND target_id = 'usage_records'"
            )
        )
        return audit_check.scalar() or 0

    count = await poll_until(_audit_count, lambda n: n >= 1)
    assert count >= 1, f"Expected at least one data.purge audit event, got {count}"


# ---------------------------------------------------------------------------
# Scenario 8: Sweep is fail-open (DB error swallowed, task survives)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_fail_open() -> None:
    """DB error during DELETE is swallowed; run_forever does not crash."""
    # Build a failing session context manager
    failing_session = AsyncMock(spec=AsyncSession)
    failing_session.__aenter__ = AsyncMock(return_value=failing_session)
    failing_session.__aexit__ = AsyncMock(return_value=False)
    failing_session.execute = AsyncMock(side_effect=RuntimeError("simulated DB failure"))
    failing_session.commit = AsyncMock()
    failing_session.rollback = AsyncMock()

    # The session_factory context manager returns a session that itself is a context manager
    ctx_mgr = MagicMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=failing_session)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    # begin() on session also needs to be a context manager
    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    failing_session.begin = MagicMock(return_value=begin_ctx)

    failing_factory = MagicMock()
    failing_factory.return_value = ctx_mgr

    sweeper = RetentionSweeper(
        session_factory=failing_factory,  # type: ignore[arg-type]
        settings=_settings_for(usage_days=365, alert_days=0, audit_days=0),
    )

    # sweep_once must not raise — returns empty dict or zeros
    result = await sweeper.sweep_once()
    assert isinstance(result, dict), "sweep_once must return a dict even on failure"

    # run_forever must survive the error and only stop on CancelledError
    tick_count = 0

    async def _mock_sleep(seconds: float) -> None:
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 1:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await sweeper.run_forever(interval_seconds=0.001, _sleep=_mock_sleep)

    # Reached here = the loop survived the DB failure and exited cleanly on CancelledError
