"""merge platform_identity_batch_processing and invites_plans_impersonation heads

Revision ID: fef4716b6e33
Revises: 1d563bf9b143, a2dd604805d7
Create Date: 2026-07-05

No-op merge: a2dd604805d7 already linearized platform_identity (5b34ca5e1c4b) and
batch_processing (d5e7f9a1c3b6) into one head. Independently, member-invite-issuance
(1193bc6178f3), plan-catalog (1e66a2cb51a6), and impersonation-session-lifecycle
(1d563bf9b143) branched directly off 5b34ca5e1c4b on a separate local branch, producing
a second head once both sides are combined. This linearizes them into one; neither
parent's DDL is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "fef4716b6e33"
down_revision: str | Sequence[str] | None = ("1d563bf9b143", "a2dd604805d7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
