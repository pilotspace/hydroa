"""moderations_endpoint_seed — seed the omni-moderation-latest catalog row + its
pricing snapshot (moderations-endpoint PLAN.md §3 Schema, FROZEN @ v1).

Revision ID: a3f0c9d17b4e
Revises: b8c1f4a2d6e9
Create Date: 2026-07-24

Data-only seed migration (no DDL) — inserts exactly ONE `models` row and exactly ONE
`pricing_snapshots` row for `omni-moderation-latest`, mirroring
`b64d469b341e_tool_call_metering_seed.py`'s shape/idempotency pattern.

  models:
    id='omni-moderation-latest', modality='moderation', provider='openai', active=true
    -- active=true (unlike mcp_tool_call's synthetic sentinel row): this IS a real,
       directly client-callable model via POST /v1/moderations — it belongs in the
       tenant-facing catalog listing.

  pricing_snapshots:
    pricing_unit='per_token', prompt_usd_per_token=0.0000001 PLACEHOLDER
      (~$0.10 / 1M tokens — ASSUMED, not a business decision this task can make;
      needs Tin's actual number at/after freeze; PLAN.md §1 ⚠ pricing rate),
    completion_usd_per_token=0 (moderation has no completion leg).

Safety rule (mirrors b64d469b341e): the models row insert and the pricing_snapshots
row insert happen in the SAME migration, models row FIRST (pricing_snapshots.model_id
carries a real FK to models.id).

Idempotent: ON CONFLICT DO NOTHING on both inserts (fixed uuid5 pricing_snapshot id).

downgrade(): deletes the pricing_snapshots row FIRST (FK), then the models row.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f0c9d17b4e"
down_revision: str | None = "b8c1f4a2d6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_ID = "omni-moderation-latest"
# Fixed uuid5(NAMESPACE_URL, "hydroa:pricing_snapshots:omni-moderation-latest:v1") —
# deterministic, never regenerated at migration-run time (see module docstring).
_PRICING_SNAPSHOT_ID = "8f1e3a5c-2d4b-5e6f-9a1c-7b8d9e0f1a2b"
_PROMPT_USD_PER_TOKEN = Decimal("0.0000001")  # PLACEHOLDER — needs Tin's number (§1 ⚠)

_INSERT_MODEL = sa.text(
    "INSERT INTO models (id, name, context_length, active, modality, provider)"
    " VALUES (:id, 'omni-moderation-latest', NULL, true, 'moderation', 'openai')"
    " ON CONFLICT (id) DO NOTHING"
)
_INSERT_PRICING_SNAPSHOT = sa.text(
    "INSERT INTO pricing_snapshots"
    " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
    "  pricing_unit, unit_usd_per_unit)"
    " VALUES (CAST(:sid AS uuid), :id, :prompt, 0, 'per_token', NULL)"
    " ON CONFLICT (id) DO NOTHING"
)
_DELETE_PRICING_SNAPSHOT = sa.text("DELETE FROM pricing_snapshots WHERE id = CAST(:sid AS uuid)")
_DELETE_MODEL = sa.text("DELETE FROM models WHERE id = :id")


def upgrade() -> None:
    """Seed the omni-moderation-latest models row, then its pricing_snapshots row."""
    op.execute(_INSERT_MODEL.bindparams(id=_MODEL_ID))
    op.execute(
        _INSERT_PRICING_SNAPSHOT.bindparams(
            sid=_PRICING_SNAPSHOT_ID, id=_MODEL_ID, prompt=_PROMPT_USD_PER_TOKEN
        )
    )


def downgrade() -> None:
    """Remove the seeded pricing_snapshots row FIRST (FK), then the models row."""
    op.execute(_DELETE_PRICING_SNAPSHOT.bindparams(sid=_PRICING_SNAPSHOT_ID))
    op.execute(_DELETE_MODEL.bindparams(id=_MODEL_ID))
