"""AlertDispatcher — polls undelivered alert_events rows and POSTs to webhook.

CONTRACT (FROZEN @ health-alerting v3):
  - run_once()    → process all currently undelivered rows; bounded retry per row
  - run_forever() → background loop calling run_once() every interval_seconds
  - webhook_url="" → idle (no POSTs)
  - 2xx            → set delivered_at = now() on the row (never deleted)
  - non-2xx after retry_max attempts → leave undelivered; try next cycle
  - URL logged host-only (never full URL with credentials or tokens)
  - Payload: exactly {event_id, event_type, tenant_id, key_id, created_at, payload}
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.alerting.domain.ports import WebhookSink

_log = logging.getLogger(__name__)

# Exponential back-off base for retry delay (seconds)
_RETRY_BASE_DELAY = 0.1


def _host_only(url: str) -> str:
    """Return only the host portion of a URL for safe logging."""
    try:
        return urlparse(url).hostname or url
    except Exception:
        return "<url>"


class AlertDispatcher:
    """Background dispatcher: polls undelivered alert_events and POSTs to webhook.

    Double-delivery prevention: each row is claimed via SELECT ... WHERE delivered_at IS NULL
    and marked with delivered_at = now() inside a transaction only on 2xx response.
    run_once() processes all undelivered rows in a single pass; concurrent invocations
    are safe because the UPDATE is row-level and only succeeds once (the DB UNIQUE dedupe_key
    + partial index ensure the row is found only once per pass).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        webhook_sink: WebhookSink,
        webhook_url: str,
        retry_max: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._sink = webhook_sink
        self._webhook_url = webhook_url
        self._retry_max = retry_max
        self._host = _host_only(webhook_url) if webhook_url else ""

    async def run_once(self) -> None:
        """Query all undelivered rows; attempt delivery; set delivered_at on 2xx."""
        if not self._webhook_url:
            # Disabled — idle without error
            return

        rows = await self._fetch_undelivered()
        if not rows:
            return

        _log.warning(
            "dispatcher: delivering %d undelivered event(s) to %s",
            len(rows),
            self._host,
        )

        for row in rows:
            await self._deliver_row(row)

    async def run_forever(self, *, interval_seconds: float = 5.0) -> None:
        """Background loop: run_once() every interval_seconds."""
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                _log.warning("dispatcher: background loop error (swallowed): %s", exc, exc_info=exc)
            await asyncio.sleep(interval_seconds)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _fetch_undelivered(self) -> list[dict]:  # type: ignore[type-arg]
        """Return all rows with delivered_at IS NULL, ordered by created_at."""
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, event_type, tenant_id, key_id, created_at, payload, dedupe_key"
                    " FROM alert_events"
                    " WHERE delivered_at IS NULL"
                    " ORDER BY created_at"
                )
            )
            return [dict(r._mapping) for r in result.fetchall()]

    async def _deliver_row(self, row: dict) -> None:  # type: ignore[type-arg]
        """Attempt to deliver one row with bounded exponential retry."""
        payload = {
            "event_id": str(row["id"]),
            "event_type": str(row["event_type"]),
            "tenant_id": str(row["tenant_id"]) if row["tenant_id"] is not None else None,
            "key_id": str(row["key_id"]) if row["key_id"] is not None else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] is not None else None,
            "payload": row["payload"] if isinstance(row["payload"], dict) else {},
        }

        for attempt in range(1, self._retry_max + 1):
            try:
                status = await self._sink.post_json(self._webhook_url, payload)
            except Exception as exc:
                _log.warning(
                    "dispatcher: POST to %s failed on attempt %d/%d (connection error): %s",
                    self._host,
                    attempt,
                    self._retry_max,
                    exc,
                )
                if attempt < self._retry_max:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue

            if 200 <= status < 300:
                # Success — mark delivered
                await self._mark_delivered(row["id"])
                _log.debug(
                    "dispatcher: delivered event %s to %s (status=%d)",
                    row["id"],
                    self._host,
                    status,
                )
                return

            # Non-2xx
            _log.warning(
                "dispatcher: POST to %s returned %d for event %s (attempt %d/%d)",
                self._host,
                status,
                row["id"],
                attempt,
                self._retry_max,
            )
            if attempt < self._retry_max:
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

        # All attempts exhausted — leave undelivered
        _log.warning(
            "dispatcher: event %s undelivered after %d attempts — will retry next cycle",
            row["id"],
            self._retry_max,
        )

    async def _mark_delivered(self, row_id: object) -> None:
        """Set delivered_at = now() for the given row id."""
        try:
            async with self._session_factory() as session:
                await session.execute(
                    text("UPDATE alert_events SET delivered_at = now() WHERE id = :id"),
                    {"id": str(row_id)},
                )
                await session.commit()
        except Exception as exc:
            _log.warning(
                "dispatcher: failed to mark event %s delivered: %s", row_id, exc, exc_info=exc
            )
