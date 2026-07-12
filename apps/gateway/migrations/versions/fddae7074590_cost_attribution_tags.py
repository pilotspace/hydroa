"""cost_attribution_tags — additive request-metadata tags column on usage_records.

Revision ID: fddae7074590
Revises: 69cfdc584129
Create Date: 2026-07-12

Additive migration (cost-attribution-tags TASK.md §3 CONTRACT):

  usage_records:
    ADD COLUMN tags JSONB NOT NULL DEFAULT '{}'::jsonb
    CREATE INDEX ix_usage_records_tags_gin ON usage_records USING gin (tags)

NOT NULL DEFAULT via server_default — instant on PostgreSQL (no table rewrite),
every pre-existing row reads back tags={} byte-identically (mirrors the
gpt-realtime-audio-columns additive-column precedent). The GIN index accelerates
containment lookups (tags @> '{"k":"v"}', used by the invoice-generation sibling
task) — it does NOT accelerate the cost-by-tag breakdown's jsonb_each_text GROUP BY
expansion (a tenant+window-scoped table scan, same scale as get_spend/
get_guardrail_analytics already operate at).

Append-only ledger preserved: tags is written ONCE at INSERT time via the existing
Redis-stream write-behind path (recorder.py -> flusher.py insert_usage_row), never
UPDATEd afterward.

downgrade() drops the index then the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fddae7074590"
down_revision: str | None = "69cfdc584129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tags JSONB NOT NULL DEFAULT '{}' + GIN index to usage_records (additive)."""
    op.add_column(
        "usage_records",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_usage_records_tags_gin",
        "usage_records",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Drop the GIN index then the additive column.

    Safe: additive-only; no existing data depends on it at migration time.
    """
    op.drop_index("ix_usage_records_tags_gin", table_name="usage_records")
    op.drop_column("usage_records", "tags")
