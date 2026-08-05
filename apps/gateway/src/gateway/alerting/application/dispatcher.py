"""AlertDispatcher — polls undelivered alert_events rows and POSTs to webhook.

CONTRACT (FROZEN @ health-alerting v3, hardened @ audit-remediation):
  - run_once()    → process all currently claimable undelivered rows; bounded retry per row
  - run_forever() → background loop calling run_once() every interval_seconds
  - webhook_url="" → idle (no POSTs)
  - 2xx            → set delivered_at = now() on the row (never deleted)
  - non-2xx after retry_max attempts within one cycle → leave undelivered; try next cycle
  - non-2xx for dead_letter_max_cycles consecutive cycles → mark dead-lettered (excluded
    from future claims); never retried forever
  - URL logged host-only (never full URL with credentials or tokens)
  - Payload: exactly {event_id, event_type, tenant_id, key_id, created_at, payload}

Double-delivery prevention (audit-remediation H1): each row is claimed via
`SELECT ... FOR UPDATE SKIP LOCKED` and the claim + the delivery attempt + the terminal
state update (delivered / cycle-attempt bump / dead-letter) all happen inside ONE DB
transaction per row. A concurrent dispatcher replica's claim query, run while this
transaction is still open, will SKIP the locked row entirely (see `_claim_and_process_by_id`)
— it is invisible to a second claimant until the first transaction commits, by which point
the row's `delivered_at` (or dead-letter marker) is already durable. This trades a held
connection/row-lock for the duration of one row's delivery attempt (bounded by
`retry_max` POSTs with small backoff) in exchange for correctness under >1 replica —
an accepted tradeoff for a low-volume alerting queue.

Retry-forever / dead-letter (audit-remediation H2): the alert_events schema is NOT owned
by this bounded context (it belongs to the spend-windows task migrations,
`apps/gateway/migrations/versions/f4a9b3c7e8d2_alert_events.py` +
`a1b2c3d4e5f6_..._tenant_nullable.py`) and no schema-changing migration is in scope for
this package. Cross-cycle attempt bookkeeping is therefore persisted, additively and
namespaced, under a reserved `__alert_dispatch` sub-object of the EXISTING `payload` JSONB
column (`{"cycle_attempts": int, "dead_letter": bool, "dead_lettered_at": iso}`) via a
`payload || jsonb_build_object(...)` shallow merge — every other top-level payload key
(including the FROZEN soft_budget_exceeded fields) is left untouched. This metadata is
ALWAYS stripped before building the outbound webhook body (see `_build_outbound_payload`)
so it is purely internal bookkeeping and never leaks to a third-party webhook receiver.
A dedicated `dead_lettered_at`/`retry_count` COLUMN (as the original table docstring
anticipated) remains the long-term correct home for this and is a follow-up outside this
package's file-scope boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.alerting.domain.ports import WebhookSink

_log = logging.getLogger(__name__)

# Exponential back-off base for retry delay (seconds)
_RETRY_BASE_DELAY = 0.1

# Reserved, namespaced key inside alert_events.payload used ONLY for dispatcher
# bookkeeping (cycle_attempts / dead_letter). Never forwarded to the webhook receiver.
_DISPATCH_META_KEY = "__alert_dispatch"

_UNDELIVERED_WHERE = (
    "delivered_at IS NULL"
    "   AND COALESCE((payload->:meta_key->>'dead_letter')::boolean, false) = false"
)

_SNAPSHOT_IDS_SQL = text(
    f"SELECT id FROM alert_events WHERE {_UNDELIVERED_WHERE} ORDER BY created_at"
)

_CLAIM_BY_ID_SQL = text(
    "SELECT id, event_type, tenant_id, key_id, created_at, payload, dedupe_key"
    " FROM alert_events"
    f" WHERE id = :row_id AND {_UNDELIVERED_WHERE}"
    " FOR UPDATE SKIP LOCKED"
)


def _host_only(url: str) -> str:
    """Return only the host portion of a URL for safe logging."""
    try:
        return urlparse(url).hostname or url
    except Exception:
        return "<url>"


class AlertDispatcher:
    """Background dispatcher: polls undelivered alert_events and POSTs to webhook.

    Double-delivery prevention: see module docstring (`FOR UPDATE SKIP LOCKED` claim,
    held across the whole per-row delivery attempt inside one transaction).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        webhook_sink: WebhookSink,
        webhook_url: str,
        retry_max: int = 3,
        dead_letter_max_cycles: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._sink = webhook_sink
        self._webhook_url = webhook_url
        self._retry_max = retry_max
        self._dead_letter_max_cycles = dead_letter_max_cycles
        self._host = _host_only(webhook_url) if webhook_url else ""
        #: In-process counter surfacing dead-letter events for observability. A future
        #: task can wire this into `gateway.observability.metrics` (out of this
        #: package's file-scope boundary — see report).
        self.dead_letter_events_total = 0

    async def run_once(self) -> None:
        """Attempt delivery, once each, of every row undelivered at the START of this
        call (a snapshot pass — matches the pre-hardening contract of one bounded
        delivery attempt per row per cycle, never draining/re-claiming the same row
        repeatedly within a single `run_once()`).

        Each row in the snapshot is still claimed individually via
        `FOR UPDATE SKIP LOCKED` (see `_claim_and_process_by_id`) so a concurrent
        dispatcher replica processing the SAME row concurrently is safely skipped here.
        """
        if not self._webhook_url:
            # Disabled — idle without error
            return

        row_ids = await self._fetch_claimable_ids()
        if not row_ids:
            return

        _log.warning(
            "dispatcher: found %d undelivered event(s) claimable for %s",
            len(row_ids),
            self._host,
        )
        for row_id in row_ids:
            await self._claim_and_process_by_id(row_id)

    async def run_forever(self, *, interval_seconds: float = 5.0) -> None:
        """Background loop: run_once() every interval_seconds."""
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                _log.warning("dispatcher: background loop error (swallowed): %s", exc, exc_info=exc)
            await asyncio.sleep(interval_seconds)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _fetch_claimable_ids(self) -> list[object]:
        """Snapshot read (no lock held) of every row id currently eligible for delivery.

        Deliberately unlocked/point-in-time — the actual concurrency-safe claim happens
        per-id in `_claim_and_process_by_id`. A row that another replica claims between
        this snapshot and our per-id claim attempt is safely skipped there (SKIP LOCKED),
        never double-delivered.
        """
        async with self._session_factory() as session:
            result = await session.execute(_SNAPSHOT_IDS_SQL, {"meta_key": _DISPATCH_META_KEY})
            return [row[0] for row in result.fetchall()]

    async def _claim_and_process_by_id(self, row_id: object) -> None:
        """Claim exactly ONE row (by id) via `FOR UPDATE SKIP LOCKED` and process it to
        completion inside a single transaction.

        The claim, the delivery attempts, and the terminal write (delivered_at OR
        updated dispatch metadata) all happen inside that ONE transaction/session — the
        row's lock is held for the whole delivery attempt so a concurrent dispatcher
        replica's claim attempt on the SAME id is SKIPPED entirely (returns immediately,
        no-op) rather than double-delivering it. If another replica already claimed (or
        delivered, or dead-lettered) this row since our snapshot read, the claim finds
        nothing and this is a silent no-op for this cycle.
        """
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    _CLAIM_BY_ID_SQL, {"row_id": row_id, "meta_key": _DISPATCH_META_KEY}
                )
                mapping = result.mappings().first()
                if mapping is None:
                    return
                row = dict(mapping)
                _log.warning(
                    "dispatcher: claimed undelivered event %s for delivery to %s",
                    row["id"],
                    self._host,
                )
                await self._deliver_claimed_row(session, row)

    async def _deliver_claimed_row(self, session: AsyncSession, row: dict) -> None:  # type: ignore[type-arg]
        """Attempt delivery of one already-claimed (locked) row within `session`'s
        open transaction; bounded retry within this cycle, then either mark delivered,
        bump the cross-cycle attempt count, or dead-letter it once the cap is hit."""
        payload = _build_outbound_payload(row)

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
                await session.execute(
                    text("UPDATE alert_events SET delivered_at = now() WHERE id = :id"),
                    {"id": str(row["id"])},
                )
                _log.debug(
                    "dispatcher: delivered event %s to %s (status=%d)",
                    row["id"],
                    self._host,
                    status,
                )
                return

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

        # All in-cycle attempts exhausted — bump the cross-cycle counter; dead-letter
        # once dead_letter_max_cycles is reached so this row is never retried forever.
        await self._record_cycle_exhausted(session, row)

    async def _record_cycle_exhausted(self, session: AsyncSession, row: dict) -> None:  # type: ignore[type-arg]
        """Persist the bumped cycle_attempts count; dead-letter on cap exhaustion."""
        payload = row["payload"]
        existing_meta = payload.get(_DISPATCH_META_KEY, {}) if isinstance(payload, dict) else {}
        cycle_attempts = int(existing_meta.get("cycle_attempts", 0)) + 1
        dead_letter = cycle_attempts >= self._dead_letter_max_cycles

        meta: dict[str, object] = {"cycle_attempts": cycle_attempts, "dead_letter": dead_letter}
        if dead_letter:
            meta["dead_lettered_at"] = datetime.now(UTC).isoformat()

        await session.execute(
            text(
                "UPDATE alert_events"
                " SET payload = payload || jsonb_build_object(:meta_key::text, :meta::jsonb)"
                " WHERE id = :id"
            ),
            {"meta_key": _DISPATCH_META_KEY, "meta": _to_json(meta), "id": str(row["id"])},
        )

        if dead_letter:
            self.dead_letter_events_total += 1
            _log.error(
                "dispatcher: event %s DEAD-LETTERED after %d cycle(s) of exhausted "
                "retries (retry_max=%d per cycle) — will NOT be retried again; "
                "dead_letter_events_total=%d",
                row["id"],
                cycle_attempts,
                self._retry_max,
                self.dead_letter_events_total,
            )
        else:
            _log.warning(
                "dispatcher: event %s undelivered after cycle %d/%d — will retry next cycle",
                row["id"],
                cycle_attempts,
                self._dead_letter_max_cycles,
            )


def _to_json(value: dict[str, object]) -> str:
    return json.dumps(value)


def _build_outbound_payload(row: dict) -> dict[str, object]:  # type: ignore[type-arg]
    """Build the exact contracted webhook body from a claimed row.

    The reserved `__alert_dispatch` bookkeeping sub-key (if present) is ALWAYS stripped —
    it is internal dispatcher metadata, never forwarded to the webhook receiver.
    """
    inner_payload = row["payload"] if isinstance(row["payload"], dict) else {}
    if _DISPATCH_META_KEY in inner_payload:
        inner_payload = {k: v for k, v in inner_payload.items() if k != _DISPATCH_META_KEY}

    return {
        "event_id": str(row["id"]),
        "event_type": str(row["event_type"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] is not None else None,
        "key_id": str(row["key_id"]) if row["key_id"] is not None else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] is not None else None,
        "payload": inner_payload,
    }
