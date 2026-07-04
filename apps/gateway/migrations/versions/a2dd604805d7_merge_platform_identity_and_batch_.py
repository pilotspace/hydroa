"""merge platform_identity and batch_processing heads

Revision ID: a2dd604805d7
Revises: 5b34ca5e1c4b, d5e7f9a1c3b6
Create Date: 2026-07-04

No-op merge: both parent migrations independently declared down_revision=
326b927cf8c2 (added on parallel feature branches — platform-identity/superadmin-role
and v57/batch-auto-grouping), producing two heads. This linearizes them into
one; neither parent's DDL is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a2dd604805d7"
down_revision: str | Sequence[str] | None = ("5b34ca5e1c4b", "d5e7f9a1c3b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
