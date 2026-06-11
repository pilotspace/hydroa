"""Shared fire-and-forget system event emitter.

All system events (circuit_breaker_open, drain_timeout, upstream_health_*) use this helper.
Pattern:
  - INSERT with ON CONFLICT (dedupe_key) DO NOTHING — idempotent
  - Swallows ALL exceptions — failures logged, never raised into the caller
  - Fire-and-forget via asyncio.ensure_future() on the hot path
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger(__name__)


async def emit_system_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> None:
    """Insert one alert_events row for a system event (tenant_id NULL, key_id NULL).

    Idempotent via UNIQUE dedupe_key + ON CONFLICT DO NOTHING.
    Swallows all exceptions — never raises into caller.
    """
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
        _log.warning(
            "event_emitter: failed to persist %s event (swallowed): %s",
            event_type,
            exc,
            exc_info=exc,
        )
