"""PostgresCreditGuard — concrete CreditGuard: row-locked reserve-then-settle.

Settle sign convention (§2 scenario oracle, not §3's prose which is ambiguous on
sign): settle_amount_usd = hold_estimate_usd - actual_cost_usd — consistent with the
schema's own signed-delta convention (hold posts a NEGATIVE amount_usd). actual_cost
below the hold estimate nets a POSITIVE settle (partial refund); above it nets a
NEGATIVE settle (additional debit). Verified against §2's two concrete settle
scenarios: hold=-0.50 + actual=0.37 -> settle=+0.13, balance 4.50->4.63; hold=-0.50 +
actual=0.80 -> settle=-0.30, balance 4.50->4.20.

NOTE — frozen §2 typo found during test-writing (tests/credits_ledger/test_credits_ledger.py
::test_settle_reconciles_hold_to_actual_cost): the scenario's headline text says balance
"becomes 4.87 (5.00 - 0.37, ...)" but 5.00-0.37=4.63, not 4.87 — the scenario's own worked
proof ("hold+settle summing to -0.37") is self-consistent with 4.63. This module implements
the arithmetically self-consistent value; flagged for a contract correction at Observe.

Fail-open semantics (availability over enforcement, mirrors RedisBudgetGuard):
  - Postgres unreachable (check_and_hold) -> log warning + increment
    credits_gate_degraded_total + allow (no hold placed).
  - Postgres unreachable (settle/release) -> log warning + increment
    credits_gate_degraded_total + swallow (the open hold is later reclaimed by
    the M6 reconciliation sweep, CreditHoldRecoverySweeper).

Concurrency (M2/M3): every write path (check_and_hold/settle/release) opens its
own transaction and calls ledger_store.lock_balance_row, which SELECT ... FOR
UPDATEs the tenant's tenant_credit_balances row. Two concurrent calls for the
SAME tenant_id serialize on that row lock — the second call's SELECT blocks
until the first transaction commits or rolls back, so the balance it reads
already reflects the first call's write. This is what closes the
concurrent-exhaustion race (TOCTOU window) at the database level, not in
application code.

HEAL (credits-ledger verify finding 1, 2026-07-12): settle()/release() originally
called find_open_hold() BEFORE lock_balance_row() — a plain unlocked SELECT ahead
of the row lock, so two concurrent finalizers for the SAME (tenant_id, request_id)
(e.g. the M6 sweep racing a late completion) could both observe the hold as open
and both post a settle/release (double-credit/debit); the lock-then-decide
discipline above was NOT actually applied to finalization, only to admission.
Fixed by reordering both methods to lock_balance_row() FIRST, THEN find_open_hold()
inside the held lock — the second finalizer's re-check now runs only after the
first transaction has committed its settle/release row and correctly no-ops.
Repro: tests/credits_ledger/test_verify_adversarial.py::
test_verify_concurrent_settle_and_release_double_post_same_hold.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.error_catalog import CREDITS_EXHAUSTED
from gateway.core.errors import ProblemError
from gateway.credits.infrastructure.ledger_store import (
    find_open_hold,
    insert_ledger_row,
    lock_balance_row,
    update_balance,
)

_log = logging.getLogger(__name__)


class PostgresCreditGuard:
    """Concrete CreditGuard backed by Postgres row locks.

    Constructor args:
      session_factory: async_sessionmaker[AsyncSession] bound to the DB pool.
      metrics: optional MetricsRegistry-shaped object exposing
        `credits_gate_degraded_total.labels(operation=...).inc()`; None -> metric
        skipped (log warning is still always emitted, M11's "never silent"
        floor holds via logging alone).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        metrics: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._metrics = metrics

    # ------------------------------------------------------------------
    # check_and_hold
    # ------------------------------------------------------------------

    async def check_and_hold(
        self, tenant_id: uuid.UUID, request_id: uuid.UUID, hold_estimate_usd: Decimal
    ) -> None:
        try:
            await self._check_and_hold_internal(tenant_id, request_id, hold_estimate_usd)
        except ProblemError:
            raise
        except Exception as exc:
            self._degrade("check_and_hold", exc, tenant_id)

    async def _check_and_hold_internal(
        self, tenant_id: uuid.UUID, request_id: uuid.UUID, hold_estimate_usd: Decimal
    ) -> None:
        async with self._session_factory() as session, session.begin():
            balance, grace = await lock_balance_row(session, tenant_id)
            if balance - hold_estimate_usd < -grace:
                raise CREDITS_EXHAUSTED.exc(
                    detail=(
                        f"balance {balance} - hold {hold_estimate_usd} < -grace {grace}"
                        f" for tenant {tenant_id}"
                    )
                )
            new_balance = balance - hold_estimate_usd
            await insert_ledger_row(
                session,
                row_id=uuid.uuid4(),
                tenant_id=tenant_id,
                entry_type="hold",
                amount_usd=-hold_estimate_usd,
                balance_after_usd=new_balance,
                request_id=request_id,
            )
            await update_balance(session, tenant_id, new_balance)

    # ------------------------------------------------------------------
    # settle
    # ------------------------------------------------------------------

    async def settle(
        self,
        tenant_id: uuid.UUID,
        request_id: uuid.UUID,
        usage_record_id: uuid.UUID,
        actual_cost_usd: Decimal,
    ) -> None:
        try:
            await self._settle_internal(tenant_id, request_id, usage_record_id, actual_cost_usd)
        except Exception as exc:
            self._degrade("settle", exc, tenant_id)

    async def _settle_internal(
        self,
        tenant_id: uuid.UUID,
        request_id: uuid.UUID,
        usage_record_id: uuid.UUID,
        actual_cost_usd: Decimal,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            # HEAL (finding 1, verify): lock-then-decide, mirroring check_and_hold's
            # M3 discipline exactly. The balance row lock MUST be taken BEFORE
            # re-checking "is this hold still open" — find_open_hold() alone is a
            # plain unlocked SELECT, so two concurrent finalizers (e.g. the M6 sweep
            # racing a late completion, or a duplicate finalize event) could both
            # read "open" and both post a settle/release against the SAME hold
            # (double-credit/debit). Taking the row lock FIRST serializes the two
            # finalizers on the SAME tenant row: the second transaction's
            # find_open_hold() call only runs after the first has committed its
            # settle/release row, so it correctly sees the hold as closed and
            # no-ops — the same TOCTOU closure check_and_hold already gets from
            # locking before deciding.
            balance, _grace = await lock_balance_row(session, tenant_id)
            open_hold = await find_open_hold(session, tenant_id, request_id)
            if open_hold is None:
                # No open hold (already settled/released — possibly by a concurrent
                # finalizer that won the race above — or the request never held,
                # e.g. credits was disabled at admission time). Nothing to reconcile.
                return
            _hold_id, hold_amount_usd = open_hold
            hold_estimate_usd = -hold_amount_usd
            # §2 scenario oracle (settle 0.50->0.37 => +0.13; settle 0.50->0.80 => -0.30):
            # settle_amount = hold_estimate_usd - actual_cost_usd, so hold(-0.50) + settle
            # sums to -actual_cost_usd exactly. actual < hold -> positive (refund, credits
            # balance); actual > hold -> negative (additional debit).
            settle_amount_usd = hold_estimate_usd - actual_cost_usd

            new_balance = balance + settle_amount_usd
            await insert_ledger_row(
                session,
                row_id=uuid.uuid4(),
                tenant_id=tenant_id,
                entry_type="settle",
                amount_usd=settle_amount_usd,
                balance_after_usd=new_balance,
                reference_type="usage_record",
                reference_id=usage_record_id,
                request_id=request_id,
            )
            await update_balance(session, tenant_id, new_balance)

    # ------------------------------------------------------------------
    # release
    # ------------------------------------------------------------------

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        try:
            await self._release_internal(tenant_id, request_id)
        except Exception as exc:
            self._degrade("release", exc, tenant_id)

    async def _release_internal(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        async with self._session_factory() as session, session.begin():
            # HEAL (finding 1, verify): lock-then-decide — see the matching comment
            # in _settle_internal above; identical reasoning applies to release().
            balance, _grace = await lock_balance_row(session, tenant_id)
            open_hold = await find_open_hold(session, tenant_id, request_id)
            if open_hold is None:
                return
            _hold_id, hold_amount_usd = open_hold
            hold_estimate_usd = -hold_amount_usd

            new_balance = balance + hold_estimate_usd
            await insert_ledger_row(
                session,
                row_id=uuid.uuid4(),
                tenant_id=tenant_id,
                entry_type="release",
                amount_usd=hold_estimate_usd,
                balance_after_usd=new_balance,
                request_id=request_id,
            )
            await update_balance(session, tenant_id, new_balance)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _degrade(self, operation: str, exc: Exception, tenant_id: uuid.UUID) -> None:
        """M11: never silent — structured warning ALWAYS, metric best-effort."""
        _log.warning(
            "credit_guard.%s failed (swallowed — fail open)",
            operation,
            exc_info=exc,
            extra={"tenant_id": str(tenant_id), "operation": operation},
        )
        if self._metrics is not None:
            try:
                self._metrics.credits_gate_degraded_total.labels(operation=operation).inc()
            except Exception:  # noqa: S110  # pragma: no cover — metrics must never break the gate
                pass


__all__ = ["PostgresCreditGuard"]
