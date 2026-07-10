"""Red suite: audit_missing_actor relaxation for key-actor events (B2 TASK.md §3, Option B).

RED until AuditEvent gains actor_key_id and __post_init__ accepts EITHER actor_user_id OR
actor_key_id for a tenant-scoped event.

CHANGE-REQUEST against the FROZEN audit-log-store contract (Tin's frozen choice,
2026-07-10, AskUserQuestion): a tenant-scoped audit event is now valid with EITHER
actor_user_id OR actor_key_id — never both None. This file re-crosses the relaxed
invariant; tests/audit/test_audit_store.py's own frozen tests (including
test_missing_actor_rejected, which supplies NEITHER field) stay green UNMODIFIED — the
relaxation is additive, user-actor events are completely unaffected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository


def test_tenant_scoped_event_valid_with_only_actor_key_id() -> None:
    """A key-authenticated relay session has no user identity — actor_key_id alone must
    satisfy the tenant-scoped invariant (previously would have raised audit_missing_actor)."""
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        actor_key_id=uuid.uuid4(),
        actor_email=None,
        action="realtime_relay.session_opened",
        target_type="realtime_relay",
        target_id="key-123",
        result="success",
        metadata={"provider": "openai", "model": "gpt-realtime"},
        created_at=datetime.now(UTC),
    )
    assert event.actor_key_id is not None
    assert event.actor_user_id is None


def test_tenant_scoped_event_still_rejected_when_both_actor_fields_absent() -> None:
    """Regression pin (mirrors tests/audit/test_audit_store.py::test_missing_actor_rejected,
    unmodified): neither actor_user_id nor actor_key_id -> still raises audit_missing_actor."""
    with pytest.raises((ValueError, TypeError), match="audit_missing_actor"):
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            actor_user_id=None,
            actor_key_id=None,
            actor_email=None,
            action="routing.update",
            target_type="routing",
            target_id="singleton",
            result="success",
            metadata={},
            created_at=datetime.now(UTC),
        )


def test_actor_key_id_defaults_to_none_backward_compatible() -> None:
    """Every EXISTING call site that constructs AuditEvent without actor_key_id (user-actor
    events across the whole codebase) must keep working unchanged."""
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        actor_email="admin@example.com",
        action="routing.update",
        target_type="routing",
        target_id="singleton",
        result="success",
        metadata={},
        created_at=datetime.now(UTC),
    )
    assert event.actor_key_id is None


# ---------------------------------------------------------------------------
# Persistence: actor_key_id round-trips through the ORM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_key_id_persists_and_round_trips(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_user_id=None,
        actor_key_id=key_id,
        actor_email=None,
        action="realtime_relay.session_opened",
        target_type="realtime_relay",
        target_id=str(key_id),
        result="success",
        metadata={"provider": "openai", "model": "gpt-realtime"},
        created_at=datetime.now(UTC),
    )
    repo = AuditRepository(db_session)
    await repo.record(event)
    await db_session.commit()

    row = (
        await db_session.execute(
            text("SELECT actor_key_id, actor_user_id FROM audit_events WHERE id = :id"),
            {"id": str(event.id)},
        )
    ).fetchone()
    assert row is not None
    assert str(row[0]) == str(key_id)
    assert row[1] is None

    # list_for_tenant round-trips actor_key_id back onto the domain entity.
    rows = await repo.list_for_tenant(tenant_id=tenant_id, limit=10)
    assert len(rows) == 1
    assert rows[0].actor_key_id == key_id
    assert rows[0].actor_user_id is None


@pytest.mark.asyncio
async def test_user_actor_events_unaffected_by_actor_key_id_column(db_session: AsyncSession) -> None:
    """A pre-existing user-actor event (actor_key_id never set) persists with actor_key_id
    NULL — the relaxation is purely additive."""
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        actor_email="admin@example.com",
        action="routing.update",
        target_type="routing",
        target_id="singleton",
        result="success",
        metadata={},
        created_at=datetime.now(UTC),
    )
    repo = AuditRepository(db_session)
    await repo.record(event)
    await db_session.commit()

    row = (
        await db_session.execute(
            text("SELECT actor_key_id, actor_user_id FROM audit_events WHERE id = :id"),
            {"id": str(event.id)},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert str(row[1]) == str(actor_id)
