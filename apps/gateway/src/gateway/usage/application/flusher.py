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


class MalformedUsageEventError(ValueError):
    """A usage event with a deterministically-unparseable REQUIRED field.

    Raised for any bad int / Decimal / UUID among the required fields (token counts,
    status, cost_usd, tenant_id, key_id, quantity, provider_cost). Signals the caller
    to DROP the entry (ACK without retry): such an event will never become parseable
    on redelivery, so retrying it forever is worse than dropping it — and worse still,
    because XAUTOCLAIM reclaims oldest-first, a permanently-failing entry would starve
    every other entry in its reclaim batch. Distinct from generic DB errors (which the
    caller must NOT ACK — those are transient/retryable).
    """


def _event_decode(v: bytes | str) -> str:
    return v.decode("utf-8") if isinstance(v, bytes) else v


def _event_field(fields: dict[Any, Any], key: str) -> str:
    """Read a stream-event field by name, tolerating bytes|str keys/values."""
    for k, v in fields.items():
        if _event_decode(k) == key:
            return _event_decode(v)
    return ""


async def insert_usage_row(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    record_id: uuid.UUID,
    fields: dict[Any, Any],
) -> None:
    """Parse a usage event's fields and upsert ONE usage_records row (PK=record_id).

    Shared by the flusher (normal Redis-stream path) and the recorder's durable
    fallback (Redis unavailable). Idempotent via ON CONFLICT (id) DO NOTHING, so a
    slow-Redis event that reached BOTH the fallback and a later flush collapses to
    one row (both key on the same deterministic id).

    Raises MalformedUsageEventError on an unparseable tenant_id/key_id (caller
    drops/ACKs — never retryable). Propagates DB errors as-is (caller decides
    whether to retry / skip ACK). The session.begin() commit boundary lives INSIDE
    this helper; any ACK ordering stays OUTSIDE it (the flusher's concern).
    """
    # Parse REQUIRED fields inside one guard: any deterministic parse failure (bad
    # int/Decimal/UUID) is POISON — the event will never become parseable on redelivery,
    # so we raise MalformedUsageEventError and the caller drops/ACKs it rather than let
    # it re-fail and (via oldest-first XAUTOCLAIM) starve its whole reclaim batch forever.
    # Optional fields (team_id, pricing_snapshot_id, raw) keep their own inner guards and
    # degrade to NULL/fallback instead of raising. `session.execute` stays OUTSIDE this
    # guard so a (retryable) DB error is NEVER misclassified as poison.
    try:
        model_id = _event_field(fields, "model_id")
        prompt_tokens = int(_event_field(fields, "prompt_tokens") or "0")
        completion_tokens = int(_event_field(fields, "completion_tokens") or "0")
        cost_usd_str = _event_field(fields, "cost_usd") or "0"
        pricing_snapshot_id_str = _event_field(fields, "pricing_snapshot_id")
        status = int(_event_field(fields, "status") or "0")
        raw_str = _event_field(fields, "raw") or "{}"
        # team-attribution: missing/empty/corrupt → NULL (old-format event safety).
        team_id_str = _event_field(fields, "team_id")
        team_id: uuid.UUID | None = None
        if team_id_str:
            try:
                team_id = uuid.UUID(team_id_str)
            except ValueError:
                team_id = None
        # pricing-units: backward-compat with pre-v7 events (missing → 'per_token'/NULL).
        pricing_unit_str = _event_field(fields, "pricing_unit") or "per_token"
        quantity_str = _event_field(fields, "quantity")
        quantity: Decimal | None = Decimal(quantity_str) if quantity_str else None
        # tiered-token-billing: per-tier counts (missing on old events → 0).
        cached_tokens = int(_event_field(fields, "cached_tokens") or "0")
        reasoning_tokens = int(_event_field(fields, "reasoning_tokens") or "0")
        # prompt-cache-passthrough: Anthropic cache-write count (old events → 0).
        cache_creation_tokens = int(_event_field(fields, "cache_creation_tokens") or "0")
        # gpt-realtime-relay-billing: dual-stream audio counts (old events → 0).
        audio_prompt_tokens = int(_event_field(fields, "audio_prompt_tokens") or "0")
        audio_completion_tokens = int(_event_field(fields, "audio_completion_tokens") or "0")
        audio_cached_tokens = int(_event_field(fields, "audio_cached_tokens") or "0")
        # provider-cost-reconciliation: basis + raw upstream cost (old events → catalog/NULL).
        cost_basis = _event_field(fields, "cost_basis") or "catalog"
        provider_cost_str = _event_field(fields, "provider_cost")
        provider_cost: Decimal | None = (
            Decimal(provider_cost_str) if provider_cost_str else None
        )
        # stream-usage-completeness: usage provenance (old events → 'frame').
        usage_source = _event_field(fields, "usage_source") or "frame"
        # provider-generation-id-capture: ""→NULL (the cost-recovery lookup key).
        provider_generation_id = _event_field(fields, "provider_generation_id") or None

        # Required identifiers — a bad UUID here is poison, same as a bad numeric.
        tenant_id = uuid.UUID(_event_field(fields, "tenant_id"))
        key_id = uuid.UUID(_event_field(fields, "key_id"))

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
    except (ValueError, ArithmeticError) as exc:
        # int()/UUID() raise ValueError; Decimal on garbage raises decimal.InvalidOperation
        # (⊂ ArithmeticError). Deterministic → poison → caller drops (ACK, no retry).
        raise MalformedUsageEventError(str(exc)) from exc

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO usage_records"
                    " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
                    "  cost_usd, status, pricing_snapshot_id, raw, team_id,"
                    "  pricing_unit, quantity, cached_tokens, reasoning_tokens,"
                    "  cache_creation_tokens,"
                    "  audio_prompt_tokens, audio_completion_tokens, audio_cached_tokens,"
                    "  cost_basis, provider_cost, usage_source,"
                    "  provider_generation_id)"
                    " VALUES"
                    " (:id, :tenant_id, :key_id, :model_id, :prompt_tokens,"
                    "  :completion_tokens, :cost_usd, :status, :pricing_snapshot_id,"
                    "  :raw, :team_id, :pricing_unit, :quantity,"
                    "  :cached_tokens, :reasoning_tokens,"
                    "  :cache_creation_tokens,"
                    "  :audio_prompt_tokens, :audio_completion_tokens, :audio_cached_tokens,"
                    "  :cost_basis, :provider_cost, :usage_source,"
                    "  :provider_generation_id)"
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
                    "team_id": team_id,
                    "pricing_unit": pricing_unit_str,
                    "quantity": quantity,
                    "cached_tokens": cached_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                    "audio_prompt_tokens": audio_prompt_tokens,
                    "audio_completion_tokens": audio_completion_tokens,
                    "audio_cached_tokens": audio_cached_tokens,
                    "cost_basis": cost_basis,
                    "provider_cost": provider_cost,
                    "usage_source": usage_source,
                    "provider_generation_id": provider_generation_id,
                },
            )


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
        pel_reclaim_idle_ms: int = 60000,
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory
        # B5: XAUTOCLAIM min-idle for the live loop (from GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS).
        # drain_until_empty overrides this to 0 (reclaim aggressively at shutdown).
        self._pel_reclaim_idle_ms = pel_reclaim_idle_ms

    async def flush_once(self, reclaim_min_idle_ms: int | None = None) -> None:
        """Process all currently pending stream messages.

        Creates consumer group on first call (MKSTREAM — stream need not exist).
        Silently returns on Redis / Postgres errors after logging.

        reclaim_min_idle_ms: XAUTOCLAIM min-idle for THIS call (default: the
        configured live-loop idle). drain_until_empty passes 0 to reclaim a
        pre-existing PEL regardless of idle age.
        """
        try:
            await self._ensure_group()
        except Exception as exc:
            _log.warning("flusher: ensure_group failed", exc_info=exc)
            return

        # B5: reclaim entries stranded in the PEL by a crashed/restarted consumer
        # (delivered via '>' but never ACKed) BEFORE reading new entries, so they are
        # re-flushed rather than stranded forever. Reprocessing is idempotent (ON
        # CONFLICT id). A reclaim failure must NOT block new-event processing — log
        # and continue to the '>' read below.
        idle = self._pel_reclaim_idle_ms if reclaim_min_idle_ms is None else reclaim_min_idle_ms
        try:
            result = await self._redis.xautoclaim(
                STREAM_KEY,
                CONSUMER_GROUP,
                CONSUMER_NAME,
                min_idle_time=idle,
                start_id="0-0",
                count=100,
            )
            # redis-py returns [next_cursor, [(id, {fields}), ...], [deleted_ids]]
            # (older servers omit the deleted list → 2-element). Unpack defensively.
            claimed = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
        except Exception as exc:
            _log.warning("flusher: xautoclaim reclaim failed", exc_info=exc)
            claimed = []
        # Per-entry, NOT batch-level: XAUTOCLAIM re-owns + resets idle for the WHOLE batch
        # at claim time, so a bare exception aborting the loop would leave the un-processed
        # remainder re-claimed (idle reset) yet never billed — and the oldest failing entry
        # would be re-claimed first every cycle, starving its siblings forever. Isolating
        # each entry means one bad entry can never stall the rest. (MalformedUsageEventError
        # is drop+ACKed inside _process_entry; only a retryable error reaches here → no ACK,
        # redelivered next cycle.)
        for entry_id, fields in claimed:
            try:
                await self._process_entry(entry_id, fields)
            except Exception as exc:
                _log.warning(
                    "flusher: reclaimed entry %s failed to process (will retry next cycle)",
                    entry_id,
                    exc_info=exc,
                )

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
        # Per-entry isolation (same rationale as the reclaim loop): one entry that fails
        # with a retryable error must not abort the batch and strand its siblings in the PEL.
        for _stream_key, entries in messages:
            for entry_id, fields in entries:
                try:
                    await self._process_entry(entry_id, fields)
                except Exception as exc:
                    _log.warning(
                        "flusher: entry %s failed to process (will retry next cycle)",
                        entry_id,
                        exc_info=exc,
                    )

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
        """Insert one ledger row (via the shared helper) and ACK the stream entry."""
        if not fields:
            # A tombstoned/empty entry carries no billable data and can never parse —
            # drop it (ACK) so it cannot poison the batch/PEL. (Deleted ids normally
            # surface in XAUTOCLAIM's result[2], not result[1], so this is defensive.)
            _log.error("flusher: dropping empty/None stream entry %s (unrecoverable)", entry_id)
            await self._ack(entry_id)
            return
        raw_entry_id = _event_decode(entry_id)
        # cost-recovery (v30 t6) + B4: an event may carry an EXPLICIT deterministic id
        # so a duplicate (recovery, or a recorder fallback whose XADD also landed) dedups
        # via ON CONFLICT (id). Absent or malformed → the stream-id derivation (byte-identical
        # to the pre-explicit-id behavior).
        explicit_id = _event_field(fields, "id")
        if explicit_id:
            try:
                record_id = uuid.UUID(explicit_id)
            except ValueError:
                _log.warning("flusher: malformed explicit id %r; using stream id", explicit_id)
                record_id = stream_id_to_uuid(raw_entry_id)
        else:
            record_id = stream_id_to_uuid(raw_entry_id)

        try:
            await insert_usage_row(self._session_factory, record_id=record_id, fields=fields)
        except MalformedUsageEventError as exc:
            # Deterministically unparseable → drop (ACK) so it cannot poison the batch/PEL
            # forever. Log the FULL raw fields at ERROR so the (rare) dropped billable event
            # is recoverable from logs. A dead-letter stream would be stronger (spec-delta).
            _log.error(
                "flusher: dropping unparseable usage event %s (unrecoverable parse: %s)"
                " | raw_fields=%r",
                entry_id,
                exc,
                fields,
            )
            await self._ack(entry_id)
            return
        # A DB error propagates here (no ACK) → at-least-once redelivery.

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
                # At shutdown, reclaim a pre-existing PEL regardless of idle age
                # (the entries are stranded BECAUSE we are stopping) so drain can
                # actually clear it instead of looping until timeout.
                await self.flush_once(reclaim_min_idle_ms=0)
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
