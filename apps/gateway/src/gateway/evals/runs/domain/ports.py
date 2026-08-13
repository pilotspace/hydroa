"""Persistence port for eval-run execution — eval-run-executor §3 (M8, appsec-engineer lens).

The seam the executor + router depend on, so neither touches SQLAlchemy directly and the
executor's behavior (governance / breaker / ZDR / resume) is provable over a fake store + a
fake upstream with no network. The SQLAlchemy adapter lives in
``infrastructure/repository.py``.

INVARIANT (M6): every tenant-facing read is tenant-scoped in the SAME query that resolves the
row — an absent or cross-tenant run is None/[] (the router maps that to a uniform 404). The
worker-facing ``load_run`` is the ONE un-scoped read: it trusts a run id claimed from the
internal durable queue and re-derives the tenant from the row (never from caller input).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from gateway.evals.infrastructure.orm import EvalCaseRow
from gateway.evals.runs.domain.entities import CaseOutcome
from gateway.evals.runs.infrastructure.orm import EvalCaseResultRow, EvalRunRow


class EvalRunStore(Protocol):
    """Tenant-scoped persistence for eval runs and their per-case results."""

    async def create_run(
        self, *, tenant_id: uuid.UUID, key_id: uuid.UUID, eval_set_id: uuid.UUID, model: str
    ) -> EvalRunRow:
        """Insert a pending run for the tenant and return it (raw key is NEVER persisted)."""
        ...

    async def get_run(self, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> EvalRunRow | None:
        """Resolve a run owned by this tenant. None for absent/cross-tenant (uniform, M6)."""
        ...

    async def load_run(self, run_id: uuid.UUID) -> EvalRunRow | None:
        """Worker-facing: load a run by id WITHOUT a tenant filter (id came from the queue)."""
        ...

    async def snapshot_cases(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID, created_at_max: object
    ) -> list[EvalCaseRow]:
        """The set's cases at launch time (created_at <= the run's created_at), creation order.

        A2: a case added AFTER the run launched is NOT in this snapshot, so the run's
        denominator is fixed. A5: creation order (created_at, id) so two runs of the same set
        align case-for-case.
        """
        ...

    async def existing_result_case_ids(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        """The set of eval_case_ids that already have a terminal result row (resume skip, M7)."""
        ...

    async def record_case_result(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        eval_case_id: uuid.UUID,
        outcome: CaseOutcome,
    ) -> bool:
        """Commit ONE result row, idempotently (M7 / R:DOUBLE_BILL).

        For a ``completed`` (payload-bearing) outcome the ZDR gate is enforced ATOMICALLY with
        the insert (``raise_if_zdr_locked``, SELECT … FOR UPDATE) — a flip landing mid-run
        raises ``ProblemError`` and NOTHING is persisted for that case (M5). Returns False (a
        no-op) if a result for (run, case) already exists — a resumed/raced drive never
        double-writes. Raises ``ProblemError`` on a ZDR refusal so the executor can mark the
        run blocked.
        """
        ...

    async def counts_by_status(self, run_id: uuid.UUID) -> dict[str, int]:
        """{completed, refused, errored} result counts for the run (rollup + status derivation)."""
        ...

    async def set_run_status(self, run_id: uuid.UUID, status: str) -> None:
        """Persist the run's DERIVED terminal status (M7) — never written speculatively."""
        ...

    async def list_case_results(
        self, *, tenant_id: uuid.UUID, run_id: uuid.UUID
    ) -> list[EvalCaseResultRow]:
        """A run's per-case results in the set's creation order (A5), tenant-scoped (M6)."""
        ...
