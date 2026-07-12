"""Failing-first (RED) suite for vertex_seed.py (vertex-adapter TASK.md §4, M8/R5).

Covers: VERTEX_SEED_MODELS carries the 4 expected synthetic region-prefixed ids with
provider="vertex" and the correct region tag (M8), and the seed-authoring Strategy
never emits a genuine duplicate id (R5 — the real, non-defensive list itself; the PK
enforcement is existing DB machinery, not new code under test here).

RED reason: gateway.catalog.infrastructure.vertex_seed does not exist yet -> ImportError.
"""

from __future__ import annotations


def test_M8_seed_rows_carry_region_prefixed_ids() -> None:
    from gateway.catalog.infrastructure.vertex_seed import VERTEX_SEED_MODELS

    by_id = {m.id: m for m in VERTEX_SEED_MODELS}
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
    from gateway.catalog.infrastructure.vertex_seed import VERTEX_SEED_MODELS

    for row in VERTEX_SEED_MODELS:
        assert row.prompt_usd_per_token > 0, f"{row.id} must have a positive prompt price"
        assert row.completion_usd_per_token > 0, f"{row.id} must have a positive completion price"
        assert row.context_length and row.context_length > 0


def test_R5_seed_ids_are_unique() -> None:
    """R5: the real (non-defensive) seed-authoring Strategy never emits a duplicate id —
    proven directly against the shipped list (the PK enforcement itself is existing DB
    machinery, not new code this task introduces)."""
    from gateway.catalog.infrastructure.vertex_seed import VERTEX_SEED_MODELS

    ids = [m.id for m in VERTEX_SEED_MODELS]
    assert len(ids) == len(set(ids)), f"duplicate ids in VERTEX_SEED_MODELS: {ids}"


async def test_M8_seed_rows_sync_into_catalog() -> None:
    """CompositeCatalogSource (chained UNCHANGED) yields every VERTEX_SEED_MODELS entry
    through list_models() — proves the static-seed chaining pattern (mirrors
    bedrock_seed.py's own precedent), not just the raw list shape."""
    from collections.abc import AsyncIterator

    from gateway.catalog.domain.entities import CatalogModel
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.vertex_seed import VERTEX_SEED_MODELS

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
        static_models=list(VERTEX_SEED_MODELS),
    )
    seen = [m async for m in source.list_models()]
    ids = {m.id for m in seen}
    for m in VERTEX_SEED_MODELS:
        assert m.id in ids, f"{m.id} must survive CompositeCatalogSource.list_models()"
