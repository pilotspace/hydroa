"""Domain ports for the credits module — zero framework imports.

Contract FROZEN @ v1 (credits-ledger TASK.md §3). Mirrors
gateway.budgets.domain.ports.BudgetGuard's shape exactly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol, runtime_checkable


@runtime_checkable
class CreditGuard(Protocol):
    """Reserve-then-settle prepaid credits spend gate.

    check_and_hold: row-locked admission check + hold insert in one DB
    transaction. Raises ProblemError(402, "ERR_CREDITS_EXHAUSTED") when
    balance_usd - hold_estimate_usd < -grace_usd, evaluated INSIDE the same
    locked transaction as the hold insert — two concurrent admissions for the
    SAME tenant serialize on the balance row lock (M2/M3).

    NEVER raises for infra failure (Postgres unreachable) -> logs a structured
    warning + increments the credits_gate_degraded counter + allows
    (fail-open, mirrors RedisBudgetGuard's "never raises on infrastructure
    failures" precedent, M11).

    settle/release are best-effort and NEVER raise — swallow + log, same
    idiom as RecordingUsageRecorder.record().
    """

    async def check_and_hold(
        self, tenant_id: uuid.UUID, request_id: uuid.UUID, hold_estimate_usd: Decimal
    ) -> None:
        """Row-locked admission check + hold insert in one DB transaction."""
        ...

    async def settle(
        self,
        tenant_id: uuid.UUID,
        request_id: uuid.UUID,
        usage_record_id: uuid.UUID,
        actual_cost_usd: Decimal,
    ) -> None:
        """Post settle = actual_cost_usd - hold_estimate_usd against the open hold."""
        ...

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        """Post release = +hold_estimate_usd (full refund) for an open hold."""
        ...


class PassthroughCreditGuard:
    """No-op default — always allows, never holds.

    Wired when credits is not configured for a tenant/deployment, mirrors
    PassthroughBudgetGuard. Default for CompletionUseCase / NonChatGovernance
    when the credits module is not wired, and for test cases that do not
    exercise the credit path.
    """

    async def check_and_hold(
        self, tenant_id: uuid.UUID, request_id: uuid.UUID, hold_estimate_usd: Decimal
    ) -> None:
        return

    async def settle(
        self,
        tenant_id: uuid.UUID,
        request_id: uuid.UUID,
        usage_record_id: uuid.UUID,
        actual_cost_usd: Decimal,
    ) -> None:
        return

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return


__all__ = ["CreditGuard", "PassthroughCreditGuard"]
