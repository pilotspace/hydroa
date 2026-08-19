"""Fire-and-forget audit event writer — fail-open.

Contract (audit-log-store TASK.md §3 — FROZEN @ v1):
  record_audit(session_factory, event) — insert one AuditEvent in a SEPARATE session
  from the admin action's own transaction. Swallows ALL exceptions (failures are logged,
  never raised into the request path). Must be scheduled as a fire-and-forget task
  (asyncio.ensure_future / asyncio.create_task), never awaited on the hot path.

FAIL-OPEN:
  An audit write failure MUST NOT change the HTTP outcome of the admin action.
  The audit write uses its own session (separate from the action's transaction),
  so a rollback of the action does not affect the audit trail, and vice versa.
  If the audit write raises for any reason, the exception is caught, logged, and swallowed.

Pattern follows alert_writer.py (same fire-and-forget + swallow contract).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository

_log = logging.getLogger(__name__)

# M9 / S5 (audit-coverage-structural-guard TASK.md) — making a SWALLOWED audit write
# detectable without ever making it blocking.
#
# record_audit is fail-open by contract, so a broken audit path is invisible: evidence can
# go missing for a whole window while every request keeps returning 200. That is the CC7.2
# gap. The remedy is a counter beside the existing log line — a signal, never a raise.
#
# WHERE THE COUNTER LIVES, AND WHY ON THE SESSIONMAKER. record_audit is handed only a
# session factory; it has no app, and the Prometheus registry is deliberately PER-APP
# (observability/metrics.py — a shared global would cross-contaminate a pytest session that
# builds many apps). So this app's MetricsRegistry is parked on THIS app's own sessionmaker,
# the one object every emitter already passes in. A swallowed failure can therefore only
# ever reach the registry of the app that produced it. A module-level global would be
# last-app-wins: a later create_app() would silently redirect an earlier app's audit
# failures onto the wrong registry.
AUDIT_METRICS_ATTR = "gateway_audit_metrics"
AUDIT_WRITE_FAILED_ATTR = "audit_write_failed_total"


def bind_audit_metrics(session_factory: Any, metrics: Any) -> None:
    """Park one app's MetricsRegistry on that app's own sessionmaker (called from create_app)."""
    setattr(session_factory, AUDIT_METRICS_ATTR, metrics)


def _count_audit_write_failure(session_factory: Any) -> None:
    """Increment the swallowed-audit-write counter — BEST EFFORT, never raises (A33).

    The whole lookup is resolved HERE rather than cached, so the live metric object is
    fetched at increment time. A counter failure must not become the audit failure it is
    reporting, which would invert fail-open (R:AUDIT_BLOCKS_REQUEST) — so the observability
    outage is swallowed exactly the way otel.py's _inc_counter swallows its own.
    """
    try:
        metrics = getattr(session_factory, AUDIT_METRICS_ATTR, None)
        counter = getattr(metrics, AUDIT_WRITE_FAILED_ATTR, None) if metrics is not None else None
        if counter is not None:
            counter.inc()
    except Exception:  # noqa: S110 — intentional: observability must never break fail-open
        pass


async def record_audit(
    session_factory: async_sessionmaker[AsyncSession],
    event: AuditEvent,
) -> None:
    """Insert one audit event row into audit_events — fail-open.

    Uses its own session (SEPARATE from the caller's transaction) so:
    - An admin action rollback cannot lose a committed audit event.
    - An audit write failure cannot roll back the admin action.

    Swallows ALL exceptions — failures are logged but NEVER raised.
    Schedule via asyncio.ensure_future() or asyncio.create_task(), never await directly.
    """
    try:
        async with session_factory() as session:
            repo = AuditRepository(session)
            await repo.record(event)
            await session.commit()
    except Exception as exc:
        _log.warning(
            "audit_writer: failed to persist audit event (swallowed — fail-open)",
            exc_info=exc,
            extra={
                "audit_action": event.action,
                "audit_event_id": str(event.id),
                "audit_tenant_id": str(event.tenant_id) if event.tenant_id else None,
                "audit_actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            },
        )
        # M9: LAST, and after the log — an observability outage must never displace the
        # warning that is today's only signal. One increment per SWALLOWED exception (A31).
        _count_audit_write_failure(session_factory)


def build_audit_event(
    *,
    action: str,
    target_type: str,
    target_id: str,
    tenant_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    actor_key_id: uuid.UUID | None = None,
    actor_scim_token_id: uuid.UUID | None = None,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Build one AuditEvent WITHOUT ever raising — returns None when it cannot be built.

    Why this exists (audit-coverage-structural-guard TASK.md M4 / R:AUDIT_BLOCKS_REQUEST):

    `record_audit` is fail-open, but its try block starts AFTER the event object already
    exists. Constructing the event at the call site therefore leaves ONE unguarded step: an
    `AuditEvent.__post_init__` failure — the `audit_missing_actor` invariant firing because a
    route supplied no actor — escapes into the handler and 500s the mutation it was only
    supposed to observe. That is the fail-open contract inverted by the single line it does
    not cover.

    So the retrofit call sites read:

        audit_event = build_audit_event(...)
        if audit_event is not None:
            await record_audit(request.app.state.sessionmaker, audit_event)

    The `record_audit` call stays AT the call site deliberately — M1 makes a real
    `record_audit(...)` call in the handler's own package the evidence that the package
    audits, and hiding it behind a shared façade would make every retrofitted package look
    silent to the route-walking guard.

    The await is INLINE, deliberately (A5): a disclosed override of this module's own
    fire-and-forget calling convention, taken uniformly across the retrofit so the row is
    durable before the mutating request returns. It is safe precisely because both failure
    paths are swallowed and `record_audit` still uses its OWN session, so neither the caller's
    transaction nor its HTTP outcome can be affected.
    """
    try:
        return AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            actor_key_id=actor_key_id,
            actor_scim_token_id=actor_scim_token_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
    except Exception as exc:
        # The event could not even be BUILT (invariant violation, bad value). Nothing to
        # persist and nothing to hand to record_audit — log and return None, never raise.
        _log.warning(
            "audit_writer: failed to BUILD audit event (swallowed — fail-open)",
            exc_info=exc,
            extra={"audit_action": action, "audit_target_type": target_type},
        )
        return None
