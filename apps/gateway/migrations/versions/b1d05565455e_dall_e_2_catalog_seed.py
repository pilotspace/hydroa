"""dall_e_2_catalog_seed — seed the dall-e-2 catalog row (image-edits-variations §3).

Revision ID: b1d05565455e
Revises: b8c1f4a2d6e9
Create Date: 2026-07-24

image-edits-variations TASK.md §3 (FROZEN @ v1), Must bullet 7. Idempotently seeds ONE
`models` row + ONE matching `pricing_snapshots` row for `dall-e-2` — the edit/variation-
CAPABLE image model (mirrors the existing `dall-e-3` SCOPE-CUT precedent in
9cdca76231c6_model_catalog_db_seed.py exactly): `modality="image"`, `provider="openai"`,
`input_modalities="text,image"` (dall-e-3 is `"text"` only — generations-only), `pricing_
unit="per_image"`, `unit_usd_per_unit=NULL` (SCOPE-CUT — bills $0 exactly as today, same
`unit_price_missing_for_non_token_unit` documented path dall-e-3 already exercises; its
real price is a size x quality SKU matrix CatalogModel has no dimension to carry).

`dall-e-2` is NOT on `tests/catalog_db_seed/test_catalog_db_seed_migration.py::_UNVERIFIED_
IDS` (that list forbids `gpt-image-1` specifically) and carries well-established,
non-controversial OpenAI pricing/capability data — assumption #1 in PLAN.md §1.

`models.id` PK collision -> `ON CONFLICT (id) DO NOTHING` (idempotent, same convention as
9cdca76231c6). `pricing_snapshots` gets a fresh uuid7() PK -- no natural conflict target,
matching the sibling migration's append-only-ledger convention.

Safety rule (TASK.md §5): upgrade()/downgrade() run inside alembic's default
per-migration transaction -- a partial seed on failure never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gateway.core.ids import uuid7

# revision identifiers, used by Alembic.
revision: str = "b1d05565455e"
down_revision: str | None = "b8c1f4a2d6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_ID = "dall-e-2"

_MODELS = sa.table(
    "models",
    sa.column("id", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("context_length", sa.Integer()),
    sa.column("modality", sa.Text()),
    sa.column("provider", sa.Text()),
    sa.column("input_modalities", sa.Text()),
    sa.column("region", sa.Text()),
)

_PRICING_SNAPSHOTS = sa.table(
    "pricing_snapshots",
    sa.column("id", PGUUID(as_uuid=True)),
    sa.column("model_id", sa.Text()),
    sa.column("prompt_usd_per_token", sa.Numeric(20, 10)),
    sa.column("completion_usd_per_token", sa.Numeric(20, 10)),
    sa.column("pricing_unit", sa.Text()),
    sa.column("unit_usd_per_unit", sa.Numeric(20, 10)),
)


def upgrade() -> None:
    """Idempotently insert the dall-e-2 models row, then its pricing_snapshots row."""
    op.get_bind().execute(
        pg_insert(_MODELS)
        .values(
            {
                "id": _MODEL_ID,
                "name": _MODEL_ID,
                "context_length": None,
                "modality": "image",
                "provider": "openai",
                "input_modalities": "text,image",
                "region": "global",
            }
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )

    op.bulk_insert(
        _PRICING_SNAPSHOTS,
        [
            {
                "id": uuid7(),
                "model_id": _MODEL_ID,
                "prompt_usd_per_token": Decimal("0.0"),
                "completion_usd_per_token": Decimal("0.0"),
                "pricing_unit": "per_image",
                # unit_price deliberately absent -> NULL (SCOPE-CUT, mirrors dall-e-3).
                "unit_usd_per_unit": None,
            }
        ],
    )


def downgrade() -> None:
    """Reverse in dependency order: pricing_snapshots (FK) before models."""
    op.execute(
        sa.text("DELETE FROM pricing_snapshots WHERE model_id = :id").bindparams(id=_MODEL_ID)
    )
    op.execute(sa.text("DELETE FROM models WHERE id = :id").bindparams(id=_MODEL_ID))
