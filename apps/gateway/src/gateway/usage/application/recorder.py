"""RecordingUsageRecorder — implements the UsageRecorder port.

Responsibilities per TASK.md §1 Must:
  1. Resolve latest pricing snapshot for the model + tenant markup_pct.
  2. Compute cost_usd using ALL Decimal arithmetic.
  3. Push one JSON event to Redis Stream key `usage:events`.
  4. INCRBYFLOAT the per-tenant-month spend counter.
  5. NEVER raise into the proxy path — all failures swallowed + logged.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.usage.application.rate_card_resolver import (
    resolve_markup_pct,
    resolve_region_multiplier,
    resolve_tier_multiplier,
)
from gateway.usage.infrastructure.redis_stream import STREAM_KEY

_log = logging.getLogger(__name__)

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class UsageRecordOutcome:
    """record()'s result — the ALREADY-COMPUTED cost, for a settle/release consumer
    (credits-ledger TASK.md §3: "settle must consume the cost already computed by
    RecordingUsageRecorder — never recompute price"). Duck-typed by callers (via
    getattr) rather than a hard import, so the UsageRecorder Protocol widens to
    `object | None` instead of taking a dependency on this usage-module type.

    usage_record_id: the deterministic id also used as the usage_records row PK
      (mirrors `insert_usage_row`'s `record_id` — the flusher/fallback insert use
      this SAME id, so it is valid to cite as `usage_records.id` even though the
      row may land a moment later via the async stream-flush path).
    free: True for a zero-cost outcome (cache hit; `cost_usd` is always 0 then) —
      the settle consumer treats this as a RELEASE rather than a SETTLE (M5).
    """

    tenant_id: uuid.UUID
    usage_record_id: uuid.UUID
    cost_usd: Decimal
    free: bool


# usage-flusher-durability (B4): per-CALL bound on every Redis op in the record path
# (NOT client-level — the redis client is shared across budget/ratelimiter/bandwidth,
# a wide blast radius). Mirrors usage/api/router.py:_RATELIMIT_REDIS_TIMEOUT_SECONDS.
_USAGE_REDIS_TIMEOUT_SECONDS = 5.0
# TTL for the per-correction advisory-counter idempotency guard (cost-recovery v30 t6).
# A correction is permanent, but the guard need only outlive any realistic re-fire window
# (inline recovery racing the periodic sweep) — 30 days is far beyond it.
_CORRECTION_COUNTED_TTL_S = 60 * 60 * 24 * 30


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
            "usage_source",
            "provider_generation_id",
            "disconnect_estimate",
            "request_id",
            "tags",
            "tier_served",
            "tier_capacity_degraded",
            "agent_principal_id",
            "cc_session_id",
            "cc_agent_id",
            "cc_parent_agent_id",
            "credential_source",
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
        usage_source: str | None = None,
        provider_generation_id: str | None = None,
        disconnect_estimate: bool = False,
        request_id: uuid.UUID | None = None,
        tags: dict[str, str] | None = None,
        tier_served: str = "standard",
        tier_capacity_degraded: bool = False,
        agent_principal_id: uuid.UUID | None = None,
        cc_session_id: str | None = None,
        cc_agent_id: str | None = None,
        cc_parent_agent_id: str | None = None,
        credential_source: str | None = None,
    ) -> None:
        """Append a usage event to the Redis Stream.

        Must not raise.  Redis unavailability is logged and swallowed.
        cached=True: injects cached=true into raw field and forces cost_usd=0
        (the INCRBYFLOAT guard only runs when cost_usd > 0, so no spend counter increment).
        guardrail_blocked=True: injects guardrail_blocked=true + blocked_by into raw field.
        pii_masked=True: injects pii_masked=true into raw field.
        pricing_unit: discriminator; None → defaults to snapshot value or 'per_token'.
        quantity: billed quantity for non-token units; None → per_token path.
        request_id: correlation key (request-log-metering-fields) — stored into
          raw["request_id"] when set; NOT a new column (usage_records is FROZEN).
        tags: client-supplied key/value request labels (cost-attribution-tags TASK.md
          §3) — stored into the dedicated usage_records.tags JSONB column. None/empty
          → "{}" (byte-identical to a request that never sent X-Gateway-Tags).
        cc_session_id/cc_agent_id/cc_parent_agent_id: Claude Code's
          x-claude-code-session-id/-agent-id/-parent-agent-id header values
          (claude-gateway-protocol-compat TASK.md §3 M4) — stored verbatim into
          raw["cc_session_id"]/raw["cc_agent_id"]/raw["cc_parent_agent_id"] when set;
          NOT a new column (usage_records is FROZEN), same idiom as request_id.

        Return value is always None — byte-identical to every pre-existing caller/test
        (see record_with_outcome() for the credits-ledger settle hook's variant, which
        shares this exact body but returns the computed outcome instead of discarding it).
        """
        await self.record_with_outcome(
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
            usage_source=usage_source,
            provider_generation_id=provider_generation_id,
            disconnect_estimate=disconnect_estimate,
            request_id=request_id,
            tags=tags,
            tier_served=tier_served,
            tier_capacity_degraded=tier_capacity_degraded,
            agent_principal_id=agent_principal_id,
            cc_session_id=cc_session_id,
            cc_agent_id=cc_agent_id,
            cc_parent_agent_id=cc_parent_agent_id,
            credential_source=credential_source,
        )
        return None

    async def record_with_outcome(
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
        usage_source: str | None = None,
        provider_generation_id: str | None = None,
        disconnect_estimate: bool = False,
        request_id: uuid.UUID | None = None,
        tags: dict[str, str] | None = None,
        tier_served: str = "standard",
        tier_capacity_degraded: bool = False,
        agent_principal_id: uuid.UUID | None = None,
        cc_session_id: str | None = None,
        cc_agent_id: str | None = None,
        cc_parent_agent_id: str | None = None,
        credential_source: str | None = None,
    ) -> UsageRecordOutcome | None:
        """Identical to record(), but RETURNS the computed outcome instead of discarding
        it (credits-ledger TASK.md §3: "settle must consume the cost already computed by
        RecordingUsageRecorder — never recompute price"). A NEW public method rather than
        changing record()'s own return value, so every pre-existing caller/test asserting
        `record(...) is None` (e.g. tests/usage/test_usage_metering.py's XADD-fallback
        case) stays byte-identical. Called instead of record() by _dispatch_record ONLY
        when a credit hold is open for the current request (see _credit_hold_ctx) AND the
        wired recorder exposes this method (duck-typed via hasattr) — a v1-Protocol test
        fake without it is unaffected, falls back to record(), and the credit-hold
        reconciliation sweep is the backstop for anything left unsettled.

        Must not raise. Returns None on any swallowed failure — nothing to settle then.
        """
        try:
            return await self._record_internal(
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
                usage_source=usage_source,
                provider_generation_id=provider_generation_id,
                disconnect_estimate=disconnect_estimate,
                request_id=request_id,
                tags=tags,
                tier_served=tier_served,
                tier_capacity_degraded=tier_capacity_degraded,
                agent_principal_id=agent_principal_id,
                cc_session_id=cc_session_id,
                cc_agent_id=cc_agent_id,
                cc_parent_agent_id=cc_parent_agent_id,
                credential_source=credential_source,
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
            return None

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
        usage_source: str | None = None,
        provider_generation_id: str | None = None,
        disconnect_estimate: bool = False,
        request_id: uuid.UUID | None = None,
        tags: dict[str, str] | None = None,
        tier_served: str = "standard",
        tier_capacity_degraded: bool = False,
        agent_principal_id: uuid.UUID | None = None,
        cc_session_id: str | None = None,
        cc_agent_id: str | None = None,
        cc_parent_agent_id: str | None = None,
        credential_source: str | None = None,
    ) -> UsageRecordOutcome:
        """Core record logic — may raise; caller swallows."""
        # Resolve pricing + markup
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        reasoning_tokens = 0
        cache_creation_tokens = 0
        # gpt-realtime-relay-billing: audio-tier counts, always 0 for every non-realtime usage dict.
        audio_prompt_tokens = 0
        audio_completion_tokens = 0
        audio_cached_tokens = 0
        cost_usd = _ZERO
        pricing_snapshot_id: str = ""
        resolved_pricing_unit = "per_token"
        resolved_quantity: Decimal | None = None
        # provider-cost-reconciliation: default catalog basis; provider_cost stays NULL
        # unless an upstream cost is consumed below.
        cost_basis = "catalog"
        provider_cost: Decimal | None = None
        # disconnect-provider-cost (v33): bound on every path so the post-costing stamp
        # below can strip markup even on the cached / no-pricing branches (where it stays 0).
        markup_pct: Decimal = _ZERO
        # region-pricing (TASK.md §3 M2): per-region multiplier composed with markup at
        # THIS single resolution point. Identity (1.0, no premium) on the cached branch,
        # where it is never fetched — mirrors the existing markup_pct skip.
        region_multiplier: Decimal = Decimal("1")
        # service-tiers TASK.md §3 (M11): per-tier markup multiplier composed with markup +
        # region at THIS single resolution point. Identity (1.0, no premium) on the cached
        # branch, where it is never fetched — mirrors the existing markup_pct/region skip.
        tier_multiplier: Decimal = Decimal("1")

        if not cached:
            # Only fetch pricing for non-cached records; cached hits always cost 0
            async with self._session_factory() as session:
                pricing = await _fetch_latest_pricing(session, model)
                markup_pct = await _fetch_markup_pct(session, tenant_id, model)
                region_multiplier = await _fetch_region_multiplier(session, tenant_id, model)
                # service-tiers TASK.md §3 (M11): resolved once here, alongside
                # region_multiplier, in the SAME batch (no N+1) — keyed on tier_served
                # (the tier ACTUALLY SERVED, never the requested/selected tier — §1 M9).
                tier_multiplier = await resolve_tier_multiplier(
                    session, tenant_id, model, tier_served
                )

            if pricing is not None:
                (
                    snap_id,
                    prompt_price,
                    completion_price,
                    snapshot_pricing_unit,
                    unit_usd_per_unit,
                    cached_input_price,
                    reasoning_price,
                    cache_creation_price,
                    audio_prompt_price,
                    audio_completion_price,
                    audio_cached_price,
                ) = pricing
                pricing_snapshot_id = str(snap_id)

                # Resolve pricing_unit: extras > snapshot > default
                # Only accept the four known values; unknown → per_token (backward-compat)
                # tool-call-metering TASK.md §3 M7: additive "per_tool_call" string
                # (this literal's sibling occurrence below gains the identical edit —
                # two independent, pre-existing duplicated literals, both intentionally
                # kept in lockstep here).
                _known_units = {
                    "per_token",
                    "per_image",
                    "per_second",
                    "per_character",
                    "per_tool_call",
                }
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
                        # prompt-cache-passthrough (TASK.md §3): read Anthropic cache-write count.
                        cache_creation_tokens = _safe_tier(
                            usage, "prompt_tokens_details", "cache_creation_tokens"
                        )
                        # gpt-realtime-relay-billing (TASK.md §3): dual-stream audio tiers, read
                        # via the SAME _safe_tier fallback — absent for every non-realtime usage
                        # dict, so these are always 0 there (byte-identical).
                        audio_prompt_tokens = _safe_tier(
                            usage, "input_token_details", "audio_tokens"
                        )
                        audio_completion_tokens = _safe_tier(
                            usage, "output_token_details", "audio_tokens"
                        )
                        audio_cached_tokens = _safe_tier(
                            usage, "input_token_details", "cached_tokens"
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
                            cache_creation_tokens=cache_creation_tokens,
                            audio_prompt_tokens=audio_prompt_tokens,
                            audio_completion_tokens=audio_completion_tokens,
                            audio_cached_tokens=audio_cached_tokens,
                            prompt_price=prompt_price,
                            completion_price=completion_price,
                            cached_price=cached_input_price,
                            reasoning_price=reasoning_price,
                            cache_creation_price=cache_creation_price,
                            audio_prompt_price=audio_prompt_price,
                            audio_completion_price=audio_completion_price,
                            audio_cached_price=audio_cached_price,
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
            # tool-call-metering TASK.md §3 M7: additive "per_tool_call" string (see
            # this literal's sibling occurrence above for the duplication note).
            _known_units = {
                "per_token",
                "per_image",
                "per_second",
                "per_character",
                "per_tool_call",
            }
            if pricing_unit is not None and pricing_unit in _known_units:
                resolved_pricing_unit = pricing_unit

        # region-pricing (TASK.md §3 M2): apply the region multiplier exactly once here —
        # AFTER markup is already folded into cost_usd (every branch above: provider_cost,
        # per-token, non-token), BEFORE the disconnect back-derivation must divide it back
        # out (M3). A harmless no-op on the cached branch (cost_usd is already 0 there;
        # region_multiplier stays the Decimal("1") identity — never fetched, per M2).
        # service-tiers TASK.md §3 (M11): tier_multiplier extends the SAME line — a
        # harmless no-op on the cached branch (tier_multiplier stays the Decimal("1")
        # identity there too).
        cost_usd = cost_usd * region_multiplier * tier_multiplier

        # disconnect-provider-cost (v33): a NON-RECOVERABLE client-disconnect (non-OpenRouter
        # or no generation id) gets no out-of-band recovery, so a positive partial estimate
        # would otherwise sit as a catalog row with provider_cost NULL — billed ~$0 and INVISIBLE
        # to the drift monitor (a silent $0 upstream charge). Surface it: stamp provider_cost =
        # the markup-stripped catalog cost (≈ the upstream's list charge), zero the user-billed
        # amount (never charged for a dropped response), and flip to the provider basis so the
        # unbilled-upstream filter (provider_cost>0, cost_usd=0, cost_basis='provider') counts it.
        # Zero-estimate disconnects fall through (nothing to estimate) → audit surfaces them.
        if (
            disconnect_estimate
            and provider_cost is None
            and cost_basis == "catalog"
            and cost_usd > _ZERO
        ):
            # region-pricing (TASK.md §3 M3): divide region_multiplier back out too — else
            # a disconnect estimate on a region-premium model silently inflates the
            # recorded provider_cost by the region factor (the drift trap found in
            # grounding: an EU model's back-derivation would otherwise over-report the
            # unbilled-upstream-cost signal by 1.1x).
            # service-tiers TASK.md §3 (M11): divide tier_multiplier back out too, for the
            # identical reason — a disconnect estimate on a priority-tier request would
            # otherwise silently inflate the recorded provider_cost by the tier premium.
            provider_cost = cost_usd / (
                (Decimal("1") + Decimal(str(markup_pct)) / Decimal("100"))
                * region_multiplier
                * tier_multiplier
            )
            cost_usd = _ZERO
            cost_basis = "provider"

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
        if request_id is not None:
            # request-log-metering-fields: correlation key — same idiom as the markers
            # above. NOT a new usage_records column (FROZEN @ v1, append-only); rides
            # inside the existing raw JSONB extras seam.
            raw_payload["request_id"] = str(request_id)
        # claude-gateway-protocol-compat TASK.md §3 M4: Claude Code session/subagent
        # attribution — same additive-raw-key idiom as request_id above. Absent on
        # every pre-existing row and on every row from a non-Claude-Code-originated
        # request (the three headers are simply never present there).
        if cc_session_id is not None:
            raw_payload["cc_session_id"] = cc_session_id
        if cc_agent_id is not None:
            raw_payload["cc_agent_id"] = cc_agent_id
        if cc_parent_agent_id is not None:
            raw_payload["cc_parent_agent_id"] = cc_parent_agent_id
        # fallback-usage-marker §3: platform-fallback provenance — same additive-raw-key idiom
        # as request_id / cc_session_id above. Absent on every own-key/pre-existing row (≡ byok);
        # provenance ONLY — tenant_id / cost_usd / spend counters below are untouched.
        if credential_source is not None:
            raw_payload["credential_source"] = credential_source

        # Encode quantity: empty string for per_token (NULL), str(q) for non-token
        quantity_str = str(resolved_quantity) if resolved_quantity is not None else ""

        # B4-id: one deterministic id (mirrors record_correction) — the stream-flush PK
        # and the direct-fallback PK are IDENTICAL, so a slow-but-landed XADD that BOTH
        # falls back AND later flushes collapses to one row via ON CONFLICT (id).
        event_id = uuid.uuid4()

        event_fields: dict[str, str] = {
            "id": str(event_id),
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
            # agent-identity-governance defect fix: persist agent_principal_id durably
            # (mirrors team_id's "" encodes NULL idiom exactly) so the disconnect
            # cost-recovery correction path can read it back off the anchor row —
            # previously this value only ever reached the transient Redis INCR below,
            # never a durable column, so recovery_sweep.py had no way to recover it.
            "agent_principal_id": str(agent_principal_id) if agent_principal_id is not None else "",
            # pricing-units: new contract fields (pricing-units TASK.md §3)
            "pricing_unit": resolved_pricing_unit,
            "quantity": quantity_str,
            # tiered-token-billing: per-tier token counts (TASK.md §3)
            "cached_tokens": str(cached_tokens),
            "reasoning_tokens": str(reasoning_tokens),
            # prompt-cache-passthrough (TASK.md §3): Anthropic cache-write token count.
            "cache_creation_tokens": str(cache_creation_tokens),
            # gpt-realtime-relay-billing (TASK.md §3): dual-stream audio token counts.
            "audio_prompt_tokens": str(audio_prompt_tokens),
            "audio_completion_tokens": str(audio_completion_tokens),
            "audio_cached_tokens": str(audio_cached_tokens),
            # provider-cost-reconciliation: billed basis + raw upstream cost (TASK.md §3)
            # provider_cost "" encodes NULL (catalog rows carry no upstream cost).
            "cost_basis": cost_basis,
            "provider_cost": str(provider_cost) if provider_cost is not None else "",
            # stream-usage-completeness: usage provenance (TASK.md §3). Default 'frame'
            # so every non-stream caller (no usage_source) reads true; 'stream_fallback'
            # marks a stream whose terminal usage frame was missing/partial.
            "usage_source": usage_source or "frame",
            # provider-generation-id-capture (v30 t6): the provider's SSE generation id
            # on a client-disconnect row; ""=NULL (the lookup key for cost-recovery).
            "provider_generation_id": provider_generation_id or "",
            # cost-attribution-tags (TASK.md §3): client-supplied request labels ->
            # the dedicated usage_records.tags JSONB column. Falsy (None/{}) -> "{}",
            # byte-identical to a request that never sent X-Gateway-Tags (M3).
            "tags": json.dumps(tags) if tags else "{}",
            # service-tiers TASK.md §3 (M9): tier ACTUALLY SERVED — new discriminator,
            # mirrors cost_basis/usage_source. "tier_capacity_degraded" mirrors the
            # guardrail_blocked-style boolean-flag convention: encoded as "true"/"false"
            # (Redis Stream fields are strings) — parsed back in flusher.py.
            "tier_served": tier_served,
            "tier_capacity_degraded": "true" if tier_capacity_degraded else "false",
        }

        # Push to Redis Stream — must not drop the event even on cost-0. Bounded by a
        # per-call timeout; on failure/timeout the event is persisted DIRECTLY to the
        # ledger (same id → ON CONFLICT) so a Redis blip never loses billing. A double
        # failure (Redis AND Postgres) propagates to record()'s outer swallow.
        # except Exception (NOT BaseException): CancelledError must propagate cleanly —
        # record() runs as a fire-and-forget task.
        xadd_ok = False
        try:
            async with asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS):
                await self._redis.xadd(STREAM_KEY, event_fields)
            xadd_ok = True
        except Exception as exc:
            _log.warning(
                "usage_recorder: XADD failed; persisting event directly to the ledger",
                exc_info=exc,
                extra={"event_id": str(event_id), "model": model},
            )
            await self._fallback_insert(event_id, event_fields)

        # Advisory spend counters — IEEE 754 float, ledger is source of truth. Best-effort:
        # only when the stream write succeeded (if XADD failed the counter write would too,
        # and the fallback row is already the durable truth; advisory reconciles from ledger).
        # BOUNDED (§1 B4-timeout: every Redis call in the record path is timeout-guarded) AND
        # best-effort — a timeout/failure here is logged and swallowed with NO durable fallback:
        # the ledger row already written by XADD/fallback is the source of truth, and the
        # advisory counters are reconstructable from it by the reconciliation job.
        if xadd_ok and cost_usd > _ZERO:
            yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
            # Tenant counter (existing) — usage:spend:{tenant_id}:{YYYYMM}
            spend_key = f"usage:spend:{tenant_id}:{yyyymm}"
            # Per-key counter (key-governance seam — M10/A2) — usage:spend:key:{key_id}:{YYYYMM}
            # Read by CompletionUseCase._check_per_key_budget() for most-specific-wins
            # enforcement. Soft-budget alerting also reads it (spend-windows/health-alerting).
            per_key_spend_key = f"usage:spend:key:{key_id}:{yyyymm}"
            try:
                async with asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS):
                    await self._redis.incrbyfloat(spend_key, float(cost_usd))
                    await self._redis.incrbyfloat(per_key_spend_key, float(cost_usd))
                    # Per-team counter (team-governance seam — fail-open same as per-key).
                    # usage:spend:team:{team_id}:{YYYYMM} — only when the key has a team
                    # attribution. Read by CompletionUseCase._check_team_budget().
                    if team_id is not None:
                        per_team_spend_key = f"usage:spend:team:{team_id}:{yyyymm}"
                        await self._redis.incrbyfloat(per_team_spend_key, float(cost_usd))
                    # Per-agent-principal counter (agent-identity-governance M4/CR-2 seam —
                    # fail-open same as per-key/per-team). usage:spend:agent_principal:
                    # {principal_id}:{YYYYMM} — only when the token is attached to a
                    # principal. Read by GovernanceService._check_agent_principal_budget()
                    # (enforcement) and GET /admin/agents (CR-2 read-side display).
                    if agent_principal_id is not None:
                        per_principal_spend_key = (
                            f"usage:spend:agent_principal:{agent_principal_id}:{yyyymm}"
                        )
                        await self._redis.incrbyfloat(per_principal_spend_key, float(cost_usd))
            except Exception as exc:
                _log.warning(
                    "usage_recorder: advisory spend increment failed "
                    "(best-effort; ledger is source of truth)",
                    exc_info=exc,
                    extra={"event_id": str(event_id), "model": model},
                )

        # credits-ledger TASK.md §3: event_id IS the deterministic usage_records PK (the
        # SAME id the flusher/fallback insert use) — safe to cite as usage_record_id here
        # even though the row lands a moment later via the async stream-flush path.
        # free=True (cost_usd <= 0) -> the settle consumer posts a RELEASE (M5), never a
        # SETTLE against a real cost.
        return UsageRecordOutcome(
            tenant_id=tenant_id,
            usage_record_id=event_id,
            cost_usd=cost_usd,
            free=cost_usd <= _ZERO,
        )

    async def _fallback_insert(self, event_id: uuid.UUID, fields: dict[str, str]) -> None:
        """Durable fallback: persist a usage event directly to the ledger when the
        Redis XADD fails/times out. Uses the SAME deterministic id the stream-flush
        would use, so ON CONFLICT (id) makes a later flush of the (landed) event a
        no-op — no double-bill. Propagates DB errors to record()'s outer swallow
        (a double store failure resolves to log-and-swallow, never a raise)."""
        from gateway.usage.application.flusher import insert_usage_row

        await insert_usage_row(self._session_factory, record_id=event_id, fields=fields)

    async def record_correction(
        self,
        *,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        cost_usd: Decimal,
        provider_cost: Decimal,
        provider_generation_id: str,
        usage_source: str = "openrouter_recovered",
        team_id: uuid.UUID | None = None,
        agent_principal_id: uuid.UUID | None = None,
    ) -> None:
        """Append a pre-priced SIGNED cost CORRECTION to the ledger (cost-recovery v30 t6).

        Unlike record(), this posts a cost_usd computed by the CALLER (a delta that may be
        NEGATIVE when a partial estimate over-shot the real cost) plus an EXPLICIT,
        deterministic event id so a duplicate recovery is an idempotent no-op at the
        flusher (ON CONFLICT (id) DO NOTHING). Always cost_basis='provider' with the
        authoritative provider_cost attached. Never raises — Redis unavailability is
        logged and swallowed, exactly like record().
        """
        try:
            created_at = datetime.datetime.now(datetime.UTC).isoformat()
            raw_payload: dict[str, object] = {
                "tenant_id": str(tenant_id),
                "key_id": str(key_id),
                "model": model,
                "usage": None,
                "status": 200,
                "correction": True,
                "provider_generation_id": provider_generation_id,
            }
            event_fields: dict[str, str] = {
                # Explicit id → the flusher uses it as the row PK (deterministic dedup).
                "id": str(event_id),
                "tenant_id": str(tenant_id),
                "key_id": str(key_id),
                "model_id": model,
                "prompt_tokens": "0",
                "completion_tokens": "0",
                "cost_usd": str(cost_usd),
                "pricing_snapshot_id": "",
                "status": "200",
                "raw": json.dumps(raw_payload),
                "created_at": created_at,
                "team_id": str(team_id) if team_id is not None else "",
                "pricing_unit": "per_token",
                "quantity": "",
                "cached_tokens": "0",
                "reasoning_tokens": "0",
                "cache_creation_tokens": "0",
                "cost_basis": "provider",
                "provider_cost": str(provider_cost),
                "usage_source": usage_source,
                "provider_generation_id": provider_generation_id,
                # agent-identity-governance defect fix: persist the correction row's
                # agent_principal_id attribution too (mirrors record()'s own "" encodes
                # NULL idiom above; team_id above was ALREADY wired — just never called
                # with a value, which the cost_recovery.py caller fix addresses) — a
                # correction is a real ledger row like any other and should carry the
                # SAME durable attribution as the anchor row it corrects.
                "agent_principal_id": (
                    str(agent_principal_id) if agent_principal_id is not None else ""
                ),
            }
            await self._redis.xadd(STREAM_KEY, event_fields)

            # Advisory spend counters move by the SIGNED delta (negative supported by
            # INCRBYFLOAT). Skip a zero delta — no balance change to record. The DB row
            # dedups via ON CONFLICT (id), but INCRBYFLOAT is NOT idempotent: a concurrent
            # inline+sweep double-fire of the SAME deterministic event_id would double-move
            # the per-key budget counter (which gates enforcement). SET NX keyed by the
            # event_id lets EXACTLY ONE caller apply the delta — exactly-once even across
            # replicas; the loser skips. (refute-driven hardening, v30 t6.2b.)
            if cost_usd != _ZERO:
                counted = await self._redis.set(
                    f"usage:correction:counted:{event_id}",
                    "1",
                    nx=True,
                    ex=_CORRECTION_COUNTED_TTL_S,
                )
                if counted:
                    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
                    await self._redis.incrbyfloat(
                        f"usage:spend:{tenant_id}:{yyyymm}", float(cost_usd)
                    )
                    await self._redis.incrbyfloat(
                        f"usage:spend:key:{key_id}:{yyyymm}", float(cost_usd)
                    )
                    if team_id is not None:
                        await self._redis.incrbyfloat(
                            f"usage:spend:team:{team_id}:{yyyymm}", float(cost_usd)
                        )
                    if agent_principal_id is not None:
                        await self._redis.incrbyfloat(
                            f"usage:spend:agent_principal:{agent_principal_id}:{yyyymm}",
                            float(cost_usd),
                        )
        except Exception as exc:
            _log.warning(
                "usage_recorder.record_correction failed (swallowed)",
                exc_info=exc,
                extra={
                    "tenant_id": str(tenant_id),
                    "model": model,
                    "provider_generation_id": provider_generation_id,
                },
            )


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
    cache_creation_tokens: int = 0,
    audio_prompt_tokens: int = 0,
    audio_completion_tokens: int = 0,
    audio_cached_tokens: int = 0,
    prompt_price: Decimal,
    completion_price: Decimal,
    cached_price: Decimal | None,
    reasoning_price: Decimal | None,
    cache_creation_price: Decimal | None = None,
    audio_prompt_price: Decimal | None = None,
    audio_completion_price: Decimal | None = None,
    audio_cached_price: Decimal | None = None,
    markup_pct: Decimal,
    model: str = "",
) -> Decimal:
    """Per-tier token cost (tiered-token-billing TASK.md §3 + prompt-cache-passthrough §3 +
    gpt-realtime-relay-billing TASK.md §3).

    When all tier counts are 0 the FLAT path runs the exact v6 expression verbatim
    (byte-identical). Otherwise the tiered expression splits fresh/cached/cache_creation/
    audio input and fresh/reasoning/audio output; a NULL tier price falls back to its base
    price; fresh counts clamp to max(0, …) so cost is never negative (logs "tier_token_clamped").

    cache_creation_tokens: Anthropic cache-write tokens (billed at cache_creation_price or
    prompt_price when cache_creation_price is NULL — no regression).

    audio_prompt_tokens / audio_completion_tokens: GPT-Realtime dual-stream audio counts.
    Per OpenAI's Realtime `usage.input_token_details`/`output_token_details` shape, these are
    a BREAKDOWN (subset) of prompt_tokens/completion_tokens, not additional to them — so they
    are subtracted out of fresh_in/fresh_out (mirroring how cached_tokens is already a subset
    of prompt_tokens) and billed separately at the audio rate, never double-counted at both the
    text rate and the audio rate. audio_cached_tokens is in turn a subset of audio_prompt_tokens
    (the cheapest of the three input tiers), billed at audio_cached_price.
    """
    markup = Decimal("1") + Decimal(str(markup_pct)) / Decimal("100")
    if (
        cached_tokens == 0
        and reasoning_tokens == 0
        and cache_creation_tokens == 0
        and audio_prompt_tokens == 0
        and audio_completion_tokens == 0
        and audio_cached_tokens == 0
    ):
        # FLAT PATH — verbatim v6 operand order; byte-identical to the pre-tier code.
        return (
            Decimal(str(prompt_tokens)) * Decimal(str(prompt_price))
            + Decimal(str(completion_tokens)) * Decimal(str(completion_price))
        ) * markup

    # TIERED PATH
    fresh_in = prompt_tokens - cached_tokens - cache_creation_tokens - audio_prompt_tokens
    fresh_out = completion_tokens - reasoning_tokens - audio_completion_tokens
    fresh_audio_in = audio_prompt_tokens - audio_cached_tokens
    if fresh_in < 0 or fresh_out < 0 or fresh_audio_in < 0:
        _log.warning(
            "tier_token_clamped",
            extra={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "cached_tokens": cached_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "audio_prompt_tokens": audio_prompt_tokens,
                "audio_completion_tokens": audio_completion_tokens,
                "audio_cached_tokens": audio_cached_tokens,
            },
        )
    fresh_in = max(0, fresh_in)
    fresh_out = max(0, fresh_out)
    fresh_audio_in = max(0, fresh_audio_in)
    cprice = Decimal(str(cached_price)) if cached_price is not None else Decimal(str(prompt_price))
    rprice = (
        Decimal(str(reasoning_price))
        if reasoning_price is not None
        else Decimal(str(completion_price))
    )
    # cache_creation_price NULL → fall back to prompt_price (no billing regression).
    ccprice = (
        Decimal(str(cache_creation_price))
        if cache_creation_price is not None
        else Decimal(str(prompt_price))
    )
    # A NULL audio price with a non-zero audio count treats that tier's rate as 0 (never
    # raises, never silently double-counts at the text rate) — logged for visibility.
    if audio_prompt_price is None and (audio_prompt_tokens > 0 or audio_cached_tokens > 0):
        _log.warning("audio_tier_price_missing", extra={"model": model, "tier": "audio_prompt"})
    if audio_completion_price is None and audio_completion_tokens > 0:
        _log.warning("audio_tier_price_missing", extra={"model": model, "tier": "audio_completion"})
    a_prompt_price = Decimal(str(audio_prompt_price)) if audio_prompt_price is not None else _ZERO
    a_completion_price = (
        Decimal(str(audio_completion_price)) if audio_completion_price is not None else _ZERO
    )
    # audio_cached_price NULL → fall back to audio_prompt_price (mirrors cached_price's
    # fallback to prompt_price); if BOTH are NULL, falls through to 0 (logged above).
    a_cached_price = (
        Decimal(str(audio_cached_price)) if audio_cached_price is not None else a_prompt_price
    )
    return (
        Decimal(str(fresh_in)) * Decimal(str(prompt_price))
        + Decimal(str(cached_tokens)) * cprice
        + Decimal(str(cache_creation_tokens)) * ccprice
        + Decimal(str(fresh_out)) * Decimal(str(completion_price))
        + Decimal(str(reasoning_tokens)) * rprice
        + Decimal(str(fresh_audio_in)) * a_prompt_price
        + Decimal(str(audio_cached_tokens)) * a_cached_price
        + Decimal(str(audio_completion_tokens)) * a_completion_price
    ) * markup


async def _fetch_latest_pricing(
    session: AsyncSession,
    model_id: str,
) -> (
    tuple[
        uuid.UUID,
        Decimal,
        Decimal,
        str,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
    ]
    | None
):
    """Return (snapshot_id, prompt_price, completion_price, pricing_unit,
    unit_usd_per_unit, cached_input_price, reasoning_price, cache_creation_price,
    audio_prompt_price, audio_completion_price, audio_cached_price).

    Returns None if no pricing snapshot found for the model.

    Additive extensions:
      pricing_unit           — discriminator; defaults to 'per_token' for old rows. (pricing-units)
      unit_usd_per_unit      — per-unit price for non-token rows; NULL otherwise. (pricing-units)
      cached_input_price / reasoning_price — per-tier prices; NULL → base-price
                              fallback. (tiered-token-billing)
      cache_creation_price   — Anthropic ~1.25x write premium; NULL -> prompt-rate
                              fallback (no regression). (prompt-cache-passthrough)
      audio_prompt_price / audio_completion_price / audio_cached_price — GPT-Realtime
                              dual-stream audio rates; NULL for every non-realtime model.
                              (gpt-realtime-relay-billing)
    """
    row = (
        await session.execute(
            text(
                "SELECT id, prompt_usd_per_token, completion_usd_per_token,"
                " pricing_unit, unit_usd_per_unit,"
                " cached_input_usd_per_token, reasoning_usd_per_token,"
                " cache_creation_usd_per_token,"
                " audio_prompt_usd_per_token, audio_completion_usd_per_token,"
                " audio_cached_usd_per_token"
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
    cache_creation_price: Decimal | None = Decimal(str(row[7])) if row[7] is not None else None
    audio_prompt_price: Decimal | None = Decimal(str(row[8])) if row[8] is not None else None
    audio_completion_price: Decimal | None = Decimal(str(row[9])) if row[9] is not None else None
    audio_cached_price: Decimal | None = Decimal(str(row[10])) if row[10] is not None else None
    return (
        uuid.UUID(str(row[0])),
        Decimal(str(row[1])),
        Decimal(str(row[2])),
        str(row[3]) if row[3] is not None else "per_token",
        unit_usd,
        cached_price,
        reasoning_price,
        cache_creation_price,
        audio_prompt_price,
        audio_completion_price,
        audio_cached_price,
    )


async def _fetch_markup_pct(session: AsyncSession, tenant_id: uuid.UUID, model_id: str) -> Decimal:
    """Return the effective per-(tenant, model) markup_pct (tiered-rate-cards).

    Delegates to the shared resolver — the SAME rate every call site resolves
    (recorder billing, cost_recovery disconnect, catalog display). A per-model
    rate-card override wins; otherwise falls back to the tenant's flat
    markup_pct (0 if the tenant row is absent — unchanged pre-existing
    behavior).
    """
    return await resolve_markup_pct(session, tenant_id, model_id)


async def _fetch_region_multiplier(
    session: AsyncSession, tenant_id: uuid.UUID, model_id: str
) -> Decimal:
    """Return the effective per-(tenant, model) region multiplier (region-pricing).

    Delegates to the shared resolver — the SAME rate every call site resolves
    (recorder billing, cost_recovery disconnect, catalog display). A
    per-(tenant, region) override wins; otherwise falls back to the DECIDED
    seed keyed by the model's region (1.0 for an unrecognized/NULL region —
    pricing never blocks, only residency policy fail-closes).
    """
    return await resolve_region_multiplier(session, tenant_id, model_id)
