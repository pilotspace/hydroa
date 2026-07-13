"""OpenRouterCostRecoveryService — recover the authoritative upstream cost for a
client-disconnect generation and bill the SIGNED top-up delta (v30 t6.2b).

When a stream is aborted by client disconnect the terminal usage frame never arrives,
so the ledger row bills only a partial estimate (or $0). t6.2 stamped the provider's
generation id on that row; THIS service rides that rail out of band:

  1. wait (bounded) for the anchor disconnect row to be FLUSHED — so the partial floor
     is counted, never raced;
  2. fetch OpenRouter's authoritative cost (poll until it settles or deadline);
  3. append ONE correction row whose signed cost_usd brings the total billed for the
     generation up to ``total_cost * (1 + markup)`` — the partial-floor + delta-top-up
     model (chosen by Tin 2026-06-22).

Idempotent at the DB: the correction row id is deterministic from the gid (uuid5), and
the flusher honours that explicit id, so a duplicate recovery (inline wiring racing the
periodic sweep) collapses via ON CONFLICT (id) DO NOTHING. Never raises into the caller
— recovery is best-effort; the sweep is the backstop.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.infrastructure.openrouter_upstream import GenerationCost
from gateway.usage.application.rate_card_resolver import (
    resolve_markup_pct,
    resolve_region_multiplier,
    resolve_tier_multiplier,
)
from gateway.usage.application.recorder import RecordingUsageRecorder

_log = logging.getLogger(__name__)

_ZERO = Decimal("0")
_RECOVERED = "openrouter_recovered"
# Stable namespace so a gid maps to the SAME correction-row id across replicas/retries.
_RECOVERY_NS = uuid.UUID("6f9a1d2c-3b4e-5a6f-8c7d-0e1f2a3b4c5d")


def recovery_event_id(provider_generation_id: str) -> uuid.UUID:
    """Deterministic correction-row id for a generation (the idempotency key)."""
    return uuid.uuid5(_RECOVERY_NS, f"{_RECOVERED}:{provider_generation_id}")


@dataclass(frozen=True)
class RecoveryOutcome:
    """The result of a recover() call (never raised — always returned)."""

    status: str
    delta_usd: Decimal | None = None
    provider_cost: Decimal | None = None


@dataclass(frozen=True)
class _LedgerState:
    anchor_exists: bool
    already_billed: Decimal
    recovered_exists: bool
    # service-tiers TASK.md §3: the ORIGINAL anchor row's stored tier_served — read
    # from usage_records, NEVER re-derived from live admission (§0 issue #5). "standard"
    # when no anchor row exists (anchor_exists=False; the safe default).
    tier_served: str = "standard"


class _GenerationCostSource(Protocol):
    async def get_generation(self, generation_id: str) -> GenerationCost | None: ...


class _CredentialResolver(Protocol):
    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> Any: ...


class OpenRouterCostRecoveryService:
    """Out-of-band authoritative-cost recovery for disconnected OpenRouter streams.

    All time budgets are bounded (design-for-failure). ``sleep`` and ``monotonic`` are
    injectable so tests drive the deadline loops deterministically with no real waits.
    """

    def __init__(
        self,
        *,
        upstream: _GenerationCostSource,
        recorder: RecordingUsageRecorder,
        session_factory: async_sessionmaker[AsyncSession],
        credential_resolver: _CredentialResolver | None = None,
        ready_deadline_s: float = 30.0,
        ready_poll_interval_s: float = 2.0,
        anchor_wait_s: float = 5.0,
        anchor_poll_interval_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._upstream = upstream
        self._recorder = recorder
        self._session_factory = session_factory
        self._credential_resolver = credential_resolver
        self._ready_deadline_s = ready_deadline_s
        self._ready_poll_interval_s = ready_poll_interval_s
        self._anchor_wait_s = anchor_wait_s
        self._anchor_poll_interval_s = anchor_poll_interval_s
        self._sleep = sleep
        self._monotonic = monotonic

    async def recover(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        provider_generation_id: str,
    ) -> RecoveryOutcome:
        """Recover the authoritative cost for a generation and bill the signed delta.

        Never raises — every failure resolves to a status string the caller can log.
        """
        gid = provider_generation_id
        if not gid:
            return RecoveryOutcome("skipped:no_generation_id")
        try:
            # 1. Wait (bounded) for the anchor disconnect row to flush so its partial
            #    floor is counted; short-circuit if a recovered row already exists.
            deadline = self._monotonic() + self._anchor_wait_s
            while True:
                state = await self._ledger_state(tenant_id, gid)
                if state.recovered_exists:
                    return RecoveryOutcome("skipped:already_recovered")
                if state.anchor_exists:
                    break
                if self._monotonic() >= deadline:
                    return RecoveryOutcome("deferred:anchor_not_flushed")
                await self._sleep(self._anchor_poll_interval_s)

            # 2. Resolve the tenant credential out of band (no request context) and poll
            #    the generation endpoint until the cost settles or the deadline elapses.
            cred_token = None
            if self._credential_resolver is not None:
                cred = await self._credential_resolver.resolve(tenant_id, "openrouter")
                if cred is None:
                    return RecoveryOutcome("deferred:no_credential")
                cred_token = set_provider_credential(cred)
            try:
                cost = await self._poll_generation(gid)
            finally:
                if cred_token is not None:
                    reset_provider_credential(cred_token)
            if cost is None:
                return RecoveryOutcome("deferred:not_settled")

            # 3. Recompute already_billed (fresh) + re-check dedup, then bill the delta.
            state = await self._ledger_state(tenant_id, gid)
            if state.recovered_exists:
                return RecoveryOutcome("skipped:already_recovered")
            markup = await self._fetch_markup(tenant_id, model)
            region_multiplier = await self._fetch_region_multiplier(tenant_id, model)
            # service-tiers TASK.md §3: keyed by the ORIGINAL anchor row's stored
            # tier_served (state.tier_served), never re-derived from live admission.
            tier_multiplier = await self._fetch_tier_multiplier(tenant_id, model, state.tier_served)
            target = (
                cost.total_cost
                * (Decimal("1") + markup / Decimal("100"))
                * region_multiplier
                * tier_multiplier
            )
            delta = target - state.already_billed
            await self._recorder.record_correction(
                event_id=recovery_event_id(gid),
                tenant_id=tenant_id,
                key_id=key_id,
                model=model,
                cost_usd=delta,
                provider_cost=cost.total_cost,
                provider_generation_id=gid,
            )
            return RecoveryOutcome("recovered", delta_usd=delta, provider_cost=cost.total_cost)
        except Exception as exc:
            _log.warning(
                "openrouter_cost_recovery failed (swallowed)",
                exc_info=exc,
                extra={"tenant_id": str(tenant_id), "provider_generation_id": gid},
            )
            return RecoveryOutcome("error")

    async def _poll_generation(self, gid: str) -> GenerationCost | None:
        """Poll get_generation until it returns a settled cost or the deadline passes."""
        deadline = self._monotonic() + self._ready_deadline_s
        while True:
            cost = await self._upstream.get_generation(gid)
            if cost is not None:
                return cost
            if self._monotonic() >= deadline:
                return None
            await self._sleep(self._ready_poll_interval_s)

    async def _ledger_state(self, tenant_id: uuid.UUID, gid: str) -> _LedgerState:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT"
                        " COUNT(*) FILTER (WHERE usage_source <> :rec),"
                        " COALESCE(SUM(cost_usd) FILTER (WHERE usage_source <> :rec), 0),"
                        " COUNT(*) FILTER (WHERE usage_source = :rec),"
                        " MIN(tier_served) FILTER (WHERE usage_source <> :rec)"
                        " FROM usage_records"
                        " WHERE tenant_id = :t AND provider_generation_id = :g"
                    ),
                    {"t": str(tenant_id), "g": gid, "rec": _RECOVERED},
                )
            ).fetchone()
        if row is None:
            return _LedgerState(anchor_exists=False, already_billed=_ZERO, recovered_exists=False)
        return _LedgerState(
            anchor_exists=int(row[0]) > 0,
            already_billed=Decimal(str(row[1])),
            recovered_exists=int(row[2]) > 0,
            tier_served=str(row[3]) if row[3] is not None else "standard",
        )

    async def _fetch_markup(self, tenant_id: uuid.UUID, model: str) -> Decimal:
        """Return the effective per-(tenant, model) markup_pct (tiered-rate-cards).

        Delegates to the shared resolver — the SAME rate recorder billing and
        catalog display resolve (no third-site drift; TASK.md §3).
        """
        async with self._session_factory() as session:
            return await resolve_markup_pct(session, tenant_id, model)

    async def _fetch_region_multiplier(self, tenant_id: uuid.UUID, model: str) -> Decimal:
        """Return the effective per-(tenant, model) region multiplier (region-pricing).

        Delegates to the shared resolver — the SAME rate recorder billing and
        catalog display resolve (no third-site drift; TASK.md §3 M4).
        """
        async with self._session_factory() as session:
            return await resolve_region_multiplier(session, tenant_id, model)

    async def _fetch_tier_multiplier(
        self, tenant_id: uuid.UUID, model: str, tier_served: str
    ) -> Decimal:
        """Return the effective priority-tier markup multiplier (service-tiers).

        Delegates to the shared resolver — the SAME rate recorder billing and
        catalog display resolve (no third-site drift; TASK.md §3 M11). Keyed by the
        ORIGINAL anchor row's stored tier_served, never re-derived from live admission.
        """
        async with self._session_factory() as session:
            return await resolve_tier_multiplier(session, tenant_id, model, tier_served)
