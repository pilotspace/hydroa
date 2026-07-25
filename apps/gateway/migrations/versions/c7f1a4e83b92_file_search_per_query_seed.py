"""file_search_per_query_seed — seed the billing-only synthetic ``file_search`` catalog
row + its per_query pricing snapshot (file-search-tool PLAN.md §3 Metering, FROZEN @ v1).

Revision ID: c7f1a4e83b92
Revises: b3d8f21ca9e6
Create Date: 2026-07-25

Data-only seed (no DDL) — inserts exactly ONE ``models`` row and exactly ONE
``pricing_snapshots`` row for the sentinel model_id ``file_search``, the FIRST use of
the ``per_query`` pricing unit. Mirrors the ``mcp_tool_call`` / ``per_tool_call`` seed
precedent (b64d469b341e) exactly.

  models:
    id='file_search', name='File Search', active=false, modality='tool_call',
    provider='hydroa', region='global'
    -- active=false (NOT the ORM's `true` default) keeps this row OUT of the tenant-facing
       GET /catalog/models listing (list_active_models_with_markup filters WHERE active=true)
       while it stays fully resolvable by _fetch_latest_pricing / resolve_markup_pct (both
       key on the model_id string, independent of models.active). NOT chat-dispatchable.
    -- modality='tool_call' (NOT the ORM's 'chat' default) exempts the row from every
       future catalog sync's stale-deactivation sweep (WHERE modality='chat'). Both flags
       are load-bearing (same traps as the mcp_tool_call seed), not incidental.

  pricing_snapshots:
    id=85a78c07-5e5d-5c13-8203-6285755a2568 (FIXED uuid5(NAMESPACE_URL,
      "hydroa:pricing_snapshots:file_search:v1") — deterministic across every run, so
      ON CONFLICT (id) DO NOTHING is a real idempotency guard),
    model_id='file_search', pricing_unit='per_query', unit_usd_per_unit=0.0025
      (Tin-frozen at 2026-07-24; the §3 target computes 1 x 0.0025 x 1.20 = 0.00300000),
    prompt_usd_per_token=0, completion_usd_per_token=0 (NOT NULL columns; inert for a
      non-token unit — the per_token branch never reads this row).

Safety (PLAN.md §5): models row FIRST (pricing_snapshots.model_id FKs models.id,
ondelete=RESTRICT) so a partial seed (pricing row without its model) is never observable.
A missing/NULL-priced snapshot would silently resolve per_query to $0 (the M11 "$0 +
warning" path) for every file_search retrieval until repaired.

Idempotent: safe to run twice (ON CONFLICT (id) DO NOTHING on both rows).
downgrade(): delete the pricing_snapshots row FIRST (FK), then the models row.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f1a4e83b92"
down_revision: str | None = "b3d8f21ca9e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_ID = "file_search"
# Fixed uuid5(NAMESPACE_URL, "hydroa:pricing_snapshots:file_search:v1") — never regenerated.
_PRICING_SNAPSHOT_ID = "85a78c07-5e5d-5c13-8203-6285755a2568"
_UNIT_USD_PER_UNIT = Decimal("0.0025")  # $2.50 / 1k retrievals (Tin, freeze 2026-07-24)

_INSERT_MODEL = sa.text(
    "INSERT INTO models (id, name, context_length, active, modality, provider, region)"
    " VALUES (:id, 'File Search', NULL, false, 'tool_call', 'hydroa', 'global')"
    " ON CONFLICT (id) DO NOTHING"
)
_INSERT_PRICING_SNAPSHOT = sa.text(
    "INSERT INTO pricing_snapshots"
    " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
    "  pricing_unit, unit_usd_per_unit)"
    " VALUES (CAST(:sid AS uuid), :id, 0, 0, 'per_query', :price)"
    " ON CONFLICT (id) DO NOTHING"
)
_DELETE_PRICING_SNAPSHOT = sa.text("DELETE FROM pricing_snapshots WHERE id = CAST(:sid AS uuid)")
_DELETE_MODEL = sa.text("DELETE FROM models WHERE id = :id")


def upgrade() -> None:
    """Seed the file_search models row, then its per_query pricing_snapshots row."""
    op.execute(_INSERT_MODEL.bindparams(id=_MODEL_ID))
    op.execute(
        _INSERT_PRICING_SNAPSHOT.bindparams(
            sid=_PRICING_SNAPSHOT_ID, id=_MODEL_ID, price=_UNIT_USD_PER_UNIT
        )
    )


def downgrade() -> None:
    """Remove the seeded pricing_snapshots row FIRST (FK), then the models row."""
    op.execute(_DELETE_PRICING_SNAPSHOT.bindparams(sid=_PRICING_SNAPSHOT_ID))
    op.execute(_DELETE_MODEL.bindparams(id=_MODEL_ID))
