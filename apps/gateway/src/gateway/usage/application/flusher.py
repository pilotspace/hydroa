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

    async def run_forever(self, interval_seconds: float = 1.0) -> None:
        """Background loop — call flush_once() every interval_seconds."""
        while True:
            try:
                await self.flush_once()
            except Exception as exc:
                _log.warning("flusher: background loop error", exc_info=exc)
            await asyncio.sleep(interval_seconds)
