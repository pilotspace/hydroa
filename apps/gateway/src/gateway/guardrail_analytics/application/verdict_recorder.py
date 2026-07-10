"""Fire-and-forget guardrail-verdict-event writer — fail-open.

Contract (guardrail-analytics TASK.md §3 — FROZEN @ v1, M1/M9):
  record_guardrail_verdicts(session_factory, ...) — insert one row per GuardrailEvent in a
  SEPARATE session from the proxied request's own transaction. Swallows ALL exceptions
  (failures are logged, never raised into the request path). Scheduled as a fire-and-forget
  task (asyncio.create_task) by the caller, never awaited inline on the hot path.

FAIL-OPEN (mirrors gateway.audit.application.audit_writer.record_audit byte-for-byte):
  A verdict-write failure MUST NOT change the HTTP outcome of the proxied completion.
  The write uses its own session (separate from the request's transaction), so a rollback
  of the proxied request can never lose a committed verdict row, and a verdict-write
  failure can never roll back the proxied request. If the write raises for any reason
  (DB error, timeout, pool exhaustion), the exception is caught, logged, and swallowed.

Bounded (M9): wrapped in asyncio.timeout(2.0) — an unbounded/blocking write is its own
failure mode regardless of "local vs remote" IO. This is NOT a new outbound-IO seam in the
CLAUDE.md retry/circuit-breaker sense (a local Postgres insert on the gateway's own
database, same rationale per-key-guardrail-policies used for its own zero-new-IO
resolution) — never retried: a fire-and-forget best-effort write retried under a
struggling store would amplify load during exactly the outage it should shed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.guardrail_analytics.infrastructure.orm import GuardrailVerdictEventRow

if TYPE_CHECKING:
    from gateway.proxy.domain.entities import GuardrailEvent

_log = logging.getLogger(__name__)

_WRITE_TIMEOUT_SECONDS = 2.0


async def record_guardrail_verdicts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    team_id: uuid.UUID | None,
    policy_source: str,
    events: list[GuardrailEvent],
) -> None:
    """Insert one row per event into guardrail_verdict_events — fail-open.

    Uses its own session (SEPARATE from the caller's transaction) so:
    - A proxied-request rollback cannot lose a committed verdict row.
    - A verdict-write failure cannot roll back the proxied request.

    No-ops when events is empty. Swallows ALL exceptions — failures are logged but
    NEVER raised. Schedule via asyncio.create_task(), never await directly.
    """
    if not events:
        return
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS):
            async with session_factory() as session:
                session.add_all(
                    [
                        GuardrailVerdictEventRow(
                            tenant_id=tenant_id,
                            key_id=key_id,
                            team_id=team_id,
                            guardrail=event.guardrail,
                            action=event.action,
                            policy_source=policy_source,
                        )
                        for event in events
                    ]
                )
                await session.commit()
    except Exception as exc:
        _log.warning(
            "verdict_recorder: failed to persist guardrail verdict(s) (swallowed — fail-open)",
            exc_info=exc,
            extra={
                "guardrail_verdict_tenant_id": str(tenant_id),
                "guardrail_verdict_key_id": str(key_id),
                "guardrail_verdict_event_count": len(events),
            },
        )
