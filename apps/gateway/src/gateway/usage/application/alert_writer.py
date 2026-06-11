"""Fire-and-forget alert event writer for soft-budget crossing detection.

This module owns the _persist_soft_budget_alert coroutine, which must be scheduled
as a fire-and-forget asyncio task (never awaited on the hot path).

Contract (FROZEN @ spend-windows — TASK.md §3 + §5):
  - Called from _check_per_key_budget() in proxy/application/use_cases.py
  - Inserts one row into alert_events with ON CONFLICT (dedupe_key) DO NOTHING
  - Swallows ALL exceptions (failures logged, never raised into the request path)
  - dedupe_key format: "soft_budget:{key_id}:{YYYYMM}"
  - payload: {"soft_budget_usd": "<decimal string>", "key_spend_usd": "<decimal string>"}
  - delivered_at: NULL at creation (health-alerting sets this)

Wire: asyncio.ensure_future(_persist_soft_budget_alert(...)) in _check_per_key_budget.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger(__name__)


async def _persist_soft_budget_alert(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    soft_budget_usd: Decimal,
    key_spend_usd: Decimal,
) -> None:
    """Insert one alert_events row for a soft-budget crossing (idempotent via UNIQUE dedupe_key).

    Swallows all exceptions — failures are logged but NEVER raised into the request path.
    This coroutine must be scheduled via asyncio.ensure_future(), never awaited directly.

    dedupe_key format: "soft_budget:{key_id}:{YYYYMM}"
    ON CONFLICT (dedupe_key) DO NOTHING ensures only 1 row per key per calendar month.
    """
    try:
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        dedupe_key = f"soft_budget:{key_id}:{yyyymm}"
        row_id = uuid.uuid4()
        payload: dict[str, Any] = {
            "soft_budget_usd": str(soft_budget_usd),
            "key_spend_usd": str(key_spend_usd),
        }

        async with session_factory() as session:
            from sqlalchemy import text  # local import to avoid module-level circular risk

            await session.execute(
                text(
                    "INSERT INTO alert_events"
                    " (id, tenant_id, key_id, event_type, payload, dedupe_key, created_at)"
                    " VALUES (:id, :tenant_id, :key_id, :event_type,"
                    " :payload::jsonb, :dedupe_key, now())"
                    " ON CONFLICT (dedupe_key) DO NOTHING"
                ),
                {
                    "id": str(row_id),
                    "tenant_id": str(tenant_id),
                    "key_id": str(key_id),
                    "event_type": "soft_budget_exceeded",
                    "payload": json.dumps(payload),
                    "dedupe_key": dedupe_key,
                },
            )
            await session.commit()
    except Exception as exc:
        _log.warning(
            "alert_writer: failed to persist soft_budget_exceeded event (swallowed)",
            exc_info=exc,
            extra={
                "tenant_id": str(tenant_id),
                "key_id": str(key_id),
                "dedupe_key": (
                    f"soft_budget:{key_id}:{datetime.datetime.now(datetime.UTC).strftime('%Y%m')}"
                ),
            },
        )
