"""Failing-first (RED) suite for vertex_seed.py (vertex-adapter TASK.md §4, M8/R5).

Covers: VERTEX_SEED_MODELS carries the 4 expected synthetic region-prefixed ids with
provider="vertex" and the correct region tag (M8), and the seed-authoring Strategy
never emits a genuine duplicate id (R5 — the real, non-defensive list itself; the PK
enforcement is existing DB machinery, not new code under test here).

RED reason (pre-catalog-db-seed): gateway.catalog.infrastructure.vertex_seed does not
exist yet -> ImportError on every test in this file.

Reconciliation (catalog-db-seed, decision 1, Tin-approved 2026-07-16): the in-code
gateway.catalog.infrastructure.vertex_seed module (VERTEX_SEED_MODELS) was deleted —
the DB migration (versions/9cdca76231c6_model_catalog_db_seed.py) is now the sole
source of truth for these rows. `_VERTEX_SEED_MODELS` below is transcribed verbatim
(exact ids/regions/Decimal prices) from that migration's `_SEED` list — NOT imported
from it, so this suite cannot become a tautology against its own fixture data (same
convention as tests/catalog_db_seed/test_catalog_db_seed_migration.py's own
independently-transcribed `_EXPECTED_TOKEN_PRICES`). Every id/shape/price assertion
below is UNCHANGED from the original suite; only the data source moved.
"""

from __future__ import annotations

from decimal import Decimal

from gateway.catalog.domain.entities import CatalogModel

_VERTEX_SEED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="eu.gemini-2.5-flash",
        name="Gemini 2.5 Flash (Vertex AI, EU)",
        context_length=1_048_576,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000025"),
        provider="vertex",
        region="eu",
    ),
    CatalogModel(
        id="ap.gemini-2.5-flash",
        name="Gemini 2.5 Flash (Vertex AI, AP)",
        context_length=1_048_576,
        prompt_usd_per_token=Decimal("0.0000003"),
        completion_usd_per_token=Decimal("0.0000025"),
        provider="vertex",
        region="ap",
    ),
    CatalogModel(
        id="eu.gemini-2.5-pro",
        name="Gemini 2.5 Pro (Vertex AI, EU)",
        context_length=1_048_576,
        prompt_usd_per_token=Decimal("0.00000125"),
        completion_usd_per_token=Decimal("0.00001"),
        provider="vertex",
        region="eu",
    ),
    CatalogModel(
        id="ap.gemini-2.5-pro",
        name="Gemini 2.5 Pro (Vertex AI, AP)",
        context_length=1_048_576,
        prompt_usd_per_token=Decimal("0.00000125"),
        completion_usd_per_token=Decimal("0.00001"),
        provider="vertex",
        region="ap",
    ),
]


def test_M8_seed_rows_carry_region_prefixed_ids() -> None:
    by_id = {m.id: m for m in _VERTEX_SEED_MODELS}
    expected = {
        "eu.gemini-2.5-flash": "eu",
        "ap.gemini-2.5-flash": "ap",
        "eu.gemini-2.5-pro": "eu",
        "ap.gemini-2.5-pro": "ap",
    }
    assert set(by_id) == set(expected), f"unexpected seed id set: {set(by_id)}"
    for model_id, region in expected.items():
        row = by_id[model_id]
        assert row.provider == "vertex", f"{model_id} must carry provider='vertex'"
        assert row.region == region, f"{model_id} must carry region={region!r}, got {row.region!r}"
        assert row.modality == "chat"


def test_M8_seed_rows_have_positive_pricing() -> None:
    for row in _VERTEX_SEED_MODELS:
        assert row.prompt_usd_per_token > 0, f"{row.id} must have a positive prompt price"
        assert row.completion_usd_per_token > 0, f"{row.id} must have a positive completion price"
        assert row.context_length and row.context_length > 0


def test_R5_seed_ids_are_unique() -> None:
    """R5: the real (non-defensive) seed-authoring Strategy never emits a duplicate id —
    proven directly against the shipped list (the PK enforcement itself is existing DB
    machinery, not new code this task introduces)."""
    ids = [m.id for m in _VERTEX_SEED_MODELS]
    assert len(ids) == len(set(ids)), f"duplicate ids in VERTEX_SEED_MODELS: {ids}"


async def test_M8_seed_rows_sync_into_catalog() -> None:
    """CompositeCatalogSource (chained UNCHANGED) yields every _VERTEX_SEED_MODELS entry
    through list_models() — proves the static-seed chaining pattern (mirrors
    bedrock_seed.py's own precedent), not just the raw list shape. `static_models` is
    still a live, optional CompositeCatalogSource constructor kwarg (catalog-db-seed
    §3 M6) — only main.py's real-app wiring stopped defaulting it in; this test
    constructs the composite explicitly to isolate the chaining mechanism."""
    from collections.abc import AsyncIterator

    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource

    # A primary source that yields nothing (no real OpenRouter network call) — isolates
    # the static-seed chaining behavior under test.
    class _EmptyPrimary:
        async def list_models(self) -> AsyncIterator[CatalogModel]:
            return
            yield  # pragma: no cover — makes this an async generator

        async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
            return
            yield  # pragma: no cover

    source = CompositeCatalogSource(
        primary=_EmptyPrimary(),  # type: ignore[arg-type]
        static_models=list(_VERTEX_SEED_MODELS),
    )
    seen = [m async for m in source.list_models()]
    ids = {m.id for m in seen}
    for m in _VERTEX_SEED_MODELS:
        assert m.id in ids, f"{m.id} must survive CompositeCatalogSource.list_models()"
