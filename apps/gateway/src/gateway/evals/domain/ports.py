"""EvalStore port — eval-set-store PLAN.md §3 (FROZEN @ v1), M6.

The persistence seam the use-cases depend on, so the router never touches SQLAlchemy and a
zero-network fake can stand in for the DB in unit tests. The SQLAlchemy adapter lives in
``infrastructure/repository.py``; a fake implementing this Protocol is what proves the port
exists (the create tests drive it without a network).

INVARIANT (M4/M5): every method is tenant-scoped; an unknown or cross-tenant id returns
None/[] — never a distinguishable error. The ZDR gate (M3) is enforced INSIDE ``create_case``
(``raise_if_zdr_locked``, atomic with the insert), not by the caller.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from gateway.evals.infrastructure.orm import EvalCaseRow, EvalSetRow


class EvalStore(Protocol):
    """Tenant-scoped persistence for eval sets and cases."""

    async def create_set(
        self, *, tenant_id: uuid.UUID, name: str, description: str | None
    ) -> EvalSetRow:
        """Insert a new eval set (metadata only) and return it. No ZDR gate (payload-free)."""
        ...

    async def list_sets(self, *, tenant_id: uuid.UUID) -> list[EvalSetRow]:
        """The tenant's sets, newest first."""
        ...

    async def get_set(self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID) -> EvalSetRow | None:
        """Resolve a set owned by this tenant. None for absent/cross-tenant (uniform, M5)."""
        ...

    async def count_cases(self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID) -> int:
        """Number of cases in a tenant's set (for the list projection's case_count)."""
        ...

    async def create_case(
        self,
        *,
        tenant_id: uuid.UUID,
        eval_set_id: uuid.UUID,
        request_body: dict[str, object],
        assertion: dict[str, object],
    ) -> EvalCaseRow:
        """Insert a case under a tenant's set — payload write.

        Enforces the ZDR gate ATOMICALLY with the insert (``raise_if_zdr_locked``, M3): a flip
        landing mid-await persists nothing. Raises 403 ERR_ZDR_PAYLOAD_BLOCKED for a ZDR tenant.
        """
        ...

    async def list_cases(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID
    ) -> list[EvalCaseRow]:
        """The set's cases in creation order (M5-order)."""
        ...
