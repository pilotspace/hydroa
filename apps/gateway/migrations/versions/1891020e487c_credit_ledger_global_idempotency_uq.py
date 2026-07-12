"""credit_ledger global idempotency-key uniqueness (credits-ledger HEAL, finding 3 / 🟡).

Revision ID: 1891020e487c
Revises: 0b5527920450
Create Date: 2026-07-12

HEAL note (verify finding 3): R4 ("Idempotency-Key reused with a DIFFERENT tenant_id
or amount_usd than its original use -> 409 CONFLICT") requires GLOBAL uniqueness of
`idempotency_key` across ALL tenants, not just per-tenant. The frozen §3 schema's
`UNIQUE (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL` index (migration
d3f7a9c1b5e8, `credit_ledger_tenant_idem_uq`) only enforces per-tenant uniqueness — the
application layer's `find_topup_by_idempotency_key` (unscoped by tenant_id, deliberately)
was the ONLY thing enforcing the cross-tenant rule, and it was serialized solely by the
CALLER's own tenant balance-row lock — two concurrent top-ups sharing a key for DIFFERENT
tenants lock DIFFERENT balance rows, so neither blocks the other, and both could commit
(a verified race: tests/credits_ledger/test_verify_adversarial.py::
test_verify_concurrent_topup_same_key_different_tenants_bypasses_r4).

This migration ADDS a second, GLOBAL partial unique index on `idempotency_key` alone
(no tenant_id component) — additive, does not touch or replace the frozen per-tenant
index, which stays exactly as §3 specifies. `topup_service.topup()` was changed to an
INSERT-first-with-conflict-detection shape (mirrors the codebase's own
INSERT-then-catch-IntegrityError idiom, e.g. tenants/infrastructure/repository.py
`create_tenant_with_owner`): the read-then-decide pre-check stays (fast path, no
behavior change for the common case), but a losing concurrent INSERT for the same key
now hits this constraint and is caught + resolved (200 replay / 409 conflict) instead of
silently succeeding. This was chosen over a `pg_advisory_xact_lock` (also considered,
per TASK.md dispatch) because a held lock across the read-then-decide critical section
deadlocks against the verify repro's `asyncio.Barrier`-forced-rendezvous technique — the
lock would prevent the two coroutines from ever reaching the barrier concurrently, so
neither could ever proceed. The constraint-based approach lets both transactions run
concurrently (satisfying the repro's rendezvous) and resolves the race reactively via
Postgres's own uniqueness check at INSERT time, with no held lock.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1891020e487c"
down_revision: str | None = "0b5527920450"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a GLOBAL (cross-tenant) partial unique index on credit_ledger.idempotency_key."""
    op.create_index(
        "credit_ledger_idempotency_key_global_uq",
        "credit_ledger",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the global idempotency-key uniqueness index."""
    op.drop_index("credit_ledger_idempotency_key_global_uq", table_name="credit_ledger")
