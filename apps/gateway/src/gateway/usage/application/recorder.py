"""RecordingUsageRecorder — implements the UsageRecorder port.

Responsibilities per TASK.md §1 Must:
  1. Resolve latest pricing snapshot for the model + tenant markup_pct.
  2. Compute cost_usd using ALL Decimal arithmetic.
  3. Push one JSON event to Redis Stream key `usage:events`.
  4. INCRBYFLOAT the per-tenant-month spend counter.
  5. NEVER raise into the proxy path — all failures swallowed + logged.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.usage.infrastructure.redis_stream import STREAM_KEY

_log = logging.getLogger(__name__)

_ZERO = Decimal("0")


class RecordingUsageRecorder:
    """Write-behind usage recorder.

    Pushes one Redis Stream event per record() call, then INCRBYFLOATs the
    advisory spend counter.  All failures are swallowed and logged — Redis
    unavailability MUST NOT fail completions (TASK §1).

    Constructor args:
      redis: redis.asyncio client (or duck-typed fake in tests).
      session_factory: async_sessionmaker[AsyncSession] bound to the DB pool.
    """

    def __init__(
        self,
        *,
        redis: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, object] | None,
        status: int,
        team_id: uuid.UUID | None = None,
        cached: bool = False,
    ) -> None:
        """Append a usage event to the Redis Stream.

        Must not raise.  Redis unavailability is logged and swallowed.
        cached=True: injects cached=true into raw field and forces cost_usd=0
        (the INCRBYFLOAT guard only runs when cost_usd > 0, so no spend counter increment).
        """
        try:
            await self._record_internal(
                tenant_id=tenant_id,
                key_id=key_id,
                model=model,
                usage=usage,
                status=status,
                team_id=team_id,
                cached=cached,
            )
        except Exception as exc:
            _log.warning(
                "usage_recorder.record failed (swallowed)",
                exc_info=exc,
                extra={
                    "tenant_id": str(tenant_id),
                    "model": model,
                    "status": status,
                },
            )

    async def _record_internal(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, object] | None,
        status: int,
        team_id: uuid.UUID | None = None,
        cached: bool = False,
    ) -> None:
        """Core record logic — may raise; caller swallows."""
        # Resolve pricing + markup
        prompt_tokens = 0
        completion_tokens = 0
        cost_usd = _ZERO
        pricing_snapshot_id: str = ""

        if not cached:
            # Only fetch pricing for non-cached records; cached hits always cost 0
            async with self._session_factory() as session:
                pricing = await _fetch_latest_pricing(session, model)
                markup_pct = await _fetch_markup_pct(session, tenant_id)

            if pricing is not None and usage is not None:
                snap_id, prompt_price, completion_price = pricing
                pricing_snapshot_id = str(snap_id)
                prompt_tokens = int(str(usage.get("prompt_tokens", 0)))
                completion_tokens = int(str(usage.get("completion_tokens", 0)))
                cost_usd = (
                    Decimal(str(prompt_tokens)) * Decimal(str(prompt_price))
                    + Decimal(str(completion_tokens)) * Decimal(str(completion_price))
                ) * (Decimal("1") + Decimal(str(markup_pct)) / Decimal("100"))
        else:
            # Cached hit: cost=0; still read token counts from usage for the record
            if usage is not None:
                prompt_tokens = int(str(usage.get("prompt_tokens", 0)))
                completion_tokens = int(str(usage.get("completion_tokens", 0)))

        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        raw_payload: dict[str, object] = {
            "tenant_id": str(tenant_id),
            "key_id": str(key_id),
            "model": model,
            "usage": usage,
            "status": status,
        }
        if cached:
            raw_payload["cached"] = True

        event_fields: dict[str, str] = {
            "tenant_id": str(tenant_id),
            "key_id": str(key_id),
            "model_id": model,
            "prompt_tokens": str(prompt_tokens),
            "completion_tokens": str(completion_tokens),
            "cost_usd": str(cost_usd),
            "pricing_snapshot_id": pricing_snapshot_id,
            "status": str(status),
            "raw": json.dumps(raw_payload),
            "created_at": created_at,
        }

        # Push to Redis Stream — must not drop the event even on cost-0
        await self._redis.xadd(STREAM_KEY, event_fields)

        # Advisory spend counters — IEEE 754 float, ledger is source of truth
        if cost_usd > _ZERO:
            yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
            # Tenant counter (existing)
            spend_key = f"usage:spend:{tenant_id}:{yyyymm}"
            await self._redis.incrbyfloat(spend_key, float(cost_usd))
            # Per-key counter (key-governance seam — M10/A2)
            # Keyed: usage:spend:key:{key_id}:{YYYYMM}
            # This counter is read by CompletionUseCase._check_per_key_budget() for
            # most-specific-wins enforcement. Soft-budget alerting will also read it
            # when the spend-windows/health-alerting tasks land.
            per_key_spend_key = f"usage:spend:key:{key_id}:{yyyymm}"
            await self._redis.incrbyfloat(per_key_spend_key, float(cost_usd))
            # Per-team counter (team-governance seam — fail-open same as per-key)
            # Keyed: usage:spend:team:{team_id}:{YYYYMM}
            # Only written when the key has a team attribution (team_id is set).
            # Read by CompletionUseCase._check_team_budget() for team budget enforcement.
            if team_id is not None:
                per_team_spend_key = f"usage:spend:team:{team_id}:{yyyymm}"
                await self._redis.incrbyfloat(per_team_spend_key, float(cost_usd))


async def _fetch_latest_pricing(
    session: AsyncSession,
    model_id: str,
) -> tuple[uuid.UUID, Decimal, Decimal] | None:
    """Return (snapshot_id, prompt_price, completion_price) or None if not found."""
    row = (
        await session.execute(
            text(
                "SELECT id, prompt_usd_per_token, completion_usd_per_token"
                " FROM pricing_snapshots"
                " WHERE model_id = :model_id"
                " ORDER BY captured_at DESC"
                " LIMIT 1"
            ),
            {"model_id": model_id},
        )
    ).fetchone()
    if row is None:
        return None
    return (
        uuid.UUID(str(row[0])),
        Decimal(str(row[1])),
        Decimal(str(row[2])),
    )


async def _fetch_markup_pct(session: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    """Return the tenant's markup_pct; defaults to 0 if tenant not found."""
    row = (
        await session.execute(
            text("SELECT markup_pct FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    if row is None:
        return _ZERO
    return Decimal(str(row[0]))
