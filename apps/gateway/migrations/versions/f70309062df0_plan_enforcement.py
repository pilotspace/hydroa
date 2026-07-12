"""plan_enforcement — additive `plans.model_allowlist`/`plans.feature_flags` columns.

Revision ID: f70309062df0
Revises: 69cfdc584129
Create Date: 2026-07-12

Adds TWO additive columns to the existing `plans` table (plan-enforcement TASK.md §3,
FROZEN @ v1), the FIRST migration to extend `plans` past plan-catalog's own "done" freeze:
  - `model_allowlist` (JSONB NULL) — mirrors ApiKeyRow.model_allowlist's own
    null=all-models/[]=no-models convention exactly. No runtime plan-CRUD writes it (M3).
  - `feature_flags` (JSONB NOT NULL DEFAULT '[]') — array of feature-key strings this plan
    tier grants (M5).

Data-only seed UPDATE for the 3 existing seeded rows (⚠ INVENTED placeholders per TASK.md
§1's own top-ranked assumption — DATA, not shape; same category as plan-catalog's own
disclosed $ numbers):
  starter:    model_allowlist=NULL, feature_flags=["logs_explorer"]
  team:       model_allowlist=NULL, feature_flags=["logs_explorer","batch"]
  enterprise: model_allowlist=NULL,
              feature_flags=["logs_explorer","batch","ml_moderation","realtime"]

Downgrade: additive-only, safe — mirrors plan-catalog's own migration's downgrade note.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f70309062df0"
down_revision: str | None = "69cfdc584129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLANS_TABLE = sa.table(
    "plans",
    sa.column("name", sa.Text()),
    sa.column("feature_flags", sa.JSON()),
)


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("model_allowlist", sa.JSON(), nullable=True),
    )
    op.add_column(
        "plans",
        sa.Column(
            "feature_flags",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    conn = op.get_bind()
    conn.execute(
        _PLANS_TABLE.update()
        .where(_PLANS_TABLE.c.name == "starter")
        .values(feature_flags=sa.text("'[\"logs_explorer\"]'::jsonb"))
    )
    conn.execute(
        _PLANS_TABLE.update()
        .where(_PLANS_TABLE.c.name == "team")
        .values(feature_flags=sa.text('\'["logs_explorer", "batch"]\'::jsonb'))
    )
    conn.execute(
        _PLANS_TABLE.update()
        .where(_PLANS_TABLE.c.name == "enterprise")
        .values(
            feature_flags=sa.text(
                '\'["logs_explorer", "batch", "ml_moderation", "realtime"]\'::jsonb'
            )
        )
    )


def downgrade() -> None:
    """Reverse in dependency order: columns only — additive-only, safe."""
    op.drop_column("plans", "feature_flags")
    op.drop_column("plans", "model_allowlist")
