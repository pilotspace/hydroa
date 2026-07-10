"""tenant_domain_claims — verified email-domain ownership claims (domain-capture TASK.md §3
M1 — FROZEN @ v1).

New table only — additive, no existing table is altered. Two indexes:
  (a) uq_domain_claims_tenant_domain — plain UNIQUE (tenant_id, domain): one claim row per
      tenant per domain, ever.
  (b) uq_domain_claims_domain_verified — PARTIAL UNIQUE INDEX on domain WHERE
      status='verified': the STRUCTURAL cross-tenant collision guard, mirrors TenantRow's
      own `tenants_platform_kind_uidx` precedent (migration for tenants.kind) — enforced by
      Postgres itself, never an app-level SELECT-then-INSERT race.

Revision ID: b3d8e1f4a7c2
Revises: a1c5e7f9b3d6
Create Date: 2026-07-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "b3d8e1f4a7c2"
down_revision: str | None = "a1c5e7f9b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_domain_claims",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("verification_token", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified')", name="ck_tenant_domain_claims_status"
        ),
    )
    op.create_index(
        "uq_domain_claims_tenant_domain",
        "tenant_domain_claims",
        ["tenant_id", "domain"],
        unique=True,
    )
    op.create_index(
        "uq_domain_claims_domain_verified",
        "tenant_domain_claims",
        ["domain"],
        unique=True,
        postgresql_where=sa.text("status = 'verified'"),
    )


def downgrade() -> None:
    op.drop_index("uq_domain_claims_domain_verified", table_name="tenant_domain_claims")
    op.drop_index("uq_domain_claims_tenant_domain", table_name="tenant_domain_claims")
    op.drop_table("tenant_domain_claims")
