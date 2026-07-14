"""agent_principal_attribution — add agent_principal_id UUID NULL column to usage_records.

Revision ID: a2b4c6d8e0f1
Revises: c1a2b3d4e5f6
Create Date: 2026-07-14

Additive migration (defect fix on agent-identity-governance TASK.md §3's frozen M4
budget guarantee — see the docstring on recorder.py's agent_principal_id handling and
usage/application/cost_recovery.py/recovery_sweep.py):
  ALTER TABLE usage_records ADD COLUMN agent_principal_id UUID NULL;

  Mirrors team_id (migration e1a3f5b9c7d2, team_attribution) exactly: no FK to
  agent_principals — append-only ledger; a principal deletion (if that ever ships)
  must not cascade-delete ledger rows (historical attribution must survive it). No
  index — unlike team_id, no query filters usage_records WHERE agent_principal_id=...
  yet; recovery_sweep only SELECTs the column off the anchor row it already scans by
  provider_generation_id (an existing partial index). No new tables; EXPECTED_TABLES
  manifest unchanged.

  Root cause this closes: record()'s main-path write already RECEIVES
  agent_principal_id (threaded from AuthzResult at every call site — proxy/application/
  use_cases.py, embeddings/images/audio use cases) and uses it to INCR the
  usage:spend:agent_principal:{id}:{YYYYMM} Redis counter, but never persists it
  DURABLY anywhere — not in this column (didn't exist), not even in the `raw` JSONB.
  So the disconnect/cost-recovery CORRECTION path (cost_recovery.py -> recovery_sweep.py)
  had no way to recover which principal a stale client_disconnect anchor row belonged
  to, and record_correction() was always called without agent_principal_id — the
  correction delta silently never reached the principal's spend counter, letting a
  principal spend past its cap after a disconnect+true-up. This column gives the
  anchor row a durable home for the id so the correction path can read it back.

Downgrade:
  ALTER TABLE usage_records DROP COLUMN IF EXISTS agent_principal_id;
  (safe — additive nullable column; downgrade removes only this migration's artefact)

Migration chain: ... -> a58f0d2/c1a2b3d4e5f6 (claude-gateway-protocol-compat) ->
  a2b4c6d8e0f1 (agent_principal_attribution)

NOTE for the ADD orchestrator: this migration was authored on branch
fix/agent-identity-killswitch-budget, parented on head c1a2b3d4e5f6
(claude-gateway-protocol-compat) as of 2026-07-14. Re-parent (edit down_revision) if
another migration lands ahead of it before integration — single linear chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "a2b4c6d8e0f1"
down_revision: str | None = "c1a2b3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add agent_principal_id UUID NULL column to usage_records."""
    op.add_column(
        "usage_records",
        sa.Column("agent_principal_id", PGUUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Drop agent_principal_id column from usage_records."""
    op.drop_column("usage_records", "agent_principal_id")
