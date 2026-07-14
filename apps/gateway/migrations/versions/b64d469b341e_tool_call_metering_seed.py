"""tool_call_metering_seed — seed the synthetic mcp_tool_call catalog row + its
pricing snapshot (tool-call-metering TASK.md §3 Schema, FROZEN @ v1, M4/M5/M6).

Revision ID: b64d469b341e
Revises: 5c8f3a1e9b2d
Create Date: 2026-07-14

Data-only seed migration (no DDL) — inserts exactly ONE `models` row and exactly ONE
`pricing_snapshots` row for the sentinel model_id `mcp_tool_call`, the FIRST real use
of the dormant non-token pricing branch (unit_usd_per_unit) recorder.py already carries.

  models:
    id='mcp_tool_call', name='MCP Tool Call', active=false, modality='tool_call',
    provider='hydroa', region='global'
    -- active=false (NOT the ORM's own `true` default) excludes this row from
       GET /catalog/models' tenant-facing listing (list_active_models_with_markup's
       WHERE active=true filter) while remaining fully resolvable by
       _fetch_latest_pricing/resolve_markup_pct (both key directly on the model_id
       string, independent of models.active) — M5.
    -- modality='tool_call' (NOT the ORM's own 'chat' default) exempts this row from
       every future catalog sync's stale-deactivation sweep (repository.py's
       WHERE modality='chat' filter) — M6.
    Both flags are load-bearing (§1 M5/M6), not incidental — see TASK.md "Known-problem
    fixes" traps: relying on either column default here would silently leak the row into
    the tenant catalog listing or get it deactivated by the next OpenRouter/MiniMax/
    Bedrock/Vertex sync pass.

  pricing_snapshots:
    id=6cb58c1d-16c1-53d1-a000-c60c06024935 (a FIXED uuid5, deterministic across every
      run of this migration — NOT gen_random_uuid(), so ON CONFLICT (id) DO NOTHING is
      a real idempotency guard rather than a no-op that always inserts a fresh row),
    model_id='mcp_tool_call', pricing_unit='per_tool_call',
    unit_usd_per_unit=0.0025  (= $2.50 / 1k tool calls, DECIDED by Tin at freeze
      2026-07-14 — TASK.md §3 "Open decisions for freeze"),
    prompt_usd_per_token=0, completion_usd_per_token=0 (NOT NULL columns on this table;
      inert for a non-token pricing_unit — the per_token branch never reads this row).

Safety rule (TASK.md §5): the models row insert and the pricing_snapshots row insert
happen in the SAME migration (one alembic transaction), models row FIRST — a partial
seed (models row present, pricing row absent) must never be observable, since
pricing_snapshots.model_id carries a real FK to models.id (ondelete=RESTRICT) and that
combination would silently produce the M11 "$0 + warning" path for every tool call
until manually repaired.

Idempotent: safe to run twice.
  - models: ON CONFLICT (id) DO NOTHING (id='mcp_tool_call' is already a stable,
    naturally-unique conflict target).
  - pricing_snapshots: ON CONFLICT (id) DO NOTHING against the FIXED uuid5 above.

downgrade(): deletes the pricing_snapshots row FIRST (its FK to models.id would
otherwise block deleting the models row), then the models row. Safe — this is a
first-party seed row, not tenant data; no other table's FK targets pricing_snapshots.id.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b64d469b341e"
down_revision: str | None = "5c8f3a1e9b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_ID = "mcp_tool_call"
# Fixed uuid5(NAMESPACE_URL, "hydroa:pricing_snapshots:mcp_tool_call:v1") — deterministic,
# never regenerated at migration-run time (see module docstring "Idempotent").
_PRICING_SNAPSHOT_ID = "6cb58c1d-16c1-53d1-a000-c60c06024935"
_UNIT_USD_PER_UNIT = Decimal("0.0025")  # $2.50 / 1k tool calls (Tin, freeze 2026-07-14)

# Bound-parameter statements throughout (no f-string SQL) — every value here is a
# fixed module constant, never external input, but parameterizing keeps this file
# clean against S608/ruff and is the least-surprising idiom regardless.
_INSERT_MODEL = sa.text(
    "INSERT INTO models (id, name, context_length, active, modality, provider, region)"
    " VALUES (:id, 'MCP Tool Call', NULL, false, 'tool_call', 'hydroa', 'global')"
    " ON CONFLICT (id) DO NOTHING"
)
_INSERT_PRICING_SNAPSHOT = sa.text(
    "INSERT INTO pricing_snapshots"
    " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
    "  pricing_unit, unit_usd_per_unit)"
    " VALUES (CAST(:sid AS uuid), :id, 0, 0, 'per_tool_call', :price)"
    " ON CONFLICT (id) DO NOTHING"
)
_DELETE_PRICING_SNAPSHOT = sa.text("DELETE FROM pricing_snapshots WHERE id = CAST(:sid AS uuid)")
_DELETE_MODEL = sa.text("DELETE FROM models WHERE id = :id")


def upgrade() -> None:
    """Seed the mcp_tool_call models row, then its pricing_snapshots row."""
    # --- models (FIRST — pricing_snapshots.model_id FK requires it to exist) ---
    op.execute(_INSERT_MODEL.bindparams(id=_MODEL_ID))

    # --- pricing_snapshots ---
    op.execute(
        _INSERT_PRICING_SNAPSHOT.bindparams(
            sid=_PRICING_SNAPSHOT_ID, id=_MODEL_ID, price=_UNIT_USD_PER_UNIT
        )
    )


def downgrade() -> None:
    """Remove the seeded pricing_snapshots row FIRST (FK), then the models row."""
    op.execute(_DELETE_PRICING_SNAPSHOT.bindparams(sid=_PRICING_SNAPSHOT_ID))
    op.execute(_DELETE_MODEL.bindparams(id=_MODEL_ID))
