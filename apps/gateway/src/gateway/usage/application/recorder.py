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

    # Typed capability seam (UsageRecordExtras in proxy/domain/ports.py):
    # declares which additive record() kwargs this recorder accepts. Callers
    # filter extras against this set — v1-Protocol fakes lack the attribute
    # and therefore receive only the base kwargs.
    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
        }
    )

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
        guardrail_blocked: bool = False,
        blocked_by: str | None = None,
        pii_masked: bool = False,
        pricing_unit: str | None = None,
        quantity: Decimal | None = None,
    ) -> None:
        """Append a usage event to the Redis Stream.

        Must not raise.  Redis unavailability is logged and swallowed.
        cached=True: injects cached=true into raw field and forces cost_usd=0
        (the INCRBYFLOAT guard only runs when cost_usd > 0, so no spend counter increment).
        guardrail_blocked=True: injects guardrail_blocked=true + blocked_by into raw field.
        pii_masked=True: injects pii_masked=true into raw field.
        pricing_unit: discriminator; None → defaults to snapshot value or 'per_token'.
        quantity: billed quantity for non-token units; None → per_token path.
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
                guardrail_blocked=guardrail_blocked,
                blocked_by=blocked_by,
                pii_masked=pii_masked,
                pricing_unit=pricing_unit,
                quantity=quantity,
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
        guardrail_blocked: bool = False,
        blocked_by: str | None = None,
        pii_masked: bool = False,
        pricing_unit: str | None = None,
        quantity: Decimal | None = None,
    ) -> None:
        """Core record logic — may raise; caller swallows."""
        # Resolve pricing + markup
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        reasoning_tokens = 0
        cost_usd = _ZERO
        pricing_snapshot_id: str = ""
        resolved_pricing_unit = "per_token"
        resolved_quantity: Decimal | None = None
        # provider-cost-reconciliation: default catalog basis; provider_cost stays NULL
        # unless an upstream cost is consumed below.
        cost_basis = "catalog"
        provider_cost: Decimal | None = None

        if not cached:
            # Only fetch pricing for non-cached records; cached hits always cost 0
            async with self._session_factory() as session:
                pricing = await _fetch_latest_pricing(session, model)
                markup_pct = await _fetch_markup_pct(session, tenant_id)

            if pricing is not None:
                (
                    snap_id,
                    prompt_price,
                    completion_price,
                    snapshot_pricing_unit,
                    unit_usd_per_unit,
                    cached_input_price,
                    reasoning_price,
                ) = pricing
                pricing_snapshot_id = str(snap_id)

                # Resolve pricing_unit: extras > snapshot > default
                # Only accept the four known values; unknown → per_token (backward-compat)
                _known_units = {"per_token", "per_image", "per_second", "per_character"}
                if pricing_unit is not None and pricing_unit in _known_units:
                    resolved_pricing_unit = pricing_unit
                elif snapshot_pricing_unit in _known_units:
                    resolved_pricing_unit = snapshot_pricing_unit
                else:
                    resolved_pricing_unit = "per_token"

                if resolved_pricing_unit == "per_token":
                    # tiered-token-billing: split cached/reasoning tiers; no-tier case is
                    # BYTE-IDENTICAL to the v6 path (see compute_per_token_cost_usd).
                    if usage is not None:
                        prompt_tokens = int(str(usage.get("prompt_tokens", 0)))
                        completion_tokens = int(str(usage.get("completion_tokens", 0)))
                        cached_tokens = _safe_tier(usage, "prompt_tokens_details", "cached_tokens")
                        reasoning_tokens = _safe_tier(
                            usage, "completion_tokens_details", "reasoning_tokens"
                        )
                    # provider-cost-reconciliation: PREFER the upstream-reported cost when
                    # present; otherwise the UNCHANGED catalog path (byte-identical floor).
                    provider_cost = _safe_provider_cost(usage)
                    if provider_cost is not None:
                        cost_usd = provider_cost * (
                            Decimal("1") + Decimal(str(markup_pct)) / Decimal("100")
                        )
                        cost_basis = "provider"
                    else:
                        cost_usd = compute_per_token_cost_usd(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cached_tokens=cached_tokens,
                            reasoning_tokens=reasoning_tokens,
                            prompt_price=prompt_price,
                            completion_price=completion_price,
                            cached_price=cached_input_price,
                            reasoning_price=reasoning_price,
                            markup_pct=markup_pct,
                            model=model,
                        )
                else:
                    # Non-token path: per_image / per_second / per_character
                    prompt_tokens = 0
                    completion_tokens = 0
                    # Resolve quantity from extras; clamp negative to 0
                    if quantity is not None:
                        q = Decimal(str(quantity))
                        if q < _ZERO:
                            _log.warning(
                                "negative_quantity_clamped",
                                extra={
                                    "pricing_unit": resolved_pricing_unit,
                                    "quantity": str(quantity),
                                    "model": model,
                                },
                            )
                            q = _ZERO
                        resolved_quantity = q
                    else:
                        resolved_quantity = _ZERO
                    # unit_price NULL on a non-token snapshot → cost=0 + WARNING
                    if unit_usd_per_unit is None:
                        _log.warning(
                            "unit_price_missing_for_non_token_unit",
                            extra={
                                "pricing_unit": resolved_pricing_unit,
                                "model": model,
                                "pricing_snapshot_id": pricing_snapshot_id,
                            },
                        )
                        cost_usd = _ZERO
                    else:
                        cost_usd = (
                            resolved_quantity
                            * Decimal(str(unit_usd_per_unit))
                            * (Decimal("1") + Decimal(str(markup_pct)) / Decimal("100"))
                        )
        else:
            # Cached hit: cost=0; still read token counts from usage for the record
            if usage is not None:
                prompt_tokens = int(str(usage.get("prompt_tokens", 0)))
                completion_tokens = int(str(usage.get("completion_tokens", 0)))
                cached_tokens = _safe_tier(usage, "prompt_tokens_details", "cached_tokens")
                reasoning_tokens = _safe_tier(
                    usage, "completion_tokens_details", "reasoning_tokens"
                )
            # Preserve the pricing_unit from extras even on cache hits (for event fields)
            _known_units = {"per_token", "per_image", "per_second", "per_character"}
            if pricing_unit is not None and pricing_unit in _known_units:
                resolved_pricing_unit = pricing_unit

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
        if guardrail_blocked:
            raw_payload["guardrail_blocked"] = True
        if blocked_by is not None:
            raw_payload["blocked_by"] = blocked_by
        if pii_masked:
            raw_payload["pii_masked"] = True

        # Encode quantity: empty string for per_token (NULL), str(q) for non-token
        quantity_str = str(resolved_quantity) if resolved_quantity is not None else ""

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
            # team-attribution: empty string encodes NULL (backward-compatible with old consumers)
            "team_id": str(team_id) if team_id is not None else "",
            # pricing-units: new contract fields (pricing-units TASK.md §3)
            "pricing_unit": resolved_pricing_unit,
            "quantity": quantity_str,
            # tiered-token-billing: per-tier token counts (TASK.md §3)
            "cached_tokens": str(cached_tokens),
            "reasoning_tokens": str(reasoning_tokens),
            # provider-cost-reconciliation: billed basis + raw upstream cost (TASK.md §3)
            # provider_cost "" encodes NULL (catalog rows carry no upstream cost).
            "cost_basis": cost_basis,
            "provider_cost": str(provider_cost) if provider_cost is not None else "",
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


def _safe_tier(usage: dict[str, Any] | None, outer: str, inner: str) -> int:
    """Extract a token-tier count (cached / reasoning) from a usage dict, fail-safe.

    Returns max(0, count) for a valid int; 0 for any malformed shape (missing,
    non-dict `*_details`, non-int member, or negative). Per TASK.md §1 Reject:
    a malformed tier is treated as ABSENT, never an error.
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get(outer)
    if not isinstance(details, dict):
        return 0
    val = details.get(inner)
    # bool is an int subclass — exclude it explicitly; non-int → absent.
    if isinstance(val, bool) or not isinstance(val, int):
        return 0
    return max(0, val)


def _safe_provider_cost(usage: dict[str, Any] | None) -> Decimal | None:
    """Extract an upstream-reported cost (USD) from a usage dict, fail-safe.

    Returns Decimal(cost) for a valid NON-NEGATIVE number (provider-cost-reconciliation
    TASK.md §3) — 0 IS valid (a free model that genuinely cost the upstream nothing is
    authoritative, not absent). Returns None for any other shape so the caller falls back
    to catalog math:
      - missing / non-dict usage / None / str / non-numeric -> None
      - bool (int subclass) -> None (a flag is not a cost)
      - negative -> None + WARNING "provider_cost_rejected" (never trust a negative cost)
    Never raises — accuracy degrades to catalog, the request always ships.
    """
    if not isinstance(usage, dict):
        return None
    val = usage.get("cost")
    # bool is an int subclass — exclude it explicitly before the numeric check.
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    if val < 0:
        _log.warning("provider_cost_rejected", extra={"cost": val})
        return None
    return Decimal(str(val))


def compute_per_token_cost_usd(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    reasoning_tokens: int,
    prompt_price: Decimal,
    completion_price: Decimal,
    cached_price: Decimal | None,
    reasoning_price: Decimal | None,
    markup_pct: Decimal,
    model: str = "",
) -> Decimal:
    """Per-tier token cost (tiered-token-billing TASK.md §3).

    When both tier counts are 0 the FLAT path runs the exact v6 expression verbatim
    (byte-identical). Otherwise the tiered expression splits fresh/cached input and
    fresh/reasoning output; a NULL tier price falls back to its base price; fresh
    counts clamp to max(0, …) so cost is never negative (logs "tier_token_clamped").
    """
    markup = Decimal("1") + Decimal(str(markup_pct)) / Decimal("100")
    if cached_tokens == 0 and reasoning_tokens == 0:
        # FLAT PATH — verbatim v6 operand order; byte-identical to the pre-tier code.
        return (
            Decimal(str(prompt_tokens)) * Decimal(str(prompt_price))
            + Decimal(str(completion_tokens)) * Decimal(str(completion_price))
        ) * markup

    # TIERED PATH
    fresh_in = prompt_tokens - cached_tokens
    fresh_out = completion_tokens - reasoning_tokens
    if fresh_in < 0 or fresh_out < 0:
        _log.warning(
            "tier_token_clamped",
            extra={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "cached_tokens": cached_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
        )
    fresh_in = max(0, fresh_in)
    fresh_out = max(0, fresh_out)
    cprice = Decimal(str(cached_price)) if cached_price is not None else Decimal(str(prompt_price))
    rprice = (
        Decimal(str(reasoning_price))
        if reasoning_price is not None
        else Decimal(str(completion_price))
    )
    return (
        Decimal(str(fresh_in)) * Decimal(str(prompt_price))
        + Decimal(str(cached_tokens)) * cprice
        + Decimal(str(fresh_out)) * Decimal(str(completion_price))
        + Decimal(str(reasoning_tokens)) * rprice
    ) * markup


async def _fetch_latest_pricing(
    session: AsyncSession,
    model_id: str,
) -> tuple[uuid.UUID, Decimal, Decimal, str, Decimal | None, Decimal | None, Decimal | None] | None:
    """Return (snapshot_id, prompt_price, completion_price, pricing_unit,
    unit_usd_per_unit, cached_input_price, reasoning_price).

    Returns None if no pricing snapshot found for the model.

    Additive extensions:
      pricing_unit      — discriminator; defaults to 'per_token' for old rows. (pricing-units)
      unit_usd_per_unit — per-unit price for non-token rows; NULL otherwise. (pricing-units)
      cached_input_price / reasoning_price — per-tier prices; NULL → base-price
                          fallback. (tiered-token-billing)
    """
    row = (
        await session.execute(
            text(
                "SELECT id, prompt_usd_per_token, completion_usd_per_token,"
                " pricing_unit, unit_usd_per_unit,"
                " cached_input_usd_per_token, reasoning_usd_per_token"
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
    unit_usd: Decimal | None = Decimal(str(row[4])) if row[4] is not None else None
    cached_price: Decimal | None = Decimal(str(row[5])) if row[5] is not None else None
    reasoning_price: Decimal | None = Decimal(str(row[6])) if row[6] is not None else None
    return (
        uuid.UUID(str(row[0])),
        Decimal(str(row[1])),
        Decimal(str(row[2])),
        str(row[3]) if row[3] is not None else "per_token",
        unit_usd,
        cached_price,
        reasoning_price,
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
