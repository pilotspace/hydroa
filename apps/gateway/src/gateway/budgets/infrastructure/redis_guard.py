"""RedisBudgetGuard — infrastructure adapter for the BudgetGuard port.

Reads budget_usd_monthly from Postgres (one SELECT per request, MVP),
reads the advisory spend counter from Redis, and enforces the ceiling.

Fail-open semantics (availability over enforcement):
  - Redis unavailable → log warning + allow
  - DB unavailable → log warning + allow
  - NULL budget → allow (unlimited)
  - Missing counter → treat as Decimal("0.00") → allow

audit-remediation C1 fix (2026-07-14) — atomic check-and-reserve:
  The original `check()` was an UNLOCKED fetch-then-compare: read
  `usage:spend:{tenant}:{yyyymm}` (Redis GET), read `budget_usd_monthly`
  (Postgres SELECT), compare in Python. usage/application/recorder.py only
  increments that SAME Redis counter well AFTER a request completes, with its
  REAL cost — so N concurrent requests admitted in the SAME instant all read the
  identical stale counter value, all pass, and only much later (post-completion)
  does the counter catch up — by which point all N have already been admitted,
  letting cumulative spend overshoot the budget by up to N x per-request cost
  (MED audit finding C1). A single Redis GET is itself atomic; the RACE is
  across separate requests/connections, which a bare GET can never close.

  Fix: `_check_and_reserve` mirrors rate_limits/infrastructure/
  redis_lua_limiter.py's own "evict expired + compute + conditional record, all
  in ONE Lua round-trip" idiom. Every ADMITTED check() reserves a small
  conservative placeholder (`hold_estimate_usd`, default $0.50 — same default as
  credits_hold_estimate_usd, core/config.py) against the budget in a short-TTL
  Redis ZSET (+ companion float sum key), for `hold_ttl_seconds` (default 120s).
  Enforcement becomes `committed_spend + sum(active holds) >= budget`, evaluated
  and updated atomically by Redis's single-threaded Lua execution — a second
  concurrent call always observes the first call's reservation, closing the
  race a bare GET-then-compare cannot.

  No explicit release: `check()`'s contract is check-only (unchanged) and its
  callers (proxy/application/use_cases.py, governance.py — both OUT of this
  package's edit scope) have no settle/release hook analogous to CreditGuard's.
  The hold's own TTL is the passive backstop instead — mirrors
  proxy/infrastructure/tier_capacity_guard.py's own documented "the hold's own
  TTL is the passive backstop" precedent for the identical reason (no release
  call site available). This means a completed request's cost can be
  double-counted (real `usage:spend` increment + its own still-active hold) for
  up to `hold_ttl_seconds` after it completes — a bounded, OVER-conservative
  window (may cause a spurious 402 near the ceiling) that can never UNDER-count
  (never lets spend silently exceed budget), which is the correct bias for a
  hard budget.

  Deliberately UNCHANGED: total-Redis-outage still fails OPEN (any exception
  raised by `_check_and_reserve`, including a fake/stub redis client lacking
  `register_script`, propagates up through `_check_internal` into `check()`'s
  existing outer try/except and is swallowed exactly as before). This repo has
  an explicit, currently-passing, NAMED contract test for that behavior —
  tests/budgets/test_budgets.py::test_redis_down_allows_completion, "§ Scenario
  5 — Redis unavailable allows completion (availability-over-enforcement)" — a
  deliberate product tradeoff (uptime over strict enforcement), not a bug, and
  flipping it would ripple into ~10 OTHER test suites across the codebase that
  wire RedisBudgetGuard directly (agent_oauth_e2e, key_governance, mcp_connector,
  obs_callbacks, rate_limits, plan_enforcement, spend_windows,
  agent_token_authn_seam, embeddings/images endpoints, credits_ledger,
  anthropic_messages_ingress), none of which are in this task's package. C1's
  finding is specifically the TOCTOU race, not the fail-open-on-total-outage
  policy — see this task's report for the full tradeoff written out.

  RESOLVED (Tin, 2026-07-15): keep fail-open on total Redis outage —
  availability over enforcement, consistent with the sibling modality guard's
  own fail-open call. This is a deliberate product decision, NOT a security
  boundary (the PII-mask and guardrail-block paths are separately fail-CLOSED).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.error_catalog import BUDGET_EXCEEDED
from gateway.core.errors import ProblemError
from gateway.tenants.domain.entitlements import resolve_entitlements

_log = logging.getLogger(__name__)

_ZERO = Decimal("0")
_DEFAULT_HOLD_ESTIMATE_USD = Decimal("0.50")
_DEFAULT_HOLD_TTL_SECONDS = 120

# ── Atomic check-and-reserve Lua script (mirrors redis_lua_limiter.py's TPM
# check+sum idiom: a ZSET of "{amount}:{uuid}" members scored by insertion time,
# a companion float sum key, both self-expiring) ─────────────────────────────
#
# KEYS[1] = budget:hold:{tenant_id}:{yyyymm}       (ZSET)
# KEYS[2] = budget:hold_sum:{tenant_id}:{yyyymm}    (float sum)
# ARGV[1] = now_ms
# ARGV[2] = hold_ttl_ms
# ARGV[3] = committed_spend (decimal string, from the existing usage:spend:* GET)
# ARGV[4] = budget (decimal string)
# ARGV[5] = member ("{hold_estimate}:{uuid}")
# ARGV[6] = ttl_s (housekeeping EXPIRE on the two keys themselves)
#
# Returns {1} admit (reservation recorded) or {0} deny (no mutation).
_CHECK_AND_HOLD_LUA = """
local zset_key = KEYS[1]
local sum_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local hold_ttl_ms = tonumber(ARGV[2])
local committed = tonumber(ARGV[3]) or 0
local budget = tonumber(ARGV[4])
local member = ARGV[5]
local ttl_s = tonumber(ARGV[6])

-- Evict expired holds and subtract their amounts from the sum (same eviction
-- idiom as _TPM_CHECK_LUA/_TPM_RECORD_LUA in redis_lua_limiter.py).
local evicted = redis.call('ZRANGEBYSCORE', zset_key, 0, now_ms - hold_ttl_ms)
local evict_sum = 0
for _, m in ipairs(evicted) do
    local amt_str = string.match(m, "^([%d%.]+):")
    if amt_str then
        evict_sum = evict_sum + tonumber(amt_str)
    end
end
if #evicted > 0 then
    redis.call('ZREMRANGEBYSCORE', zset_key, 0, now_ms - hold_ttl_ms)
end
if evict_sum > 0 then
    local new_sum = redis.call('INCRBYFLOAT', sum_key, -evict_sum)
    if tonumber(new_sum) < 0 then
        redis.call('SET', sum_key, '0')
    end
end

local raw_hold_sum = redis.call('GET', sum_key)
local hold_sum = 0
if raw_hold_sum then
    hold_sum = tonumber(raw_hold_sum) or 0
end

local prospective = committed + hold_sum
if prospective >= budget then
    -- Denied: no reservation added, no mutation beyond the eviction above.
    return {0}
end

-- Admit: reserve this call's placeholder against the budget.
local hold_amount_str = string.match(member, "^([%d%.]+):")
local hold_amount = tonumber(hold_amount_str) or 0
redis.call('ZADD', zset_key, now_ms, member)
redis.call('INCRBYFLOAT', sum_key, hold_amount)
redis.call('EXPIRE', zset_key, ttl_s)
redis.call('EXPIRE', sum_key, ttl_s)

return {1}
"""


class RedisBudgetGuard:
    """Concrete BudgetGuard: reads DB budget + Redis counter, enforces ceiling.

    Constructor args:
      redis: redis.asyncio client (or duck-typed fake in tests).
      session_factory: async_sessionmaker[AsyncSession] bound to the DB pool.
      hold_estimate_usd: placeholder reserved against the budget for every call
        this guard ADMITS (audit-remediation C1 fix); default mirrors
        credits_hold_estimate_usd's own $0.50 default (core/config.py).
      hold_ttl_seconds: how long a reservation counts toward "prospective spend"
        before self-expiring (no explicit release exists — see module docstring).
    """

    def __init__(
        self,
        *,
        redis: Any,
        session_factory: async_sessionmaker[AsyncSession],
        hold_estimate_usd: Decimal = _DEFAULT_HOLD_ESTIMATE_USD,
        hold_ttl_seconds: int = _DEFAULT_HOLD_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory
        self._hold_estimate_usd = hold_estimate_usd
        self._hold_ttl_seconds = hold_ttl_seconds
        # Lazy-registered (audit-remediation C1): NOT called eagerly in __init__ so
        # a duck-typed test fake exposing only get/incrbyfloat/etc (no
        # register_script) still constructs successfully — mirrors this class's
        # own pre-existing duck-typing tolerance for `redis`. First failure (e.g.
        # AttributeError against such a fake) surfaces inside _check_and_reserve,
        # which is already inside check()'s fail-open try/except.
        self._check_and_hold_script: Any = None

    async def check(self, tenant_id: uuid.UUID) -> None:
        """Enforce the monthly budget ceiling.

        Raises ProblemError(402, "ERR_BUDGET_EXCEEDED") when spent + active holds
        >= budget. Never raises on infrastructure failures.
        """
        try:
            await self._check_internal(tenant_id)
        except ProblemError:
            raise
        except Exception as exc:
            _log.warning(
                "budget_guard.check failed (swallowed — fail open)",
                exc_info=exc,
                extra={"tenant_id": str(tenant_id)},
            )

    async def _check_internal(self, tenant_id: uuid.UUID) -> None:
        """Core check logic — ProblemError propagates; other exceptions swallowed by caller."""
        # Step 1: Fetch budget_usd_monthly from DB
        budget = await self._fetch_budget(tenant_id)
        if budget is None:
            # NULL budget = unlimited — allow unconditionally
            return

        # Step 2/3: atomic check-and-reserve (audit-remediation C1) — replaces the
        # old unlocked "GET spend, compare in Python" TOCTOU with ONE Redis Lua
        # round-trip that reads the committed counter, adds any still-active
        # reservations, compares against budget, and — only when admitting —
        # atomically records this call's own reservation. See module docstring.
        admitted = await self._check_and_reserve(tenant_id, budget)
        if not admitted:
            # Best-effort, informational only (the decision above already ran
            # against committed+held; a fresh GET here can't change the outcome
            # but keeps the error detail human-readable).
            spent = await self._fetch_spent(tenant_id)
            raise BUDGET_EXCEEDED.exc(
                detail=f"Spent {spent} >= budget {budget} for tenant {tenant_id}"
            )

    async def _check_and_reserve(self, tenant_id: uuid.UUID, budget: Decimal) -> bool:
        """Atomic Redis-side check-and-reserve — see module docstring (C1 fix)."""
        committed = await self._fetch_spent(tenant_id)
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        zset_key = f"budget:hold:{tenant_id}:{yyyymm}"
        sum_key = f"budget:hold_sum:{tenant_id}:{yyyymm}"
        now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
        hold_ttl_ms = self._hold_ttl_seconds * 1000
        member = f"{self._hold_estimate_usd}:{uuid.uuid4().hex}"

        if self._check_and_hold_script is None:
            self._check_and_hold_script = self._redis.register_script(_CHECK_AND_HOLD_LUA)

        result = await self._check_and_hold_script(
            keys=[zset_key, sum_key],
            args=[
                str(now_ms),
                str(hold_ttl_ms),
                str(committed),
                str(budget),
                member,
                str(self._hold_ttl_seconds + 1),
            ],
        )
        return bool(int(result[0]))

    async def _fetch_budget(self, tenant_id: uuid.UUID) -> Decimal | None:
        """Return Decimal budget or None (unlimited). Raises on DB failure.

        plan-enforcement (TASK.md §3, M2): extends the single query with a LEFT JOIN to
        `plans` so an assigned plan's budget_usd_monthly_default fills the gap when the
        tenant has no explicit budget of its own — the SAME choke point milestone rule 4
        requires credits-ledger to reuse, one query, zero pipeline edits. Precedence via
        resolve_entitlements (M1): explicit tenant setting > plan default > unlimited.
        """
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT t.budget_usd_monthly, p.budget_usd_monthly_default "
                        "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id "
                        "WHERE t.id = :tid"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).fetchone()
        if row is None:
            return None
        tenant_budget = Decimal(str(row[0])) if row[0] is not None else None
        plan_budget_default = Decimal(str(row[1])) if row[1] is not None else None
        return resolve_entitlements(
            tenant_budget_usd_monthly=tenant_budget,
            plan_id=None,
            plan_budget_usd_monthly_default=plan_budget_default,
            plan_model_allowlist=None,
            plan_feature_flags=None,
        ).effective_budget_usd_monthly

    async def _fetch_spent(self, tenant_id: uuid.UUID) -> Decimal:
        """Return advisory spend counter from Redis; returns 0 on any failure."""
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:{tenant_id}:{yyyymm}"
        try:
            raw = await self._redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "budget_guard: Redis GET failed (treating spend as 0, fail open)",
                exc_info=exc,
                extra={"tenant_id": str(tenant_id), "key": spend_key},
            )
            return _ZERO
        if raw is None:
            return _ZERO
        try:
            return Decimal(raw.decode() if isinstance(raw, bytes) else str(raw))
        except InvalidOperation:
            _log.warning(
                "budget_guard: unparseable spend counter value",
                extra={"tenant_id": str(tenant_id), "raw": repr(raw)},
            )
            return _ZERO
