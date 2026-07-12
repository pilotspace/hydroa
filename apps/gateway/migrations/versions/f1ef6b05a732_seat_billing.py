"""seat_billing — seat_membership_events append-only ledger + plans.seat_price_usd_monthly.

Revision ID: f1ef6b05a732
Revises: 1891020e487c
Create Date: 2026-07-12

Creates the `seat_membership_events` table (seat-billing TASK.md §3 CONTRACT — FROZEN
@ v2): an append-only ledger of `joined`/`deactivated`/`reactivated` transitions, one row
per event, written transactionally alongside each `users`-row mutation (application-layer
write sites: InviteRepository.accept, SqlAlchemyScimUserRepository.create_user/.set_active,
_get_or_provision_sso_user new-user branch, join_verified_tenant_domain — v2/CR-1 adds the
last two). NEVER updated or deleted (M3) — the seat-domain analog of `usage_records`'
"one ledger of truth" doctrine (§1 ⚠, the single lowest-confidence call in the frozen
contract, CONFIRMED at freeze).

Also adds ONE additive, nullable column to the existing `plans` table:
  - `seat_price_usd_monthly` (NUMERIC(12,2), CHECK > 0 if set) — NULL = no seat pricing
    (inert, M2). Seeded in THIS SAME migration per the DECIDED freeze-review prices:
    team **$15.00** / enterprise **$40.00** per seat-month; starter stays NULL/seatless
    (replaces the §3 "TBD" placeholders — DECIDED 2026-07-12, Tin).

Migration-time backfill (M4, data-only): for EVERY pre-existing `users` row, seed exactly
one synthetic `'joined'` event at `occurred_at = users.created_at`; for every pre-existing
row that is ALREADY deactivated (`deactivated_at IS NOT NULL`), seed one additional
`'deactivated'` event at `occurred_at = users.deactivated_at`. Without this, every tenant's
pre-existing team would silently price as zero seats on the first post-ship invoice (R5) —
a severe, silent under-bill, not a narrow edge case. Runs via `op.execute(text(...))`
INSERT...SELECT (single statement, no per-row Python loop — mirrors this migration file's
own additive-migration-with-data-seed precedent, e.g. 1e66a2cb51a6's `op.bulk_insert`).

`users.id`/`users.tenant_id`/`users.created_at`/`users.deactivated_at` are stable, already-
shipped columns (§0 GROUND) — this backfill reads them directly via raw SQL, never via the
ORM (mirrors every other migration-time backfill in this codebase, e.g. the scim-
provisioning `deactivated_at` backfill).

Downgrade: additive-only, safe — drops the new table + column in dependency order, mirrors
every prior additive migration in this milestone (e.g. plan_catalog's own downgrade note).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "f1ef6b05a732"
down_revision: str | None = "1891020e487c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seat_membership_events",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('joined', 'deactivated', 'reactivated')",
            name="ck_seat_membership_events_event_type",
        ),
    )
    op.create_index(
        "ix_seat_membership_events_tenant_user_occurred",
        "seat_membership_events",
        ["tenant_id", "user_id", "occurred_at"],
    )

    op.add_column(
        "plans",
        sa.Column("seat_price_usd_monthly", sa.Numeric(12, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_plans_seat_price_positive",
        "plans",
        "seat_price_usd_monthly IS NULL OR seat_price_usd_monthly > 0",
    )

    # DECIDED at freeze review (2026-07-12, Tin): team $15.00 / enterprise $40.00 per
    # seat-month; starter stays NULL/seatless (no UPDATE needed — NULL is the column
    # default for every row, including starter's).
    op.execute(sa.text("UPDATE plans SET seat_price_usd_monthly = '15.00' WHERE name = 'team'"))
    op.execute(
        sa.text("UPDATE plans SET seat_price_usd_monthly = '40.00' WHERE name = 'enterprise'")
    )

    # Backfill (M4/R5): one synthetic 'joined' event per pre-existing users row.
    op.execute(
        sa.text(
            "INSERT INTO seat_membership_events (id, tenant_id, user_id, event_type, occurred_at)"
            " SELECT gen_random_uuid(), tenant_id, id, 'joined', created_at FROM users"
        )
    )
    # Backfill (M4/R5): one additional synthetic 'deactivated' event for every
    # pre-existing row already deactivated as of migration time.
    op.execute(
        sa.text(
            "INSERT INTO seat_membership_events (id, tenant_id, user_id, event_type, occurred_at)"
            " SELECT gen_random_uuid(), tenant_id, id, 'deactivated', deactivated_at"
            " FROM users WHERE deactivated_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Reverse in dependency order: constraint -> column -> table."""
    op.drop_constraint("ck_plans_seat_price_positive", "plans", type_="check")
    op.drop_column("plans", "seat_price_usd_monthly")
    op.drop_index(
        "ix_seat_membership_events_tenant_user_occurred",
        table_name="seat_membership_events",
    )
    op.drop_table("seat_membership_events")
