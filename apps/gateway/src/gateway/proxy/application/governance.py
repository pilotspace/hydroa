"""NonChatGovernance — reusable governance gate for non-chat modalities.

Standalone re-implementation of the nine ordered governance steps used by
CompletionUseCase._enforce_governance. The chat path (use_cases.py) is NEVER
modified. images-endpoint and audio-endpoints reuse this class unchanged.

Contract FROZEN @ embeddings-endpoint (TASK.md §3).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    BUDGET_EXCEEDED,
    MODEL_DISABLED,
    MODEL_NOT_ALLOWED,
    MODEL_UNKNOWN,
    PLAN_MODEL_NOT_ALLOWED,
    RATE_LIMITED,
)
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.proxy.domain.ports import KeyAuthenticator, ModelAccess, ModelChecker
from gateway.rate_limits.domain.errors import RateLimitExceededError
from gateway.rate_limits.domain.ports import RateLimiter

_log = logging.getLogger(__name__)
_ZERO = Decimal("0")


def _parse_spend(raw: bytes | str | None) -> Decimal:
    """Parse Redis spend counter value; returns 0 on any failure (fail-open)."""
    if raw is None:
        return _ZERO
    try:
        return Decimal(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (InvalidOperation, AttributeError):
        return _ZERO


class NonChatGovernance:
    """Reusable governance gate for non-chat modalities.

    images-endpoint and audio-endpoints construct and call this class with
    the same interface. The chat path (CompletionUseCase) is NEVER modified.

    Nine ordered checks (same order as CompletionUseCase._enforce_governance):
      auth → expiry → allowlist → catalog → per-key-budget → team-budget →
      tenant-budget → RPM → TPM
    """

    def __init__(
        self,
        *,
        authenticator: KeyAuthenticator,
        model_checker: ModelChecker,
        budget_guard: Any,
        rate_limiter: RateLimiter | None,
        redis_client: Any,
        session_factory: Any = None,
    ) -> None:
        self._authenticator = authenticator
        self._model_checker = model_checker
        self._budget_guard = budget_guard
        self._rate_limiter = rate_limiter
        self._redis_client = redis_client
        # Optional app-scoped async_sessionmaker for the advisory soft-budget alert
        # (fire-and-forget write to alert_events). None → alert disabled (back-compat).
        self._session_factory = session_factory

    async def authorize(
        self,
        raw_key: str | None,
        model_id: str,
        *,
        estimated_tokens: int | None = None,
    ) -> AuthzResult:
        """Run the nine governance checks in order; return AuthzResult on pass.

        Ordered checks (same order as CompletionUseCase._enforce_governance):
          Step 1: Authenticate key → AUTH_KEY_INVALID  (401)
          Step 2: Check key expiry → AUTH_KEY_EXPIRED  (401)
          Step 3: Check model allowlist → MODEL_NOT_ALLOWED (403)
          Step 4: Check model catalog → MODEL_UNKNOWN (400) / MODEL_DISABLED (403)
          Step 5: Check per-key budget → BUDGET_EXCEEDED (402) — fail-open on Redis error
          Step 6: Check team budget → BUDGET_EXCEEDED (402) — fail-open on Redis error
          Step 7: Check tenant budget → BUDGET_EXCEEDED (402)
          Step 8: Check RPM → RATE_LIMITED (429) + Retry-After — skipped when rate_limiter None
          Step 9: Check TPM → RATE_LIMITED (429) + Retry-After — skipped when estimated_tokens None

        Returns: AuthzResult on success (all checks passed).
        """
        # Step 1: Authenticate
        if not raw_key:
            raise AUTH_KEY_INVALID.exc()
        try:
            authz = await self._authenticator.authenticate(raw_key)
        except InvalidApiKeyError:
            raise AUTH_KEY_INVALID.exc() from None

        # Step 2: Expiry check
        self._check_expiry(authz)

        # Step 3: Model allowlist check (MUST run before catalog check)
        self._check_model_allowlist(authz, model_id)

        # plan-enforcement (M4): plan-level model-allowlist check — composes by
        # INTERSECTION with the key-level allowlist just above; runs immediately after it,
        # BEFORE the catalog check (mirrors use_cases.py's own insertion point exactly —
        # dual-copy governance, never staggered).
        self._check_plan_model_allowlist(authz, model_id)

        # Step 4: Catalog active + per-tenant override check
        await self._check_model_catalog(model_id, authz.tenant_id)

        # Steps 5-7: Budget enforcement - most-specific-wins.
        # When monthly_budget_usd is set: per-key hard 402 wins; team cap still checked;
        # tenant-budget check is SKIPPED (most-specific-wins means per-key is authoritative).
        # When no hard per-key budget: team budget checked, then tenant budget enforces.
        if authz.monthly_budget_usd is not None:
            # Step 5: Per-key hard budget (fail-open on Redis error)
            await self._check_per_key_budget(authz)
            # Step 6: Team budget — key under its own cap can still be stopped by team cap
            await self._check_team_budget(authz)
            # Step 7: tenant budget SKIPPED — per-key wins (most-specific-wins)
        else:
            # Step 5: No hard per-key budget — but the soft-alert seam still runs
            # (mirror use_cases.py:616-618; budget None → no per-key 402 possible).
            if authz.soft_budget_usd is not None:
                await self._check_per_key_budget(authz)
            # Step 6: Team budget check — fail-open on Redis errors
            await self._check_team_budget(authz)
            # Step 7: Tenant budget enforces (RedisBudgetGuard)
            await self._budget_guard.check(authz.tenant_id)

        # Step 8: RPM check — skip when rate_limiter is None OR rpm_limit is None
        if self._rate_limiter is not None and authz.rpm_limit is not None:
            try:
                await self._rate_limiter.check_rpm(authz.key_id, authz.rpm_limit)
            except RateLimitExceededError as exc:
                raise RATE_LIMITED.exc(
                    detail=f"RPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

        # Step 9: TPM pre-flight — skip when estimated_tokens/rate_limiter/tpm_limit is None
        if (
            estimated_tokens is not None
            and self._rate_limiter is not None
            and authz.tpm_limit is not None
        ):
            try:
                await self._rate_limiter.check_tpm(authz.key_id, authz.tpm_limit)
            except RateLimitExceededError as exc:
                raise RATE_LIMITED.exc(
                    detail=f"TPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

        return authz

    # ---------------------------------------------------------------------------
    # Private governance helpers (mirrors use_cases.py module-level functions)
    # ---------------------------------------------------------------------------

    def _check_expiry(self, authz: AuthzResult) -> None:
        """Raise AUTH_KEY_EXPIRED if the key has expired."""
        if authz.expires_at is None:
            return
        now_utc = datetime.datetime.now(datetime.UTC)
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.UTC)
        if exp <= now_utc:
            raise AUTH_KEY_EXPIRED.exc()

    def _check_model_allowlist(self, authz: AuthzResult, model_id: str) -> None:
        """Raise MODEL_NOT_ALLOWED if model is not in key allowlist.

        null allowlist = all models allowed.
        empty [] = no models allowed (security-strict).
        """
        if authz.model_allowlist is None:
            return
        if model_id not in authz.model_allowlist:
            raise MODEL_NOT_ALLOWED.exc()

    def _check_plan_model_allowlist(self, authz: AuthzResult, model_id: str) -> None:
        """Raise ERR_PLAN_MODEL_NOT_ALLOWED (plan-enforcement TASK.md §3, M4, M9) if the
        model is outside the tenant's ASSIGNED PLAN's model_allowlist.

        Composes by INTERSECTION with _check_model_allowlist above — a no-op (M7
        grandfathered) when the tenant has no assigned plan; a no-op when the plan itself
        imposes no restriction (null allowlist).
        """
        if authz.plan_id is None:
            return  # M7 — unplanned, grandfathered-unlimited
        if authz.plan_model_allowlist is None:
            return  # plan imposes no restriction
        if model_id not in authz.plan_model_allowlist:
            raise PLAN_MODEL_NOT_ALLOWED.exc(
                extra={
                    "upgrade_hint": {
                        "plan_id": str(authz.plan_id),
                        "plan_name": authz.plan_name,
                        "model": model_id,
                    }
                }
            )

    async def _check_model_catalog(self, model_id: str, tenant_id: uuid.UUID) -> None:
        """Check catalog active state and per-tenant override (tri-state ModelAccess)."""
        if hasattr(self._model_checker, "check_for_tenant"):
            access = await self._model_checker.check_for_tenant(model_id, tenant_id)
            if access is ModelAccess.UNKNOWN:
                raise MODEL_UNKNOWN.exc(model_id=model_id)
            if access is ModelAccess.TENANT_DISABLED:
                raise MODEL_DISABLED.exc()
            # ModelAccess.ACTIVE — proceed
        else:
            is_active = await self._model_checker.is_active(model_id)
            if not is_active:
                raise MODEL_UNKNOWN.exc(model_id=model_id)

    async def _check_per_key_budget(self, authz: AuthzResult) -> None:
        """Check per-key Redis spend counter against key's monthly_budget_usd.

        Also fires the advisory soft-budget alert (mirrors the chat path,
        use_cases.py:_check_per_key_budget) when soft_budget_usd is crossed.
        Fail-open: Redis unavailable → allow (advisory counter pattern).
        Counter key: usage:spend:key:{key_id}:{YYYYMM}
        """
        budget = authz.monthly_budget_usd
        # Return early only if neither hard nor soft budget needs a Redis read.
        if budget is None and authz.soft_budget_usd is None:
            return

        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:key:{authz.key_id}:{yyyymm}"

        try:
            redis = self._redis_client
            if redis is None:
                return  # No Redis wired — fail open
            raw = await redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "per_key_budget_check: Redis GET failed (fail open)",
                exc_info=exc,
                extra={"key_id": str(authz.key_id), "spend_key": spend_key},
            )
            return

        spent = _parse_spend(raw)

        # Soft budget seam: fire-and-forget alert event when crossing detected
        # (chat-identical). Scheduled here (pre-flight), never awaited — hot-path
        # latency unaffected. ON CONFLICT (dedupe_key) DO NOTHING → idempotent.
        if (
            authz.soft_budget_usd is not None
            and spent >= authz.soft_budget_usd
            and self._session_factory is not None
        ):
            from gateway.usage.application.alert_writer import (
                persist_soft_budget_alert,
            )

            _task = asyncio.ensure_future(
                persist_soft_budget_alert(
                    self._session_factory,
                    authz.tenant_id,
                    authz.key_id,
                    authz.soft_budget_usd,
                    spent,
                )
            )
            # Keep reference to prevent GC; swallow exceptions in callback.
            _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        # Hard budget enforcement — only when monthly_budget_usd is set.
        if budget is not None and spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=f"Per-key spend {spent} >= budget {budget} for key {authz.key_id}"
            )

    async def _check_team_budget(self, authz: AuthzResult) -> None:
        """Check per-team Redis spend counter against team's team_budget_usd.

        Fail-open: Redis unavailable → allow.
        Counter key: usage:spend:team:{team_id}:{YYYYMM}
        """
        budget = authz.team_budget_usd
        team_id = authz.team_id
        if budget is None or team_id is None:
            return

        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:team:{team_id}:{yyyymm}"

        try:
            redis = self._redis_client
            if redis is None:
                return  # No Redis wired — fail open
            raw = await redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "team_budget_check: Redis GET failed (fail open)",
                exc_info=exc,
                extra={"team_id": str(team_id), "spend_key": spend_key},
            )
            return

        spent = _parse_spend(raw)

        if spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=f"Team spend {spent} >= budget {budget} for team {team_id}"
            )
