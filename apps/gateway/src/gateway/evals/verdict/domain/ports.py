"""Persistence port for the baseline pin — baseline-and-verdict §3 (M3, M6).

The seam the router depends on, so it never touches SQLAlchemy directly and the pin/read
behavior is provable over a fake. The SQLAlchemy adapter lives in
``infrastructure/repository.py``.

INVARIANT (M5): every tenant-facing read is tenant-scoped in the SAME query that resolves the
row — an absent or cross-tenant baseline is ``None`` (the router maps that to ``no_baseline`` or
a uniform 404 as appropriate). Pinning is an idempotent upsert on ``UNIQUE(eval_set_id)`` (M3):
one baseline per set, promotable to a better run.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from gateway.evals.verdict.infrastructure.orm import EvalBaselineRow


class EvalBaselineStore(Protocol):
    """Tenant-scoped persistence for the one-baseline-per-set pin."""

    async def pin_baseline(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID, run_id: uuid.UUID
    ) -> EvalBaselineRow:
        """Upsert the set's baseline to ``run_id`` (idempotent on eval_set_id) and return it."""
        ...

    async def get_baseline(
        self, *, tenant_id: uuid.UUID, eval_set_id: uuid.UUID
    ) -> EvalBaselineRow | None:
        """The set's pinned baseline for this tenant, or None (absent/cross-tenant, M5/M6)."""
        ...
