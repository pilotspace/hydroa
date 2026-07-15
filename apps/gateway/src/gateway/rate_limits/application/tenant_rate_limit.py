"""enforce_tenant_rate_limit — the ONE shared tenant-layer plan rpm/tpm enforcement both
CompletionUseCase._enforce_rate_limits (chat) and NonChatGovernance.authorize (non-chat)
call — mirrors gateway.proxy.application.residency.check_residency_existence's own
dual-copy-governance "ONE shared implementation function, never staggered" precedent
(plan-rate-enforcement TASK.md §3, FROZEN @ v1, M3/M4).
"""

from __future__ import annotations

import contextvars
import uuid

from gateway.core.error_catalog import RATE_LIMITED
from gateway.rate_limits.domain.errors import RateLimitExceededError
from gateway.rate_limits.domain.ports import RateLimiter
from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import PlanRateLimitResolver

# Published by enforce_tenant_rate_limit() ONLY when this request's resolved tenant TPM
# ceiling is active (M4) — consumed by CompletionUseCase's tenant-window sibling of
# _fire_record_tpm so post-response accounting records into the SAME window that was
# just admission-checked. None (default; every unplanned/uncapped tenant, and every
# non-chat request — which never fires record_tpm at all even for the per-key window,
# a pre-existing gap this task does not widen) ⇒ no tenant record ever fires ⇒
# completely inert. asyncio copies the contextvar context at task-creation time (the
# same guarantee use_cases.py's own _credit_hold_ctx docstring documents), so concurrent
# requests (each its own top-level Task) never cross-leak.
tenant_tpm_ctx: contextvars.ContextVar[tuple[uuid.UUID, int] | None] = contextvars.ContextVar(
    "tenant_tpm_ctx", default=None
)


async def enforce_tenant_rate_limit(
    rate_limiter: RateLimiter | None,
    resolver: PlanRateLimitResolver | None,
    tenant_id: uuid.UUID,
) -> None:
    """Check the tenant-scoped RPM/TPM window ALONGSIDE the per-key window (M3).

    Composes, never replaces: call this AFTER the existing per-key RPM/TPM check at
    both enforce seams. Either window firing -> 429 ERR_RATE_LIMITED + Retry-After,
    identical error shape to the per-key rejection.

    No-op (byte-identical to pre-task behavior) when resolver or rate_limiter is None —
    mirrors this codebase's own None-default optional-port convention (residency_lookup,
    credit_guard, tier_capacity_guard, etc).

    Fail-open (M5): resolver.resolve() never raises (a DB error resolves to
    ResolvedRate(None, None)) — this function adds no extra try/except around resolve()
    by design. A None dimension is simply never checked: an unplanned/uncapped tenant
    stays completely inert (no Redis round-trip beyond the one resolver SELECT).
    """
    if resolver is None or rate_limiter is None:
        return
    resolved = await resolver.resolve(tenant_id)
    if resolved.tpm is not None:
        # Published BEFORE the RPM/TPM checks below (not after) so a request that gets
        # 429'd on the RPM leg still leaves nothing dangling — record_tpm is only ever
        # consumed by the SUCCESSFUL post-response accounting call sites, which a 429'd
        # request never reaches (no usage, no post-response fire) — so the ordering here
        # is harmless either way; publishing first keeps the function's control flow flat.
        tenant_tpm_ctx.set((tenant_id, resolved.tpm))
    if resolved.rpm is not None:
        try:
            await rate_limiter.check_rpm(tenant_id, resolved.rpm)
        except RateLimitExceededError as exc:
            raise RATE_LIMITED.exc(
                detail=f"tenant RPM limit {exc.limit} exceeded for tenant {tenant_id}",
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from None
    if resolved.tpm is not None:
        try:
            await rate_limiter.check_tpm(tenant_id, resolved.tpm)
        except RateLimitExceededError as exc:
            raise RATE_LIMITED.exc(
                detail=f"tenant TPM limit {exc.limit} exceeded for tenant {tenant_id}",
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from None


__all__ = ["enforce_tenant_rate_limit", "tenant_tpm_ctx"]
