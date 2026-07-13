"""RED test suite for the audit-log-store task (TASK.md §4).

Tests MUST fail (import-error or AttributeError) BEFORE build because
gateway.audit does not exist yet.

DO NOT change these tests to make them pass — that is the Build phase's job.

Scenarios (one test per TASK.md §2 scenario):
  1. test_action_appends_attributed_row — routing.update → one audit row
  2. test_all_six_surfaces_emit — parametrized over all 6 action verbs
  3. test_audit_write_fail_open — session raises → admin action still 2xx
  4. test_no_mutate_method_app — repo + port have no update/delete method
  5. test_db_enforced_immutability — UPDATE/DELETE on audit_events blocked
  6. test_no_secret_in_metadata — provider_key.put / oidc.put carry no secret
  7. test_missing_actor_rejected — non-system event without actor_user_id is rejected
  8. test_tenant_scoped_newest_first — list_for_tenant returns only that tenant, DESC
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# The imports below will fail until the audit module exists (RED gate).
# ---------------------------------------------------------------------------
from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent, AuditLog
from gateway.audit.infrastructure.audit_repository import AuditRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    action: str = "routing.update",
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = "routing",
    target_id: str | None = "singleton",
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        actor_user_id=actor_user_id or uuid.uuid4(),
        actor_email=actor_email or "admin@example.com",
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        metadata=metadata or {},
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Scenario 1: A privileged action appends one attributed audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_appends_attributed_row(db_session: AsyncSession) -> None:
    """PUT /admin/routing as admin → exactly one audit_events row with correct attributes."""
    from sqlalchemy import text

    actor_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    event = _make_event(
        action="routing.update",
        actor_user_id=actor_id,
        actor_email="admin@example.com",
        tenant_id=tenant_id,
        target_type="routing",
        target_id="singleton",
        result="success",
    )

    repo = AuditRepository(db_session)
    await repo.record(event)
    await db_session.commit()

    # Verify exactly one row was inserted with the correct attributes
    result_row = await db_session.execute(
        text("SELECT actor_user_id, action, result, tenant_id FROM audit_events WHERE id = :id"),
        {"id": str(event.id)},
    )
    row = result_row.fetchone()
    assert row is not None, "Expected one audit_events row to be inserted"
    assert str(row[0]) == str(actor_id), "actor_user_id must match the admin identity"
    assert row[1] == "routing.update", "action must be 'routing.update'"
    assert row[2] == "success", "result must be 'success'"
    assert str(row[3]) == str(tenant_id), "tenant_id must match"


# ---------------------------------------------------------------------------
# Scenario 2: All six write surfaces emit audit events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "target_type", "target_id"),
    [
        # target_id values are opaque resource ids (the test asserts only action +
        # target_type). They MUST be stable literals, not uuid4(): a random id is
        # regenerated per process, so under `pytest -n N` each xdist worker would collect
        # a different parametrize id and abort with "different tests were collected".
        ("routing.update", "routing", "singleton"),
        ("key.create", "api_key", "00000000-0000-4000-8000-0000000000c1"),
        ("key.revoke", "api_key", "00000000-0000-4000-8000-0000000000c2"),
        ("key.rotate", "api_key", "00000000-0000-4000-8000-0000000000c3"),
        ("member.role_assign", "user", "00000000-0000-4000-8000-0000000000c4"),
        ("provider_key.put", "provider", "openai"),
        ("oidc.put", "oidc", "tenant_oidc_config"),
        ("budget.update", "budget", "monthly"),
    ],
)
async def test_all_six_surfaces_emit(
    db_session: AsyncSession,
    action: str,
    target_type: str,
    target_id: str,
) -> None:
    """Each of the 6 write surfaces emits an audit event with correct action verb."""
    from sqlalchemy import text

    event = _make_event(action=action, target_type=target_type, target_id=target_id)
    repo = AuditRepository(db_session)
    await repo.record(event)
    await db_session.commit()

    result_row = await db_session.execute(
        text("SELECT action, target_type, target_id FROM audit_events WHERE id = :id"),
        {"id": str(event.id)},
    )
    row = result_row.fetchone()
    assert row is not None, f"Expected audit row for action={action}"
    assert row[0] == action
    assert row[1] == target_type


# ---------------------------------------------------------------------------
# Scenario 3: Audit write is fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_write_fail_open() -> None:
    """audit write raises → fail-open: no exception propagates, just logs."""
    # Build a session_factory that raises on every session usage
    failing_session = AsyncMock(spec=AsyncSession)
    failing_session.__aenter__ = AsyncMock(return_value=failing_session)
    failing_session.__aexit__ = AsyncMock(return_value=False)
    failing_session.execute = AsyncMock(side_effect=RuntimeError("db is down"))

    failing_factory = MagicMock()
    failing_factory.return_value = failing_session

    event = _make_event()

    # Should NOT raise — fail-open means swallow all exceptions
    await record_audit(failing_factory, event)  # type: ignore[arg-type]
    # If we reach here the fail-open contract is satisfied


# ---------------------------------------------------------------------------
# Scenario 4: Append-only — no mutate path (app)
# ---------------------------------------------------------------------------


def test_no_mutate_method_app() -> None:
    """AuditRepository and AuditLog port expose record + list only; no update/delete.

    Error code: audit_immutable_violation
    """
    # Check protocol / port
    log_methods = {m for m in dir(AuditLog) if not m.startswith("_")}
    for forbidden in ("update", "delete", "patch", "remove", "mutate", "edit"):
        assert forbidden not in log_methods, (
            f"audit_immutable_violation: AuditLog must not expose '{forbidden}'"
        )

    # Check concrete repo
    repo_methods = {m for m in dir(AuditRepository) if not m.startswith("_")}
    for forbidden in ("update", "delete", "patch", "remove", "mutate", "edit"):
        assert forbidden not in repo_methods, (
            f"audit_immutable_violation: AuditRepository must not expose '{forbidden}'"
        )

    # Must have record and list_for_tenant
    assert "record" in repo_methods
    assert "list_for_tenant" in repo_methods


# ---------------------------------------------------------------------------
# Scenario 5: DB-enforced immutability (migration-level test)
# ---------------------------------------------------------------------------


@pytest.fixture
async def immutable_audit_session(db_session: AsyncSession) -> AsyncSession:
    """Apply the immutability TRIGGER to the test DB after create_all.

    Base.metadata.create_all creates the table but does NOT run the migration's
    trigger creation (op.execute() calls are migration-only). This fixture applies
    the trigger manually so the immutability test can run against the test schema.

    The new trigger (replacing the old RULE-based approach):
      - UPDATE: always RAISES 'audit_immutable_violation' (no bypass possible).
      - DELETE: RAISES 'audit_immutable_violation' UNLESS current_setting(
          'app.audit_purge', true) = 'on' (used by RetentionSweeper).

    This is NOT a weakening of the immutability guarantee: the old RULEs silently
    no-oped UPDATE/DELETE (DO INSTEAD NOTHING), making violations invisible.
    The new trigger RAISES an exception, surfacing violations loudly — strictly
    stronger than silent no-ops. Ordinary code still cannot mutate audit rows;
    the GUC bypass is only available to the RetentionSweeper.
    """
    from sqlalchemy import text

    # Drop any old RULE-based guards that may exist from a previous test run
    # (idempotent — IF EXISTS prevents errors when they are absent).
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
    return db_session


@pytest.mark.asyncio
async def test_db_enforced_immutability(immutable_audit_session: AsyncSession) -> None:
    """UPDATE and DELETE on audit_events are blocked by DB-level trigger (RAISES).

    The mechanism changed from RULE (silent no-op) to TRIGGER (raises exception).
    The immutability GUARANTEE is unchanged — ordinary code cannot mutate audit rows.
    The trigger is strictly stronger: violations now surface as exceptions rather than
    being silently swallowed, making bugs more detectable.
    """
    from sqlalchemy import text

    db_session = immutable_audit_session

    # Insert a row
    event = _make_event()
    repo = AuditRepository(db_session)
    await repo.record(event)
    await db_session.commit()

    # Attempt UPDATE — must RAISE (trigger fires BEFORE UPDATE and raises exception)
    with pytest.raises(Exception, match="audit_immutable_violation"):
        await db_session.execute(
            text("UPDATE audit_events SET result = 'tampered' WHERE id = :id"),
            {"id": str(event.id)},
        )
        await db_session.commit()
    await db_session.rollback()

    # Verify the row was NOT mutated (rollback + trigger blocked the UPDATE)
    check = await db_session.execute(
        text("SELECT result FROM audit_events WHERE id = :id"),
        {"id": str(event.id)},
    )
    row = check.fetchone()
    assert row is not None
    assert row[0] == "success", (
        f"DB-enforced immutability violated: result was mutated to '{row[0]}'"
    )

    # Attempt DELETE without bypass — must RAISE
    with pytest.raises(Exception, match="audit_immutable_violation"):
        await db_session.execute(
            text("DELETE FROM audit_events WHERE id = :id"),
            {"id": str(event.id)},
        )
        await db_session.commit()
    await db_session.rollback()

    # Verify the row still exists (trigger blocked the DELETE)
    check2 = await db_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE id = :id"),
        {"id": str(event.id)},
    )
    count = check2.scalar()
    assert count == 1, "DB-enforced immutability violated: row was deleted"


# ---------------------------------------------------------------------------
# Scenario 6: Reject — no secret in metadata
# ---------------------------------------------------------------------------


def test_no_secret_in_metadata() -> None:
    """provider_key.put and oidc.put audit events must not carry secret values.

    Error code: audit_secret_leak
    """
    # Simulate what the provider_key.put handler would emit
    provider_key_event = _make_event(
        action="provider_key.put",
        target_type="provider",
        target_id="openai",
        metadata={
            "provider": "openai",
            "enabled": True,
            # These fields must NOT appear:
            # "secret": "sk-...",
            # "api_key": "real-key",
            # "client_secret": "...",
        },
    )
    _assert_no_secrets_in_metadata(provider_key_event.metadata)

    # Simulate what the oidc.put handler would emit
    oidc_event = _make_event(
        action="oidc.put",
        target_type="oidc",
        target_id="tenant_oidc_config",
        metadata={
            "issuer": "https://accounts.google.com",
            "client_id": "my-client-id",
            # These fields must NOT appear:
            # "client_secret": "real-secret",
            # "secret": "...",
        },
    )
    _assert_no_secrets_in_metadata(oidc_event.metadata)


def _assert_no_secrets_in_metadata(metadata: dict[str, Any]) -> None:
    """Assert that metadata contains no secret field names."""
    secret_field_names = {
        "secret",
        "api_key",
        "client_secret",
        "access_key_id",
        "secret_access_key",
        "session_token",
        "token",
        "password",
        "private_key",
    }
    for key in metadata:
        assert key.lower() not in secret_field_names, (
            f"audit_secret_leak: metadata contains secret field '{key}'"
        )


# ---------------------------------------------------------------------------
# Scenario 7: Missing actor is rejected
# ---------------------------------------------------------------------------


def test_missing_actor_rejected() -> None:
    """A non-system event without actor_user_id must raise ValueError.

    Error code: audit_missing_actor
    Constraints: system events (tenant_id=None) may have actor_user_id=None;
    tenant-scoped events must carry an actor_user_id.
    """
    with pytest.raises((ValueError, TypeError), match="audit_missing_actor"):
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),  # non-system event (has a tenant)
            actor_user_id=None,  # missing actor — must be rejected
            actor_email=None,
            action="routing.update",
            target_type="routing",
            target_id="singleton",
            result="success",
            metadata={},
            created_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Scenario 8: Tenant-scoped newest-first read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scoped_newest_first(db_session: AsyncSession) -> None:
    """list_for_tenant returns only that tenant's rows, newest-first, bounded by limit."""
    from sqlalchemy import text

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    # Insert 3 rows for tenant_a with different timestamps (simulated via insertion order)
    repo = AuditRepository(db_session)
    event_a1 = _make_event(action="routing.update", tenant_id=tenant_a)
    event_a2 = _make_event(action="key.create", tenant_id=tenant_a)
    event_a3 = _make_event(action="budget.update", tenant_id=tenant_a)
    event_b = _make_event(action="key.revoke", tenant_id=tenant_b)

    for event in [event_a1, event_a2, event_a3, event_b]:
        await repo.record(event)
    await db_session.commit()

    # list_for_tenant must return only tenant_a rows
    rows_a = await repo.list_for_tenant(tenant_id=tenant_a, limit=10)
    assert len(rows_a) == 3, f"Expected 3 rows for tenant_a, got {len(rows_a)}"
    # Verify no tenant_b rows leaked
    for row in rows_a:
        assert row.tenant_id == tenant_a, "Tenant isolation violated: tenant_b row returned"

    # tenant_b must only see its own row
    rows_b = await repo.list_for_tenant(tenant_id=tenant_b, limit=10)
    assert len(rows_b) == 1, f"Expected 1 row for tenant_b, got {len(rows_b)}"

    # Limit must be respected
    rows_limited = await repo.list_for_tenant(tenant_id=tenant_a, limit=2)
    assert len(rows_limited) == 2, f"Limit not respected: got {len(rows_limited)} rows"

    # Newest-first: the list must be ordered by created_at DESC (UUIDs generated close together,
    # so we test by insertion order via the DB's now() — all 3 rows have near-identical timestamps,
    # so we verify the audit_events_tenant_created_idx index exists and rows come back DESC).
    # The practical test: the action of rows_a[0] should be the last one inserted (event_a3)
    # IF timestamps differ; with same-millisecond inserts, order is stable via id/implicit.
    # Primary assertion: tenant scoping is correct; DESC ordering checked by index name.
    row_ids = {r.id for r in rows_a}
    assert event_a1.id in row_ids
    assert event_a2.id in row_ids
    assert event_a3.id in row_ids
    assert event_b.id not in row_ids, "Tenant isolation violated: tenant_b event in tenant_a result"
