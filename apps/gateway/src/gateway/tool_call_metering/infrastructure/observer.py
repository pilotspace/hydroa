"""MeteringToolCallObserver — implements the FROZEN
gateway.mcp_connector.domain.ports.ToolCallObserver Protocol (mcp-connector-passthrough
TASK.md §3, FROZEN @ v2 — CR-1 added `call_id`) verbatim.

tool-call-metering TASK.md §3 CONTRACT (FROZEN @ v1). Once this task's DI wiring
(main.py) replaces the sibling's NoopToolCallObserver default with an instance of this
class, it becomes the ONLY production implementation of ToolCallObserver — the sole
hook every successfully-dialed (non-refused, non-blocked) MCP tool call fires through
on its way into the ONE shared billing path (usage_records via RecordingUsageRecorder /
the shared rate-card resolver). This task never writes a usage_records row itself and
never computes cost itself — it forwards into the injected UsageRecorder, byte-for-byte
per M2, and inherits Decimal-only cost computation, cost_basis/usage_source provenance,
and swallow-and-log failure handling entirely from RecordingUsageRecorder (M10).

Exactly-once (R2, CR-1): the frozen Protocol carries `call_id: UUID`, minted exactly
once per tool invocation at the upstream admission point specifically so THIS task can
dedupe a double-invocation. RecordingUsageRecorder.record()/record_with_outcome()/
_record_internal() have NO event_id passthrough — event_id is always freshly minted
via uuid.uuid4() inside _record_internal — and TASK.md §5 Scope restricts this task's
edit to recorder.py to the two `_known_units` literal additions only (no other line),
so this task cannot honor R2 by threading a deterministic id INTO the recorder's own
insert path. Instead, MeteringToolCallObserver owns its OWN dedupe gate: a Redis
SET-NX-EX on a key deterministically derived from call_id, using the SAME redis client
instance already wired for every other billed call (no second Redis instance — M1). A
duplicate call_id within the TTL window is dropped BEFORE it ever reaches
usage_recorder.record() — so exactly one usage_records row results per logical tool
call, achieving R2's OUTCOME (billed exactly once) without widening recorder.py's
frozen signature. This is a disclosed build-time design choice (TASK.md §3's own literal
CONTRACT code block still shows the pre-CR-1 call shape with no event_id parameter) —
see the task's final build report for the full residue note.

Design-for-failure (CLAUDE.md): the dedupe check is fail-OPEN — if the Redis SETNX
itself raises (e.g. Redis unreachable), this observer logs a warning and proceeds to
call usage_recorder.record() anyway. A missed bill (R1's never-a-silent-$0 bar) is a
worse outcome than the rare double-bill this residual risk covers during a Redis
outage — the SAME asymmetry the milestone's own accepted-residual-risk framing names.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Literal

from gateway.proxy.domain.ports import UsageRecorder

_log = logging.getLogger(__name__)

#: The sentinel, first-party-priced catalog model_id this task's migration seeds
#: (models row active=false/modality='tool_call'; pricing_snapshots row
#: pricing_unit='per_tool_call'). TASK.md §3 Schema / Glossary delta.
MCP_TOOL_CALL_MODEL_ID = "mcp_tool_call"

#: Dedupe-guard TTL — comfortably longer than any realistic fire-and-forget
#: retry/timeout race window at the upstream call site (R2/CR-1), short enough to
#: never accumulate meaningfully in Redis. Not a billing-permanence guarantee (unlike
#: the correction-counted guard's 30-day TTL in recorder.py) — this guard only needs
#: to outlive a single retry race, not a reconciliation sweep.
_DEDUPE_TTL_SECONDS = 24 * 60 * 60


class MeteringToolCallObserver:
    """Bills exactly one `usage_records` row per successfully-dialed MCP tool call,
    through the SAME shared rate-card resolver every other billed unit already uses.

    Constructor args:
      usage_recorder: the SAME UsageRecorder instance wired for every other billed
        call (M1 "no second Redis/session instance") — typically the production
        RecordingUsageRecorder already on app.state.usage_recorder.
      redis: the SAME redis.asyncio client already wired on app.state.redis_client —
        used ONLY for the call_id dedupe gate (R2/CR-1), never as a billing ledger.
    """

    def __init__(self, *, usage_recorder: UsageRecorder, redis: Any) -> None:
        self._usage_recorder = usage_recorder
        self._redis = redis

    async def record(
        self,
        *,
        call_id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        server_host: str,
        tool_name: str,
        status: Literal["success"],
        latency_ms: int,
    ) -> None:
        """Never raises and never blocks its caller (M10) — the mcp-connector-
        passthrough call site fires this fire-and-forget; any failure here (dedupe
        check OR the forwarded record() call) is logged and swallowed, exactly like
        every other billed-call failure mode in this codebase.

        latency_ms is accepted per the frozen Protocol signature but not persisted by
        v1 (no usage_records column for it — TASK.md §3 CONTRACT note).
        """
        del latency_ms  # accepted-but-unused, per §3 CONTRACT note (v1 scope)
        del status  # the Protocol only ever carries "success" (Literal) — no branch

        try:
            claimed = await self._claim(call_id)
        except Exception as exc:  # dedupe must never block billing
            _log.warning(
                "tool_call_metering_dedupe_check_failed",
                exc_info=exc,
                extra={"call_id": str(call_id), "tenant_id": str(tenant_id)},
            )
            # Fail OPEN: proceed to record anyway (see module docstring).
            claimed = True

        if not claimed:
            _log.info(
                "tool_call_metering_duplicate_dropped",
                extra={
                    "call_id": str(call_id),
                    "tenant_id": str(tenant_id),
                    "server_host": server_host,
                    "tool_name": tool_name,
                },
            )
            return

        try:
            # M2: forwards, unconditionally and byte-for-byte, into the injected
            # UsageRecorder — no re-derivation of tenant/key identity, no extra DB
            # read beyond what record() itself already performs.
            #
            # A plain dict[str, Any] unpacked via **kwargs (not explicit keyword
            # args) mirrors proxy/application/use_cases.py:_dispatch_record's own
            # idiom — the base UsageRecorder Protocol only declares
            # tenant_id/key_id/model/usage/status; pricing_unit/quantity/tags are
            # additive extras a concrete recorder (RecordingUsageRecorder) accepts
            # but the narrow Protocol type does not statically declare.
            kwargs: dict[str, Any] = {
                "tenant_id": tenant_id,
                "key_id": key_id,
                "model": MCP_TOOL_CALL_MODEL_ID,
                "usage": None,
                "status": 200,
                "pricing_unit": "per_tool_call",
                "quantity": Decimal("1"),
                "tags": {"mcp_server": server_host, "mcp_tool": tool_name},
            }
            await self._usage_recorder.record(**kwargs)
        except Exception as exc:  # M10: never raises into the caller
            _log.warning(
                "tool_call_metering_record_failed",
                exc_info=exc,
                extra={"call_id": str(call_id), "tenant_id": str(tenant_id)},
            )

    async def _claim(self, call_id: uuid.UUID) -> bool:
        """SET key NX EX — True iff THIS invocation is the first to claim call_id
        (i.e. the one that gets billed); False for every later duplicate within the
        TTL window."""
        key = f"mcp:tool_call:metered:{call_id}"
        claimed = await self._redis.set(key, "1", nx=True, ex=_DEDUPE_TTL_SECONDS)
        return bool(claimed)


__all__ = ["MCP_TOOL_CALL_MODEL_ID", "MeteringToolCallObserver"]
