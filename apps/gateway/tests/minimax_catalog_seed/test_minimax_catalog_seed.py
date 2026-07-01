"""Failing-first (RED) suite for minimax-catalog-seed (TASK.md §4, contract FROZEN @ v1).

One test per §2 SCENARIOS (the "existing suite stays byte-identical" scenario is proven by
running tests/catalog/test_model_catalog.py unmodified at BUILD/VERIFY, not a new test here).

Run only this suite:
  cd apps/gateway && uv run pytest tests/minimax_catalog_seed/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

SYNC = "/internal/catalog/sync"
MODELS = "/v1/models"
ADMIN_CATALOG_MODELS = "/admin/catalog/models"

_MINIMAX_IDS = ("MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed")


class _FakeSource:
    """Minimal CatalogSource-shaped double — chat + embedding model lists are configurable."""

    def __init__(
        self,
        chat_models: list[CatalogModel] | None = None,
        embedding_models: list[CatalogModel] | None = None,
    ) -> None:
        self.chat_models = chat_models or []
        self.embedding_models = embedding_models or []

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        for model in self.chat_models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        for model in self.embedding_models:
            yield model


def _install_composite(app: Any, fake_primary: _FakeSource) -> None:
    """Install a CompositeCatalogSource(fake_primary, MINIMAX_SEED_MODELS) on app.state."""
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.minimax_seed import MINIMAX_SEED_MODELS

    app.state.catalog_source = CompositeCatalogSource(
        primary=fake_primary, static_models=MINIMAX_SEED_MODELS
    )


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str = "MinimaxCatalogTest",
    email: str = "mcs@example.io",
    password: str = "minimax catalog seed test",
) -> str:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token: str = login.json()["access_token"]
    return token


# ===========================================================================
# Scenario: MINIMAX_SEED_MODELS lists 3 correctly-priced chat entries
# ===========================================================================


def test_minimax_seed_models_has_three_correctly_priced_chat_entries() -> None:
    from gateway.catalog.infrastructure.minimax_seed import MINIMAX_SEED_MODELS

    by_id = {m.id: m for m in MINIMAX_SEED_MODELS}
    assert set(by_id) == set(_MINIMAX_IDS), f"expected exactly {_MINIMAX_IDS}, got {set(by_id)}"

    for model in MINIMAX_SEED_MODELS:
        assert model.modality == "chat", f"{model.id}: modality must be 'chat'"
        assert model.provider == "minimax", f"{model.id}: provider must be 'minimax'"
        assert model.input_modalities == "text", f"{model.id}: input_modalities must be 'text'"

    m3 = by_id["MiniMax-M3"]
    assert m3.context_length == 1_000_000
    assert m3.prompt_usd_per_token == pytest.approx(0.0000003)
    assert m3.completion_usd_per_token == pytest.approx(0.0000012)

    m27 = by_id["MiniMax-M2.7"]
    assert m27.context_length == 204_800
    assert m27.prompt_usd_per_token == pytest.approx(0.0000003)
    assert m27.completion_usd_per_token == pytest.approx(0.0000012)

    m27hs = by_id["MiniMax-M2.7-highspeed"]
    assert m27hs.context_length == 204_800
    assert m27hs.prompt_usd_per_token == pytest.approx(0.0000006)
    assert m27hs.completion_usd_per_token == pytest.approx(0.0000024)


# ===========================================================================
# Scenario: CompositeCatalogSource chains OpenRouter chat models with the MiniMax seed
# ===========================================================================


async def test_composite_source_chains_primary_then_minimax_seed() -> None:
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.minimax_seed import MINIMAX_SEED_MODELS

    fake_primary = _FakeSource(
        chat_models=[
            CatalogModel(
                id="anthropic/claude-opus-4",
                name="Claude Opus 4",
                context_length=200_000,
                prompt_usd_per_token=15e-6,
                completion_usd_per_token=75e-6,
                provider="openrouter",
            ),
            CatalogModel(
                id="anthropic/claude-sonnet-4",
                name="Claude Sonnet 4",
                context_length=200_000,
                prompt_usd_per_token=3e-6,
                completion_usd_per_token=15e-6,
                provider="openrouter",
            ),
        ]
    )
    composite = CompositeCatalogSource(primary=fake_primary, static_models=MINIMAX_SEED_MODELS)

    yielded = [model async for model in composite.list_models()]

    assert len(yielded) == 5, f"expected 2 OpenRouter + 3 MiniMax = 5, got {len(yielded)}"
    assert [m.id for m in yielded[:2]] == [
        "anthropic/claude-opus-4",
        "anthropic/claude-sonnet-4",
    ], "OpenRouter models must be yielded first"
    assert [m.id for m in yielded[2:]] == list(_MINIMAX_IDS), (
        "MiniMax seed models must follow, in MINIMAX_SEED_MODELS order"
    )


# ===========================================================================
# Scenario: CompositeCatalogSource delegates embeddings to the primary source only
# ===========================================================================


async def test_composite_source_embeddings_delegate_to_primary_only() -> None:
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.minimax_seed import MINIMAX_SEED_MODELS

    fake_primary = _FakeSource(
        embedding_models=[
            CatalogModel(
                id="openai/text-embedding-3-small",
                name="text-embedding-3-small",
                context_length=8191,
                prompt_usd_per_token=2e-8,
                completion_usd_per_token=0.0,
                modality="embedding",
                provider="openrouter",
            ),
        ]
    )
    composite = CompositeCatalogSource(primary=fake_primary, static_models=MINIMAX_SEED_MODELS)

    yielded = [model async for model in composite.list_embedding_models()]

    assert len(yielded) == 1
    assert yielded[0].id == "openai/text-embedding-3-small"
    assert not any(m.id in _MINIMAX_IDS for m in yielded), (
        "MiniMax must never contribute embedding rows"
    )


# ===========================================================================
# Scenario: main.py wires the composite source in place of the bare OpenRouter source
# ===========================================================================


async def test_main_wires_composite_catalog_source(app: Any) -> None:
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource
    from gateway.catalog.infrastructure.openrouter_source import OpenRouterCatalogSource

    source = app.state.catalog_source
    assert isinstance(source, CompositeCatalogSource), (
        f"app.state.catalog_source must be a CompositeCatalogSource, got {type(source)!r}"
    )
    assert isinstance(source._primary, OpenRouterCatalogSource), (  # pyright: ignore[reportPrivateUsage]
        "the wrapped primary source must still be OpenRouterCatalogSource"
    )
    assert [
        m.id for m in source._static_models  # pyright: ignore[reportPrivateUsage]
    ] == list(_MINIMAX_IDS), "the wrapped static_models must be MINIMAX_SEED_MODELS"


# ===========================================================================
# Scenario: _upsert_model persists provider/input_modalities on first insert
# ===========================================================================


async def test_upsert_model_persists_provider_on_insert(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="MiniMax-M3",
        name="MiniMax-M3",
        context_length=1_000_000,
        prompt_usd_per_token=0.0000003,
        completion_usd_per_token=0.0000012,
        modality="chat",
        provider="minimax",
        input_modalities="text",
    )

    await repo.sync_catalog([model])

    row = (
        await db_session.execute(
            text("SELECT provider, input_modalities FROM models WHERE id = :mid"),
            {"mid": "MiniMax-M3"},
        )
    ).one()
    assert row.provider == "minimax", (
        f"provider must be 'minimax' on INSERT, not the column default; got {row.provider!r}"
    )
    assert row.input_modalities == "text"


# ===========================================================================
# Scenario: _upsert_model persists provider/input_modalities on conflict-update
# ===========================================================================


async def test_upsert_model_persists_provider_on_conflict_update(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    original = CatalogModel(
        id="shared-id-for-conflict-test",
        name="Original",
        context_length=1000,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        modality="chat",
        provider="openrouter",
        input_modalities="text",
    )
    await repo.sync_catalog([original])

    updated = CatalogModel(
        id="shared-id-for-conflict-test",
        name="Original",
        context_length=1000,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        modality="chat",
        provider="minimax",
        input_modalities="text",
    )
    await repo.sync_catalog([updated])

    row = (
        await db_session.execute(
            text("SELECT provider FROM models WHERE id = :mid"),
            {"mid": "shared-id-for-conflict-test"},
        )
    ).one()
    assert row.provider == "minimax", (
        f"provider must be updated to 'minimax' on conflict-update; got {row.provider!r}"
    )


# ===========================================================================
# Scenario: OpenRouter rows keep explicit provider="openrouter" after the fix
# ===========================================================================


async def test_openrouter_rows_keep_explicit_provider_after_fix(db_session: AsyncSession) -> None:
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="anthropic/claude-opus-4",
        name="Claude Opus 4",
        context_length=200_000,
        prompt_usd_per_token=15e-6,
        completion_usd_per_token=75e-6,
        modality="chat",
        provider="openrouter",
        input_modalities="text",
    )

    await repo.sync_catalog([model])

    row = (
        await db_session.execute(
            text("SELECT provider FROM models WHERE id = :mid"),
            {"mid": "anthropic/claude-opus-4"},
        )
    ).one()
    assert row.provider == "openrouter"


# ===========================================================================
# Scenario: a full sync persists the MiniMax rows as active with correct fields
# ===========================================================================


async def test_full_sync_persists_minimax_rows_active_with_correct_fields(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_composite(app, _FakeSource())

    resp = await client.post(SYNC)
    assert resp.status_code == 200, resp.text

    rows = (
        await db_session.execute(
            text(
                "SELECT id, active, provider, modality, input_modalities, context_length "
                "FROM models WHERE id = ANY(:ids)"
            ),
            {"ids": list(_MINIMAX_IDS)},
        )
    ).all()
    by_id = {r.id: r for r in rows}
    assert set(by_id) == set(_MINIMAX_IDS)
    for model_id, row in by_id.items():
        assert row.active is True, f"{model_id}: must be active"
        assert row.provider == "minimax", f"{model_id}: provider mismatch"
        assert row.modality == "chat", f"{model_id}: modality mismatch"
        assert row.input_modalities == "text", f"{model_id}: input_modalities mismatch"

    for model_id in _MINIMAX_IDS:
        snap = (
            await db_session.execute(
                text(
                    "SELECT prompt_usd_per_token, completion_usd_per_token FROM pricing_snapshots "
                    "WHERE model_id = :mid ORDER BY captured_at DESC LIMIT 1"
                ),
                {"mid": model_id},
            )
        ).one_or_none()
        assert snap is not None, f"{model_id}: must have a pricing snapshot"


# ===========================================================================
# Scenario: a second sync run does not deactivate MiniMax rows
# ===========================================================================


async def test_second_sync_does_not_deactivate_minimax_rows(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_composite(
        app,
        _FakeSource(
            chat_models=[
                CatalogModel(
                    id="anthropic/claude-opus-4",
                    name="Claude Opus 4",
                    context_length=200_000,
                    prompt_usd_per_token=15e-6,
                    completion_usd_per_token=75e-6,
                    provider="openrouter",
                )
            ]
        ),
    )
    assert (await client.post(SYNC)).status_code == 200

    # Second sync: OpenRouter's fake feed now returns a DIFFERENT model set entirely.
    _install_composite(
        app,
        _FakeSource(
            chat_models=[
                CatalogModel(
                    id="anthropic/claude-sonnet-4",
                    name="Claude Sonnet 4",
                    context_length=200_000,
                    prompt_usd_per_token=3e-6,
                    completion_usd_per_token=15e-6,
                    provider="openrouter",
                )
            ]
        ),
    )
    assert (await client.post(SYNC)).status_code == 200

    rows = (
        await db_session.execute(
            text("SELECT id, active FROM models WHERE id = ANY(:ids)"),
            {"ids": list(_MINIMAX_IDS)},
        )
    ).all()
    by_id = {r.id: r.active for r in rows}
    assert set(by_id) == set(_MINIMAX_IDS)
    assert all(active is True for active in by_id.values()), (
        f"MiniMax rows must survive a second sync with a different OpenRouter feed: {by_id}"
    )


# ===========================================================================
# Scenario: synced MiniMax models are visible on public and admin catalog endpoints
# ===========================================================================


async def test_v1_models_and_admin_catalog_models_list_minimax_with_correct_shape(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_composite(app, _FakeSource())
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    v1 = await client.get(MODELS, headers=headers)
    admin = await client.get(ADMIN_CATALOG_MODELS, headers=headers)

    assert v1.status_code == 200, v1.text
    assert admin.status_code == 200, admin.text

    v1_data = {m["id"]: m for m in v1.json()["data"]}
    admin_data = {m["id"]: m for m in admin.json()["data"]}

    for model_id in _MINIMAX_IDS:
        assert model_id in v1_data, f"{model_id} missing from /v1/models"
        assert model_id in admin_data, f"{model_id} missing from /admin/catalog/models"
        assert "input_modalities" not in v1_data[model_id], (
            f"{model_id}: /v1/models must not expose input_modalities"
        )
        assert admin_data[model_id]["input_modalities"] == ["text"], (
            f"{model_id}: /admin/catalog/models must expose input_modalities=['text']"
        )
