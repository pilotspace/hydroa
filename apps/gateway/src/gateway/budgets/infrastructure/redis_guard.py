"""RedisBudgetGuard — infrastructure adapter for the BudgetGuard port.

Reads budget_usd_monthly from Postgres (one SELECT per request, MVP),
reads the advisory spend counter from Redis, and enforces the ceiling.

Fail-open semantics (availability over enforcement):
  - Redis unavailable → log warning + allow
  - DB unavailable → log warning + allow
  - NULL budget → allow (unlimited)
  - Missing counter → treat as Decimal("0.00") → allow
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

_log = logging.getLogger(__name__)

_ZERO = Decimal("0")


class RedisBudgetGuard:
    """Concrete BudgetGuard: reads DB budget + Redis counter, enforces ceiling.

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

    async def check(self, tenant_id: uuid.UUID) -> None:
        """Enforce the monthly budget ceiling.

        Raises ProblemError(402, "ERR_BUDGET_EXCEEDED") when spent >= budget.
        Never raises on infrastructure failures.
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

        # Step 2: Fetch spend counter from Redis
        spent = await self._fetch_spent(tenant_id)

        # Step 3: Enforce ceiling
        if spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=f"Spent {spent} >= budget {budget} for tenant {tenant_id}"
            )

    async def _fetch_budget(self, tenant_id: uuid.UUID) -> Decimal | None:
        """Return Decimal budget or None (unlimited). Raises on DB failure."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT budget_usd_monthly FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0]))

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
