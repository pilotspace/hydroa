"""UsageLedgerFlusher — reads Redis Stream, inserts rows into Postgres ledger.

Responsibilities per TASK.md §1 Must:
  - Consumer group: XGROUP CREATE with MKSTREAM (idempotent).
  - Read: XREADGROUP with count=100, block=0 (non-blocking in tests).
  - Insert: ON CONFLICT (id) DO NOTHING — exactly-once semantic in ledger.
  - Acknowledge: XACK after successful INSERT (at-least-once + idempotency).
  - flush_once() is the deterministic test entry point.
  - Background lifespan task runs flush_once() on 1s interval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.usage.infrastructure.redis_stream import (
    CONSUMER_GROUP,
    CONSUMER_NAME,
    STREAM_KEY,
    stream_id_to_uuid,
)

_log = logging.getLogger(__name__)


class UsageLedgerFlusher:
    """Read pending events from Redis Stream and upsert them into usage_records.

    flush_once() processes all currently pending messages (XREADGROUP COUNT 100
    with block=0 — returns immediately).  Called directly by tests; also
    invoked by the background lifespan task at 1s intervals.
    """

    def __init__(
        self,
        *,
        redis: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory

    async def flush_once(self) -> None:
        """Process all currently pending stream messages.

        Creates consumer group on first call (MKSTREAM — stream need not exist).
        Silently returns on Redis / Postgres errors after logging.
        """
        try:
            await self._ensure_group()
        except Exception as exc:
            _log.warning("flusher: ensure_group failed", exc_info=exc)
            return

        try:
            messages = await self._redis.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {STREAM_KEY: ">"},
                count=100,
                block=0,
            )
        except Exception as exc:
            _log.warning("flusher: xreadgroup failed", exc_info=exc)
            return

        if not messages:
            return

        # messages format: [(stream_key, [(id, {field: value, ...}), ...])]
        for _stream_key, entries in messages:
            for entry_id, fields in entries:
                await self._process_entry(entry_id, fields)

    async def _ensure_group(self) -> None:
        """Create consumer group if it doesn't exist (MKSTREAM for first call)."""
        try:
            await self._redis.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP means group already exists — safe to ignore
            err_str = str(exc)
            if "BUSYGROUP" not in err_str:
                raise

    async def _process_entry(
        self, entry_id: bytes | str, fields: dict[bytes | str, bytes | str]
    ) -> None:
        """Insert one ledger row and ACK the stream entry."""

        def _decode(v: bytes | str) -> str:
            return v.decode("utf-8") if isinstance(v, bytes) else v

        def _field(key: str) -> str:
            # Fields may be bytes or str depending on decode_responses setting
            for k, v in fields.items():
                if _decode(k) == key:
                    return _decode(v)
            return ""

        raw_entry_id = _decode(entry_id) if isinstance(entry_id, bytes) else entry_id
        record_id = stream_id_to_uuid(raw_entry_id)

        tenant_id_str = _field("tenant_id")
        key_id_str = _field("key_id")
        model_id = _field("model_id")
        prompt_tokens = int(_field("prompt_tokens") or "0")
        completion_tokens = int(_field("completion_tokens") or "0")
        cost_usd_str = _field("cost_usd") or "0"
        pricing_snapshot_id_str = _field("pricing_snapshot_id")
        status = int(_field("status") or "0")
        raw_str = _field("raw") or "{}"

        try:
            tenant_id = uuid.UUID(tenant_id_str)
            key_id = uuid.UUID(key_id_str)
        except ValueError as exc:
            _log.error("flusher: invalid UUID in stream entry %s: %s", entry_id, exc)
            # ACK to avoid re-delivery of unparseable entry
            await self._ack(entry_id)
            return

        pricing_snapshot_id: uuid.UUID | None = None
        if pricing_snapshot_id_str:
            try:
                pricing_snapshot_id = uuid.UUID(pricing_snapshot_id_str)
            except ValueError:
                pricing_snapshot_id = None

        try:
            raw_dict: dict[str, Any] = json.loads(raw_str)
        except (json.JSONDecodeError, ValueError):
            raw_dict = {"_raw": raw_str}

        cost_usd = Decimal(cost_usd_str)

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO usage_records"
                        " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
                        "  cost_usd, status, pricing_snapshot_id, raw)"
                        " VALUES"
                        " (:id, :tenant_id, :key_id, :model_id, :prompt_tokens,"
                        "  :completion_tokens, :cost_usd, :status, :pricing_snapshot_id,"
                        "  :raw)"
                        " ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": record_id,
                        "tenant_id": tenant_id,
                        "key_id": key_id,
                        "model_id": model_id,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_usd": cost_usd,
                        "status": status,
                        "pricing_snapshot_id": pricing_snapshot_id,
                        "raw": json.dumps(raw_dict),
                    },
                )

        # ACK only after successful INSERT (at-least-once guarantee)
        await self._ack(entry_id)

    async def _ack(self, entry_id: bytes | str) -> None:
        try:
            await self._redis.xack(STREAM_KEY, CONSUMER_GROUP, entry_id)
        except Exception as exc:
            _log.warning("flusher: xack failed for %s: %s", entry_id, exc)

    async def drain_until_empty(self, *, timeout: float) -> None:  # noqa: ASYNC109
        """Drain all pending Redis Stream entries until PEL is empty or timeout.

        Contract (ops-hardening TASK.md §3):
          - timeout=0 → skip drain immediately, log warning, return.
          - Loop: flush_once() → check XPENDING count → exit if 0 or elapsed >= timeout.
          - Never raises; logs WARNING on timeout or Redis errors.
          - Idempotent: ON CONFLICT DO NOTHING in INSERT; XACK only after successful insert.
        """
        if timeout <= 0:
            _log.warning(
                "flusher: drain skipped (timeout=%s <= 0); events remain durable in Redis PEL",
                timeout,
            )
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if loop.time() >= deadline:
                remaining = await self._backlog_size()
                _log.warning(
                    "flusher: drain timed out after %.1fs; %s events remain durable in the stream",
                    timeout,
                    remaining if remaining >= 0 else "unknown",
                )
                # M8: emit drain_timeout system event (bounded 0.5s; must never
                # block or fail the shutdown path). dedupe_key is per-episode —
                # a constant key would let ON CONFLICT swallow every drain
                # timeout after the first one in the table's lifetime.
                try:
                    from gateway.alerting.application.event_emitter import emit_system_event

                    emit_task = asyncio.ensure_future(
                        emit_system_event(
                            self._session_factory,
                            event_type="drain_timeout",
                            dedupe_key=f"drain_timeout:{uuid.uuid4()}",
                            payload={"timeout_seconds": timeout},
                        )
                    )
                    await asyncio.wait_for(asyncio.shield(emit_task), timeout=0.5)
                except Exception as exc:
                    _log.warning("flusher: drain_timeout alert not persisted", exc_info=exc)
                return

            try:
                await self.flush_once()
            except Exception as exc:
                _log.warning("flusher: drain flush_once error", exc_info=exc)

            # Drain is complete only when the PEL is empty AND no unread
            # entries remain for the group (flush_once reads at most 100 per
            # call — a deeper backlog must keep the loop going while the
            # timeout allows; M2's invariant is "committed or durable").
            backlog = await self._backlog_size()
            if backlog == 0:
                _log.info("flusher: drain complete — PEL empty, no unread backlog")
                return
            if backlog < 0:
                # Redis unreachable — keep trying until the bounded timeout.
                await asyncio.sleep(0.05)
                continue

            # Yield to event loop briefly before next flush iteration
            await asyncio.sleep(0.01)

    async def _backlog_size(self) -> int:
        """Pending (delivered, unacked) + undelivered entries for the group; -1 if Redis fails."""
        try:
            summary = await self._redis.xpending(STREAM_KEY, CONSUMER_GROUP)
            pending = int(summary.get("pending", 0)) if isinstance(summary, dict) else 0
        except Exception as exc:
            _log.warning("flusher: drain backlog check failed", exc_info=exc)
            return -1
        # Undelivered-entry lag is a best-effort refinement: real Redis ≥7
        # reports it via XINFO GROUPS; clients/fakes without it fall back to
        # the PEL-only view (entries stay durable in the stream regardless).
        lag = 0
        try:
            for group in await self._redis.xinfo_groups(STREAM_KEY):
                name = group.get("name")
                if isinstance(name, bytes):
                    name = name.decode()
                if name == CONSUMER_GROUP:
                    # 'lag' is None when Redis cannot compute it (entries
                    # trimmed); treat unknown as 0 so the drain terminates.
                    lag = int(group.get("lag") or 0)
                    break
        except Exception:
            lag = 0
        return pending + lag

    async def run_forever(self, interval_seconds: float = 1.0) -> None:
        """Background loop — call flush_once() every interval_seconds."""
        while True:
            try:
                await self.flush_once()
            except Exception as exc:
                _log.warning("flusher: background loop error", exc_info=exc)
            await asyncio.sleep(interval_seconds)
