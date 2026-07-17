"""Failing-first (RED) suite for catalog-db-seed (TASK.md §4, contract FROZEN @ v1).

One test per §2 SCENARIOS that does NOT require a real Alembic migration run — the
migration-backed scenarios (fresh-DB-after-migration, downgrade reversibility, unverified
ids absent) live in test_catalog_db_seed_migration.py (real Alembic harness, per the
platform-tenant-seed precedent — Base.metadata.create_all never runs data migrations).

Run only this suite:
  cd apps/gateway && GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test \
    .venv/bin/python -m pytest tests/catalog_db_seed/test_catalog_db_seed.py -q --no-cov -p no:randomly
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.infrastructure.orm import ModelRow, PricingSnapshotRow
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

# ===========================================================================
# Scenario: dataclass gains 3 additive fields   (M1)
# ===========================================================================


def test_catalog_model_gains_3_additive_fields_with_defaults() -> None:
    """Every existing call site (positional-required + no new kwargs) still constructs,
    and the 3 new fields default to (None, "per_token", None) per §3 CONTRACT."""
    field_names = {f.name for f in dataclasses.fields(CatalogModel)}
    assert {"cache_creation_usd_per_token", "pricing_unit", "unit_usd_per_unit"} <= field_names

    model = CatalogModel(
        id="m1",
        name="M1",
        context_length=None,
        prompt_usd_per_token=Decimal("0.000001"),
        completion_usd_per_token=Decimal("0.000002"),
    )
    assert model.cache_creation_usd_per_token is None
    assert model.pricing_unit == "per_token"
    assert model.unit_usd_per_unit is None


def test_catalog_model_round_trips_all_3_new_fields_when_supplied() -> None:
    """A caller supplying all 3 new fields explicitly round-trips them unchanged, exact
    Decimal precision (no float rounding)."""
    model = CatalogModel(
        id="m2",
        name="M2",
        context_length=None,
        prompt_usd_per_token=Decimal("0.0"),
        completion_usd_per_token=Decimal("0.0"),
        cache_creation_usd_per_token=Decimal("0.000000375"),
        pricing_unit="per_image",
        unit_usd_per_unit=Decimal("0.04"),
    )
    assert model.cache_creation_usd_per_token == Decimal("0.000000375")
    assert model.pricing_unit == "per_image"
    assert model.unit_usd_per_unit == Decimal("0.04")
    # Exact-Decimal pin (money-is-Decimal rule) — a float round-trip would NOT
    # equal the string-constructed Decimal bit-for-bit.
    unit_price = model.unit_usd_per_unit
    assert unit_price is not None
    assert Decimal(unit_price) == Decimal("0.04")


# ===========================================================================
# Scenario: _insert_snapshot persists the 3 new fields   (M2)
# ===========================================================================


async def test_insert_snapshot_persists_three_new_fields_exact_decimal(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="snap-new-fields",
        name="Snap New Fields",
        context_length=None,
        prompt_usd_per_token=Decimal("0.0"),
        completion_usd_per_token=Decimal("0.0"),
        provider="openai",
        modality="image",
        cache_creation_usd_per_token=Decimal("0.000000375"),
        pricing_unit="per_image",
        unit_usd_per_unit=Decimal("0.04"),
    )
    await repo.sync_catalog([model])

    row = (
        await db_session.execute(
            select(PricingSnapshotRow).where(PricingSnapshotRow.model_id == "snap-new-fields")
        )
    ).scalar_one()
    assert Decimal(str(row.cache_creation_usd_per_token)) == Decimal("0.000000375")
    assert row.pricing_unit == "per_image"
    assert Decimal(str(row.unit_usd_per_unit)) == Decimal("0.04")


# ===========================================================================
# Scenario: pricing_unit default never violates the NOT NULL column   (R4)
# ===========================================================================


async def test_pricing_unit_defaults_to_per_token_never_null(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="no-explicit-pricing-unit",
        name="No Explicit Pricing Unit",
        context_length=None,
        prompt_usd_per_token=Decimal("0.000001"),
        completion_usd_per_token=Decimal("0.000002"),
        provider="openrouter",
        # pricing_unit NOT provided — must fall back to "per_token", never NULL.
    )
    await repo.sync_catalog([model])  # must not raise IntegrityError

    row = (
        await db_session.execute(
            select(PricingSnapshotRow).where(
                PricingSnapshotRow.model_id == "no-explicit-pricing-unit"
            )
        )
    ).scalar_one()
    assert row.pricing_unit == "per_token"


# ===========================================================================
# Scenario: provider-scoped sync deactivates only that provider's missing rows   (M5, R2)
# ===========================================================================


async def test_provider_scoped_sync_deactivates_only_that_providers_missing_rows(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)

    minimax_current = CatalogModel(
        id="MiniMax-M3",
        name="MiniMax-M3",
        context_length=1_000_000,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000012"),
        provider="minimax",
    )
    minimax_legacy = CatalogModel(
        id="MiniMax-M2.7",
        name="MiniMax-M2.7",
        context_length=204_800,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000012"),
        provider="minimax",
    )
    bedrock_row = CatalogModel(
        id="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        name="Claude 3.5 Haiku (Bedrock, US)",
        context_length=200_000,
        prompt_usd_per_token=Decimal("0.0000008"),
        completion_usd_per_token=Decimal("0.000004"),
        provider="bedrock",
    )
    vertex_row = CatalogModel(
        id="eu.gemini-2.5-flash",
        name="Gemini 2.5 Flash (Vertex AI, EU)",
        context_length=1_048_576,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000025"),
        provider="vertex",
    )
    openai_row = CatalogModel(
        id="whisper-1",
        name="whisper-1",
        context_length=None,
        prompt_usd_per_token=Decimal("0.0"),
        completion_usd_per_token=Decimal("0.0"),
        provider="openai",
    )

    # Initial sync: all 5 providers' rows land active.
    await repo.sync_catalog([minimax_current, minimax_legacy, bedrock_row, vertex_row, openai_row])

    # Second sync: ONLY the current MiniMax model is present upstream.
    await repo.sync_catalog([minimax_current])

    rows = (
        await db_session.execute(
            select(ModelRow.id, ModelRow.active, ModelRow.provider).where(
                ModelRow.id.in_(
                    [
                        "MiniMax-M3",
                        "MiniMax-M2.7",
                        "us.anthropic.claude-3-5-haiku-20241022-v1:0",
                        "eu.gemini-2.5-flash",
                        "whisper-1",
                    ]
                )
            )
        )
    ).all()
    by_id = {r.id: r.active for r in rows}

    assert by_id["MiniMax-M3"] is True, "the still-current minimax row must stay active"
    assert by_id["MiniMax-M2.7"] is False, (
        "absent-from-batch minimax row must be deactivated (same provider)"
    )
    assert by_id["us.anthropic.claude-3-5-haiku-20241022-v1:0"] is True, (
        "CROSS_PROVIDER_DEACTIVATION: a minimax-only sync must never touch bedrock rows"
    )
    assert by_id["eu.gemini-2.5-flash"] is True, (
        "CROSS_PROVIDER_DEACTIVATION: a minimax-only sync must never touch vertex rows"
    )
    assert by_id["whisper-1"] is True, (
        "CROSS_PROVIDER_DEACTIVATION: a minimax-only sync must never touch openai rows"
    )


# ===========================================================================
# Scenario: an empty batch carries no provider signal -> no-op   (M5, R3)
# ===========================================================================


async def test_empty_batch_no_provider_signal_is_noop(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    seed_models = [
        CatalogModel(
            id="empty-batch-minimax",
            name="Empty Batch Minimax",
            context_length=None,
            prompt_usd_per_token=Decimal("0.0000003"),
            completion_usd_per_token=Decimal("0.0000012"),
            provider="minimax",
        ),
        CatalogModel(
            id="empty-batch-bedrock",
            name="Empty Batch Bedrock",
            context_length=None,
            prompt_usd_per_token=Decimal("0.000003"),
            completion_usd_per_token=Decimal("0.000015"),
            provider="bedrock",
        ),
    ]
    await repo.sync_catalog(seed_models)

    # AMBIGUOUS_EMPTY_BATCH_DEACTIVATION: neither call below may deactivate anything.
    await repo.sync_catalog([], embedding_models=None)
    await repo.sync_catalog([], embedding_models=[])

    rows = (
        await db_session.execute(
            select(ModelRow.id, ModelRow.active).where(
                ModelRow.id.in_(["empty-batch-minimax", "empty-batch-bedrock"])
            )
        )
    ).all()
    by_id = {r.id: r.active for r in rows}
    assert by_id["empty-batch-minimax"] is True
    assert by_id["empty-batch-bedrock"] is True


# ===========================================================================
# Scenario: static_models removed, 5 files deleted, boot unaffected   (M6, M7)
# ===========================================================================


async def test_static_models_removed_boot_unaffected(app: Any) -> None:
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource

    source = app.state.catalog_source
    assert isinstance(source, CompositeCatalogSource)
    assert isinstance(source._primary, OpenRouterCatalogSource)  # pyright: ignore[reportPrivateUsage]
    assert list(source._static_models) == [], (  # pyright: ignore[reportPrivateUsage]
        "CompositeCatalogSource must be wired with NO static_models — the DB migration is "
        "now the sole source of truth for these rows (decision 1)"
    )


def test_deleted_seed_modules_raise_module_not_found() -> None:
    import importlib

    for mod in (
        "gateway.catalog.infrastructure.minimax_seed",
        "gateway.catalog.infrastructure.gpt_realtime_seed",
        "gateway.catalog.infrastructure.bedrock_seed",
        "gateway.catalog.infrastructure.vertex_seed",
        "gateway.catalog.infrastructure.openai_seed",
    ):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{mod} must no longer exist (TASK.md M7)")
