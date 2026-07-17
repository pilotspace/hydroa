"""model_catalog_db_seed — DB becomes the catalog source of truth for 4 static providers.

Revision ID: 9cdca76231c6
Revises: f94771e4aa7c
Create Date: 2026-07-16

catalog-db-seed TASK.md §3 (FROZEN @ v1), M3/M4/M8. Idempotently seeds 34 `models` rows +
34 matching `pricing_snapshots` rows spanning minimax(8)/openai(12)/bedrock(6)/vertex(8) —
the exact verified set from the research doc (source of record for every literal Decimal
price; not duplicated here beyond what this migration needs to insert). Mirrors
3fc2328e5e82_platform_tenant_seed.py's convention: raw `op.execute`/`sa.table` DML, NEVER
importing `gateway.*` domain/application code from `versions/` — the ONE exception is
`gateway.core.ids.uuid7`, a pure stdlib-only utility with zero app/domain coupling, already
imported by 3fc2328e5e82 and 1e66a2cb51a6 for the identical reason (uuid7 PKs generated
explicitly, never a DB-side default).

`models.id` PK collision -> `ON CONFLICT (id) DO NOTHING` (idempotent against a colliding id
from a prior partial/failed run — known-problem fix, TASK.md §5). `pricing_snapshots` rows
carry fresh uuid7() PKs each call — no natural conflict target; the append-only ledger simply
grows if this migration function were ever invoked twice outside alembic's own
once-per-revision tracking (never expected in normal operation).

dall-e-3 (Tin SCOPE-CUT 2026-07-16): pricing_unit="per_image" but unit_usd_per_unit=NULL —
bills $0 exactly as today (recorder.py's documented unit_price_missing_for_non_token_unit
path); its real price is a quality x size SKU matrix CatalogModel has no dimension to carry.
whisper-1/tts-1/tts-1-hd get real per_second/per_character unit prices (billing turns ON).

Safety rule (TASK.md §5): upgrade()/downgrade() run inside alembic's default per-migration
transaction — partial seed on failure never commits.
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
revision: str = "9cdca76231c6"
down_revision: str | None = "f94771e4aa7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    sa.column("cached_input_usd_per_token", sa.Numeric(20, 10)),
    sa.column("cache_creation_usd_per_token", sa.Numeric(20, 10)),
    sa.column("audio_prompt_usd_per_token", sa.Numeric(20, 10)),
    sa.column("audio_completion_usd_per_token", sa.Numeric(20, 10)),
    sa.column("audio_cached_usd_per_token", sa.Numeric(20, 10)),
    sa.column("pricing_unit", sa.Text()),
    sa.column("unit_usd_per_unit", sa.Numeric(20, 10)),
)

# Every literal Decimal price traces to /private/tmp/.../catalog-seed-data.md (research doc,
# fetch date 2026-07-16 unless noted) — TASK.md §3 seed-set breakdown. Keys not given default
# to: context_length=None, input_modalities="text", region="global", cached_input=None,
# cache_creation=None, audio_*=None, pricing_unit="per_token", unit_price=None.
_SEED: list[dict[str, object]] = [
    # --- MiniMax (8) — provider="minimax", modality="chat" -----------------
    {
        "id": "MiniMax-M3",
        "name": "MiniMax-M3",
        "context_length": 1_000_000,
        "provider": "minimax",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000012"),
        "cached_input": Decimal("0.00000006"),
        # cache_creation_usd_per_token unconfirmed for M3 specifically — left None
        # (TASK.md §1 Issue 6).
    },
    {
        "id": "MiniMax-M2.7",
        "name": "MiniMax-M2.7",
        "context_length": 204_800,
        "provider": "minimax",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000012"),
        "cached_input": Decimal("0.00000006"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2.7-highspeed",
        "name": "MiniMax-M2.7-highspeed",
        "context_length": 204_800,
        "provider": "minimax",
        "prompt": Decimal("0.0000006"),
        "completion": Decimal("0.0000024"),
        "cached_input": Decimal("0.00000006"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2.5",
        "name": "MiniMax-M2.5",
        "context_length": None,  # confirm-before-seeding flag (§1 Assumption) — price only
        "provider": "minimax",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000012"),
        "cached_input": Decimal("0.00000003"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2.5-highspeed",
        "name": "MiniMax-M2.5-highspeed",
        "context_length": None,
        "provider": "minimax",
        "prompt": Decimal("0.0000006"),
        "completion": Decimal("0.0000024"),
        "cached_input": Decimal("0.00000003"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2.1",
        "name": "MiniMax-M2.1",
        "context_length": None,
        "provider": "minimax",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000012"),
        "cached_input": Decimal("0.00000003"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2.1-highspeed",
        "name": "MiniMax-M2.1-highspeed",
        "context_length": None,
        "provider": "minimax",
        "prompt": Decimal("0.0000006"),
        "completion": Decimal("0.0000024"),
        "cached_input": Decimal("0.00000003"),
        "cache_creation": Decimal("0.000000375"),
    },
    {
        "id": "MiniMax-M2",
        "name": "MiniMax-M2",
        "context_length": None,
        "provider": "minimax",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000012"),
        "cached_input": Decimal("0.00000003"),
        "cache_creation": Decimal("0.000000375"),
    },
    # --- OpenAI (12) — provider="openai" ------------------------------------
    {
        "id": "text-embedding-3-small",
        "name": "text-embedding-3-small",
        "context_length": 8191,
        "provider": "openai",
        "modality": "embedding",
        "prompt": Decimal("0.00000002"),
        "completion": Decimal("0.0"),
    },
    {
        "id": "text-embedding-3-large",
        "name": "text-embedding-3-large",
        "context_length": 8191,
        "provider": "openai",
        "modality": "embedding",
        "prompt": Decimal("0.00000013"),
        "completion": Decimal("0.0"),
    },
    {
        "id": "dall-e-3",
        "name": "dall-e-3",
        "context_length": None,
        "provider": "openai",
        "modality": "image",
        "prompt": Decimal("0.0"),
        "completion": Decimal("0.0"),
        "pricing_unit": "per_image",
        # unit_price deliberately absent -> NULL (Tin SCOPE-CUT 2026-07-16, TASK.md M4).
    },
    {
        "id": "whisper-1",
        "name": "whisper-1",
        "context_length": None,
        "provider": "openai",
        "modality": "audio_stt",
        "input_modalities": "audio",
        "prompt": Decimal("0.0"),
        "completion": Decimal("0.0"),
        "pricing_unit": "per_second",
        "unit_price": Decimal("0.0001"),
    },
    {
        "id": "tts-1",
        "name": "tts-1",
        "context_length": None,
        "provider": "openai",
        "modality": "audio_tts",
        "prompt": Decimal("0.0"),
        "completion": Decimal("0.0"),
        "pricing_unit": "per_character",
        "unit_price": Decimal("0.000015"),
    },
    {
        "id": "tts-1-hd",
        "name": "tts-1-hd",
        "context_length": None,
        "provider": "openai",
        "modality": "audio_tts",
        "prompt": Decimal("0.0"),
        "completion": Decimal("0.0"),
        "pricing_unit": "per_character",
        "unit_price": Decimal("0.00003"),
    },
    {
        "id": "gpt-realtime",
        "name": "gpt-realtime",
        "context_length": None,
        "provider": "openai",
        "prompt": Decimal("0.000004"),
        "completion": Decimal("0.000016"),
        "cached_input": Decimal("0.0000004"),
        "audio_prompt": Decimal("0.000032"),
        "audio_completion": Decimal("0.000064"),
        "audio_cached": Decimal("0.0000004"),
    },
    {
        "id": "gpt-5.4",
        "name": "gpt-5.4",
        "context_length": 1_050_000,
        "provider": "openai",
        "prompt": Decimal("0.0000025"),
        "completion": Decimal("0.000015"),
        "cached_input": Decimal("0.00000025"),
    },
    {
        "id": "gpt-5.4-mini",
        "name": "gpt-5.4-mini",
        "context_length": 400_000,
        "provider": "openai",
        "prompt": Decimal("0.00000075"),
        "completion": Decimal("0.0000045"),
        "cached_input": Decimal("0.000000075"),
    },
    {
        "id": "gpt-realtime-2.1",
        "name": "gpt-realtime-2.1",
        "context_length": None,
        "provider": "openai",
        "prompt": Decimal("0.000004"),
        "completion": Decimal("0.000024"),
        "cached_input": Decimal("0.0000004"),
        "audio_prompt": Decimal("0.000032"),
        "audio_completion": Decimal("0.000064"),
        "audio_cached": Decimal("0.0000004"),
    },
    {
        "id": "gpt-4o-transcribe",
        "name": "gpt-4o-transcribe",
        "context_length": None,
        "provider": "openai",
        "modality": "audio_stt",
        "input_modalities": "audio",
        "prompt": Decimal("0.0000025"),
        "completion": Decimal("0.00001"),
    },
    {
        "id": "gpt-4o-mini-transcribe",
        "name": "gpt-4o-mini-transcribe",
        "context_length": None,
        "provider": "openai",
        "modality": "audio_stt",
        "input_modalities": "audio",
        "prompt": Decimal("0.00000125"),
        "completion": Decimal("0.000005"),
    },
    # --- Bedrock (6) — provider="bedrock", carried forward verbatim --------
    {
        "id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "name": "Claude 3.5 Sonnet v2 (Bedrock, US)",
        "context_length": 200_000,
        "provider": "bedrock",
        "input_modalities": "text,image",
        "region": "us",
        "prompt": Decimal("0.000003"),
        "completion": Decimal("0.000015"),
    },
    {
        "id": "eu.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "name": "Claude 3.5 Sonnet v2 (Bedrock, EU)",
        "context_length": 200_000,
        "provider": "bedrock",
        "input_modalities": "text,image",
        "region": "eu",
        "prompt": Decimal("0.000003"),
        "completion": Decimal("0.000015"),
    },
    {
        "id": "apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "name": "Claude 3.5 Sonnet v2 (Bedrock, APAC)",
        "context_length": 200_000,
        "provider": "bedrock",
        "input_modalities": "text,image",
        "region": "ap",
        "prompt": Decimal("0.000003"),
        "completion": Decimal("0.000015"),
    },
    {
        "id": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
        "name": "Claude 3.5 Haiku (Bedrock, US)",
        "context_length": 200_000,
        "provider": "bedrock",
        "region": "us",
        "prompt": Decimal("0.0000008"),
        "completion": Decimal("0.000004"),
    },
    {
        "id": "eu.anthropic.claude-3-5-haiku-20241022-v1:0",
        "name": "Claude 3.5 Haiku (Bedrock, EU)",
        "context_length": 200_000,
        "provider": "bedrock",
        "region": "eu",
        "prompt": Decimal("0.0000008"),
        "completion": Decimal("0.000004"),
    },
    {
        "id": "apac.anthropic.claude-3-5-haiku-20241022-v1:0",
        "name": "Claude 3.5 Haiku (Bedrock, APAC)",
        "context_length": 200_000,
        "provider": "bedrock",
        "region": "ap",
        "prompt": Decimal("0.0000008"),
        "completion": Decimal("0.000004"),
    },
    # --- Vertex (8) — provider="vertex" -------------------------------------
    {
        "id": "eu.gemini-2.5-flash",
        "name": "Gemini 2.5 Flash (Vertex AI, EU)",
        "context_length": 1_048_576,
        "provider": "vertex",
        "region": "eu",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000025"),
    },
    {
        "id": "ap.gemini-2.5-flash",
        "name": "Gemini 2.5 Flash (Vertex AI, AP)",
        "context_length": 1_048_576,
        "provider": "vertex",
        "region": "ap",
        "prompt": Decimal("0.0000003"),
        "completion": Decimal("0.0000025"),
    },
    {
        "id": "eu.gemini-2.5-pro",
        "name": "Gemini 2.5 Pro (Vertex AI, EU)",
        "context_length": 1_048_576,
        "provider": "vertex",
        "region": "eu",
        "prompt": Decimal("0.00000125"),
        "completion": Decimal("0.00001"),
    },
    {
        "id": "ap.gemini-2.5-pro",
        "name": "Gemini 2.5 Pro (Vertex AI, AP)",
        "context_length": 1_048_576,
        "provider": "vertex",
        "region": "ap",
        "prompt": Decimal("0.00000125"),
        "completion": Decimal("0.00001"),
    },
    {
        "id": "eu.gemini-2.5-flash-lite",
        "name": "Gemini 2.5 Flash-Lite (Vertex AI, EU)",
        "context_length": None,  # unconfirmed (§1 Assumption) — price only
        "provider": "vertex",
        "region": "eu",
        "prompt": Decimal("0.0000001"),
        "completion": Decimal("0.0000004"),
        "cached_input": Decimal("0.00000001"),
    },
    {
        "id": "ap.gemini-2.5-flash-lite",
        "name": "Gemini 2.5 Flash-Lite (Vertex AI, AP)",
        "context_length": None,
        "provider": "vertex",
        "region": "ap",
        "prompt": Decimal("0.0000001"),
        "completion": Decimal("0.0000004"),
        "cached_input": Decimal("0.00000001"),
    },
    {
        "id": "eu.gemini-embedding-001",
        "name": "Gemini Embedding 001 (Vertex AI, EU)",
        "context_length": None,
        "provider": "vertex",
        "modality": "embedding",
        "region": "eu",
        "prompt": Decimal("0.00000015"),
        "completion": Decimal("0.0"),
    },
    {
        "id": "ap.gemini-embedding-001",
        "name": "Gemini Embedding 001 (Vertex AI, AP)",
        "context_length": None,
        "provider": "vertex",
        "modality": "embedding",
        "region": "ap",
        "prompt": Decimal("0.00000015"),
        "completion": Decimal("0.0"),
    },
]

# The 34 seeded model ids — used by downgrade() to scope its deletes precisely.
SEEDED_MODEL_IDS: tuple[str, ...] = tuple(str(row["id"]) for row in _SEED)

assert len(SEEDED_MODEL_IDS) == 34, f"expected 34 seed rows, got {len(SEEDED_MODEL_IDS)}"
assert len(set(SEEDED_MODEL_IDS)) == 34, "duplicate id in _SEED"


def upgrade() -> None:
    """Idempotently insert the 34 models rows, then their matching pricing_snapshots rows."""
    model_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "context_length": row.get("context_length"),
            "modality": row.get("modality", "chat"),
            "provider": row["provider"],
            "input_modalities": row.get("input_modalities", "text"),
            "region": row.get("region", "global"),
        }
        for row in _SEED
    ]
    op.get_bind().execute(
        pg_insert(_MODELS).values(model_rows).on_conflict_do_nothing(index_elements=["id"])
    )

    snapshot_rows = [
        {
            "id": uuid7(),
            "model_id": row["id"],
            "prompt_usd_per_token": row["prompt"],
            "completion_usd_per_token": row["completion"],
            "cached_input_usd_per_token": row.get("cached_input"),
            "cache_creation_usd_per_token": row.get("cache_creation"),
            "audio_prompt_usd_per_token": row.get("audio_prompt"),
            "audio_completion_usd_per_token": row.get("audio_completion"),
            "audio_cached_usd_per_token": row.get("audio_cached"),
            "pricing_unit": row.get("pricing_unit", "per_token"),
            "unit_usd_per_unit": row.get("unit_price"),
        }
        for row in _SEED
    ]
    op.bulk_insert(_PRICING_SNAPSHOTS, snapshot_rows)


def downgrade() -> None:
    """Reverse in dependency order: pricing_snapshots (FK) before models."""
    op.execute(
        sa.text("DELETE FROM pricing_snapshots WHERE model_id IN :ids").bindparams(
            sa.bindparam("ids", value=SEEDED_MODEL_IDS, expanding=True)
        )
    )
    op.execute(
        sa.text("DELETE FROM models WHERE id IN :ids").bindparams(
            sa.bindparam("ids", value=SEEDED_MODEL_IDS, expanding=True)
        )
    )
