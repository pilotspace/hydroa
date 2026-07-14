"""audit-remediation C3 — tenants.billing_mode (double-bill fix).

Revision ID: d4a1b8c3f6e2
Revises: f5a8c1e3b6d9
Create Date: 2026-07-14

Additive migration (audit-remediation §C3 — HIGH double-bill finding):
  ALTER TABLE tenants ADD COLUMN billing_mode TEXT NOT NULL DEFAULT 'invoice';
  ALTER TABLE tenants ADD CONSTRAINT ck_tenants_billing_mode
    CHECK (billing_mode IN ('invoice', 'credits'));

Why the default preserves today's behavior for every existing tenant:
  Every tenant in this table today is billed EXACTLY one way — monthly by
  InvoiceGenerator off SUM(usage_records.cost_usd) (billing/application/
  invoice_generator.py). The credits ledger (credits/infrastructure/
  postgres_guard.py) is a SEPARATE, orthogonal mechanism gated by a single
  GLOBAL operator flag (settings.credits_gate_enabled, main.py) — when on, it
  ALSO holds+settles the SAME usage_records.cost_usd for every tenant with a
  credits balance, with no coordination between the two paths (the double-bill
  HAZARD this task closes). Backfilling every pre-existing row to
  billing_mode='invoice' (the server_default, no explicit backfill UPDATE
  needed) means InvoiceGenerator.generate_for_tenant's new billing_mode == 'credits'
  skip-gate (see invoice_generator.py) NEVER fires for any tenant that exists at
  migration time — every existing tenant keeps getting invoiced exactly as it
  does today, byte-identical, regardless of whether credits_gate_enabled happens
  to be on in a given environment. An operator must explicitly opt a tenant INTO
  'credits' mode (no code path in this fix does so automatically) before
  InvoiceGenerator will ever skip them — this migration only makes the kill
  switch available, it does not flip it for anyone.

Downgrade: drop the CHECK constraint then the column (safe, additive — no other
table/row depends on it, mirrors allow_non_claude_failover's own
add-then-drop-only precedent, migration c1a2b3d4e5f6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a1b8c3f6e2"
down_revision: str | None = "f5a8c1e3b6d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.billing_mode (NOT NULL DEFAULT 'invoice') + its CHECK constraint."""
    op.add_column(
        "tenants",
        sa.Column(
            "billing_mode",
            sa.Text(),
            nullable=False,
            server_default="invoice",
        ),
    )
    op.create_check_constraint(
        "ck_tenants_billing_mode",
        "tenants",
        "billing_mode IN ('invoice', 'credits')",
    )


def downgrade() -> None:
    """Drop the CHECK constraint then the column (safe, additive)."""
    op.drop_constraint("ck_tenants_billing_mode", "tenants", type_="check")
    op.drop_column("tenants", "billing_mode")
