"""Domain ports for the budgets module — zero framework imports.

Contract FROZEN @ v1 (budgets TASK.md §3).
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class BudgetGuard(Protocol):
    """Pre-flight budget enforcement port.

    Called AFTER key auth, BEFORE model-check and upstream.
    Contract: raises ProblemError(402, "ERR_BUDGET_EXCEEDED", ...) when
    the tenant's spend counter >= budget_usd_monthly AND budget is not NULL.
    NEVER raises for any other reason:
      - Redis unavailable → log warning + allow
      - NULL budget (unlimited) → allow
      - Missing counter (no spend yet) → treat as 0.00 → allow
    """

    async def check(self, tenant_id: uuid.UUID) -> None:
        """Check whether the tenant is within their monthly budget.

        Raises ProblemError(402, "ERR_BUDGET_EXCEEDED") when spent >= budget.
        Silently allows on any infrastructure failure (Redis down, DB timeout).
        """
        ...


class PassthroughBudgetGuard:
    """No-op implementation — always allows.

    Default for CompletionUseCase when the budgets module is not wired,
    and for test cases that do not exercise the budget path.
    """

    async def check(self, tenant_id: uuid.UUID) -> None:
        """Always passes — does nothing."""
        return


__all__ = ["BudgetGuard", "PassthroughBudgetGuard"]
