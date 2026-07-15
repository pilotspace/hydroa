"""Shared fire-and-forget system event emitter.

All system events (circuit_breaker_open, drain_timeout, upstream_health_*) use this helper.
Pattern:
  - INSERT with ON CONFLICT (dedupe_key) DO NOTHING — idempotent
  - Swallows ALL exceptions — failures logged, never raised into the caller (this
    fire-and-forget CONTRACT is preserved; callers use asyncio.ensure_future() and never
    await/inspect the result)
  - Fire-and-forget via asyncio.ensure_future() on the hot path

Observability (audit-remediation MED finding): a swallowed DB error used to be logged at
WARNING with no counter — a genuinely broken emitter (bad DSN, pool exhaustion, permission
change) silently dropped every system event forever with nothing to alert on. Failures are
now logged at ERROR (naming event_type + dedupe_key) AND increment a process-local counter
(`emit_system_event_failures_total()`) so the emitter's health is observable. This module
has no route into `gateway.observability.metrics`'s per-app `CollectorRegistry` (that file
is outside this package's scope) — wiring this counter into the real Prometheus registry
is a follow-up outside this package's boundary; the counter is deliberately exposed as a
plain module-level accessor so that follow-up (or a test) can read it without needing a
live app/registry.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger(__name__)

#: Process-local counter of swallowed emit failures — see module docstring.
_emit_failures_total = 0


def emit_system_event_failures_total() -> int:
    """Return the number of `emit_system_event` calls that swallowed a DB error so far
    in this process. Monotonically increasing; resets only on process restart."""
    return _emit_failures_total


async def emit_system_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> None:
    """Insert one alert_events row for a system event (tenant_id NULL, key_id NULL).

    Idempotent via UNIQUE dedupe_key + ON CONFLICT DO NOTHING.
    Swallows all exceptions — never raises into caller (fire-and-forget CONTRACT) — but
    every swallowed failure is logged at ERROR and counted (see module docstring).
    """
    global _emit_failures_total
    try:
        row_id = uuid.uuid4()
        async with session_factory() as session:
            from sqlalchemy import text  # local import to avoid circular risk

            await session.execute(
                text(
                    "INSERT INTO alert_events"
                    " (id, tenant_id, key_id, event_type, payload, dedupe_key, created_at)"
                    " VALUES (:id, NULL, NULL, :event_type,"
                    " :payload::jsonb, :dedupe_key, now())"
                    " ON CONFLICT (dedupe_key) DO NOTHING"
                ),
                {
                    "id": str(row_id),
                    "event_type": event_type,
                    "payload": json.dumps(payload),
                    "dedupe_key": dedupe_key,
                },
            )
            await session.commit()
    except Exception as exc:
        _emit_failures_total += 1
        _log.error(
            "event_emitter: failed to persist %s event dedupe_key=%s (swallowed, "
            "fire-and-forget contract preserved) — emit_failures_total=%d: %s",
            event_type,
            dedupe_key,
            _emit_failures_total,
            exc,
            exc_info=exc,
        )
