"""merge gpt_realtime_audio_columns and tenant_model_presets heads

Revision ID: 326b927cf8c2
Revises: a4c6e8b0d2f3, b5f8a1d4c7e0
Create Date: 2026-07-02

No-op merge: both parent migrations independently declared down_revision=
c2e4a6f8b0d3 (added on parallel feature branches — gpt-realtime-pricing and
v56 per-tenant model presets), producing two heads. This linearizes them into
one; neither parent's DDL is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "326b927cf8c2"
down_revision: str | Sequence[str] | None = ("a4c6e8b0d2f3", "b5f8a1d4c7e0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
