"""Use-cases for the evals module (eval-set-store PLAN.md §3, M6).

Thin orchestrators over the ``EvalStore`` port — the router depends on these, never on the
repository directly. Shape validation (empty name / request_body / assertion) is the router's
job (before it reaches here); these own the flow and the ownership check.
"""

from __future__ import annotations

import uuid

from gateway.evals.domain.errors import EvalSetNotFound
from gateway.evals.domain.ports import EvalStore
from gateway.evals.infrastructure.orm import EvalCaseRow, EvalSetRow


class CreateEvalSetUseCase:
    def __init__(self, store: EvalStore) -> None:
        self._store = store

    async def execute(
        self, *, tenant_id: uuid.UUID, name: str, description: str | None
    ) -> EvalSetRow:
        """Create a payload-free eval set for the tenant (no ZDR gate — M1/M7)."""
        return await self._store.create_set(tenant_id=tenant_id, name=name, description=description)


class CreateEvalCaseUseCase:
    def __init__(self, store: EvalStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        eval_set_id: uuid.UUID,
        request_body: dict[str, object],
        assertion: dict[str, object],
    ) -> EvalCaseRow:
        """Create a case under a tenant's set (payload write).

        The parent set is resolved in the same tenant scope FIRST — an absent or cross-tenant
        set raises ``EvalSetNotFound`` (M5, uniform 404) before any payload is touched. The ZDR
        gate is enforced atomically inside ``create_case`` (M3).
        """
        parent = await self._store.get_set(tenant_id=tenant_id, eval_set_id=eval_set_id)
        if parent is None:
            raise EvalSetNotFound
        return await self._store.create_case(
            tenant_id=tenant_id,
            eval_set_id=eval_set_id,
            request_body=request_body,
            assertion=assertion,
        )
