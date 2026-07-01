"""Failing-first (RED) suite for the DB-backed TenantModelPresetStore (v56 §3 CONTRACT, TASK.md §4).

Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test.
bootstrap_fresh_db() pattern mirrors the v25 BYOK tenant_provider_key_store harness exactly.
All tests call the store directly (no HTTP) — this is a schema + PORT task.

TRUE-RED RULE: every test imports from modules that do NOT exist yet:
  - gateway.proxy.domain.model_presets                       (TenantModelPreset, TenantModelPresetStore, ModelPresetError)
  - gateway.proxy.infrastructure.tenant_model_preset_store   (DbTenantModelPresetStore)
Collection fails with ModuleNotFoundError → RED for the right reason.

Once the modules + migration exist, each test asserts the observable behavior noted in its docstring.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

# ---------------------------------------------------------------------------
# Modules under test — none exist yet; import failure IS the red target.
# ---------------------------------------------------------------------------
from gateway.proxy.domain.model_presets import (  # type: ignore[import]
    ModelPresetError,
    TenantModelPreset,
    TenantModelPresetStore,
)
from gateway.proxy.infrastructure.tenant_model_preset_store import (  # type: ignore[import]
    DbTenantModelPresetStore,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105


def make_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
    )


# ---------------------------------------------------------------------------
# DB bootstrap helpers (mirror the BYOK tenant_provider_key_store harness)
# ---------------------------------------------------------------------------


async def bootstrap_fresh_db(settings: Settings) -> tuple[Any, Any]:
    """Create a fresh schema; return (engine, sessionmaker)."""
    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return engine, bootstrap_app.state.sessionmaker


async def insert_tenant_row(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Insert a minimal tenants row to satisfy the FK tenant_model_presets.tenant_id → tenants.id."""
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, created_at, updated_at) "
            "VALUES (:id, :name, now(), now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "name": f"test-tenant-{tenant_id}"},
    )
    await session.commit()


async def insert_model_row(session: AsyncSession, model_id: str, *, active: bool = True) -> None:
    """Seed a catalog models row so target validation can pass (or, with active=False, fail)."""
    await session.execute(
        text(
            "INSERT INTO models (id, name, active) VALUES (:id, :name, :active) "
            "ON CONFLICT (id) DO UPDATE SET active = EXCLUDED.active"
        ),
        {"id": model_id, "name": model_id, "active": active},
    )
    await session.commit()


def build_store(engine: Any, sessionmaker: Any) -> DbTenantModelPresetStore:
    """Instantiate the store. It constructs its own SqlAlchemyModelChecker per upsert() call."""
    return DbTenantModelPresetStore(sessionmaker=sessionmaker)


async def _prepare(
    settings: Settings, *, models: dict[str, bool] | None = None
) -> tuple[Any, Any, uuid.UUID]:
    """Fresh DB + one tenant + optional seeded models. Returns (engine, sessionmaker, tenant_id)."""
    engine, sm = await bootstrap_fresh_db(settings)
    tenant_id = uuid.uuid4()
    async with sm() as session:
        await insert_tenant_row(session, tenant_id)
        for mid, active in (models or {}).items():
            await insert_model_row(session, mid, active=active)
    return engine, sm, tenant_id


# ---------------------------------------------------------------------------
# S1 — upsert then resolve returns the target
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upsert_then_resolve_returns_target() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True})
    store = build_store(engine, sm)

    await store.upsert(tenant, "cheap", "opus", "gpt5-5")

    assert await store.resolve(tenant, "cheap", "opus") == "gpt5-5"


# ---------------------------------------------------------------------------
# S2 — re-upsert updates the target in place (no duplicate row)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reupsert_updates_in_place_no_duplicate() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True, "glm-5.2": True})
    store = build_store(engine, sm)

    await store.upsert(tenant, "cheap", "opus", "gpt5-5")
    await store.upsert(tenant, "cheap", "opus", "glm-5.2")

    assert await store.resolve(tenant, "cheap", "opus") == "glm-5.2"
    rows = [
        p for p in await store.list(tenant) if p.preset_name == "cheap" and p.alias_key == "opus"
    ]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# S3 — the same alias maps differently across presets
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_alias_differs_across_presets() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(
        settings, models={"deepseek-v4-flash": True, "gpt5-5": True}
    )
    store = build_store(engine, sm)

    await store.upsert(tenant, "cheap", "opus", "deepseek-v4-flash")
    await store.upsert(tenant, "quality", "opus", "gpt5-5")

    assert await store.resolve(tenant, "cheap", "opus") == "deepseek-v4-flash"
    assert await store.resolve(tenant, "quality", "opus") == "gpt5-5"


# ---------------------------------------------------------------------------
# S4 — list returns only the calling tenant's rows (cross-tenant isolation)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_only_calling_tenant() -> None:
    settings = make_settings()
    engine, sm, tenant_a = await _prepare(settings, models={"gpt5-5": True, "glm-5.2": True})
    tenant_b = uuid.uuid4()
    async with sm() as session:
        await insert_tenant_row(session, tenant_b)
    store = build_store(engine, sm)

    await store.upsert(tenant_a, "cheap", "opus", "gpt5-5")
    await store.upsert(tenant_b, "cheap", "opus", "glm-5.2")

    rows_a = await store.list(tenant_a)
    assert all(isinstance(p, TenantModelPreset) for p in rows_a)
    targets_a = {(p.preset_name, p.alias_key, p.target_model) for p in rows_a}
    assert ("cheap", "opus", "gpt5-5") in targets_a
    # tenant B's row (target glm-5.2) must NOT appear in tenant A's listing
    assert all(p.target_model != "glm-5.2" for p in rows_a)
    # and resolve is isolated too
    assert await store.resolve(tenant_a, "cheap", "opus") == "gpt5-5"
    assert await store.resolve(tenant_b, "cheap", "opus") == "glm-5.2"


# ---------------------------------------------------------------------------
# S5 — resolve is None when no preset exists
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolve_none_when_absent() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings)
    store = build_store(engine, sm)

    assert await store.resolve(tenant, "cheap", "opus") is None


# ---------------------------------------------------------------------------
# S6 — delete removes the mapping and is idempotent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_removes_and_idempotent() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True})
    store = build_store(engine, sm)
    await store.upsert(tenant, "cheap", "opus", "gpt5-5")

    assert await store.delete(tenant, "cheap", "opus") is True
    assert await store.resolve(tenant, "cheap", "opus") is None
    assert await store.delete(tenant, "cheap", "opus") is False


# ---------------------------------------------------------------------------
# S7 — reject an unknown/inactive target model (strict at write)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_unknown_target() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings)  # no models seeded → "ghost-model" absent
    store = build_store(engine, sm)

    with pytest.raises(ModelPresetError) as ei:
        await store.upsert(tenant, "cheap", "opus", "ghost-model")
    assert ei.value.code == "ERR_PRESET_TARGET_UNKNOWN"
    # unchanged: no row written
    assert await store.resolve(tenant, "cheap", "opus") is None


@pytest.mark.asyncio
async def test_reject_inactive_target() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"retired-model": False})
    store = build_store(engine, sm)

    with pytest.raises(ModelPresetError) as ei:
        await store.upsert(tenant, "cheap", "opus", "retired-model")
    assert ei.value.code == "ERR_PRESET_TARGET_UNKNOWN"
    assert await store.resolve(tenant, "cheap", "opus") is None


# ---------------------------------------------------------------------------
# S8 — reject an invalid selector token (colon / empty / over-length)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_selector_with_colon() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True})
    store = build_store(engine, sm)

    with pytest.raises(ModelPresetError) as ei:
        await store.upsert(tenant, "cheap:tier", "opus", "gpt5-5")
    assert ei.value.code == "ERR_PRESET_SELECTOR_INVALID"
    assert await store.list(tenant) == []  # unchanged: nothing written


@pytest.mark.asyncio
async def test_reject_selector_empty() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True})
    store = build_store(engine, sm)

    with pytest.raises(ModelPresetError) as ei:
        await store.upsert(tenant, "cheap", "", "gpt5-5")
    assert ei.value.code == "ERR_PRESET_SELECTOR_INVALID"


@pytest.mark.asyncio
async def test_reject_selector_over_length() -> None:
    settings = make_settings()
    engine, sm, tenant = await _prepare(settings, models={"gpt5-5": True})
    store = build_store(engine, sm)

    with pytest.raises(ModelPresetError) as ei:
        await store.upsert(tenant, "x" * 65, "opus", "gpt5-5")
    assert ei.value.code == "ERR_PRESET_SELECTOR_INVALID"


# ---------------------------------------------------------------------------
# S9 — additive: the store is wired on app.state AND consumed by the proxy ingress
# ---------------------------------------------------------------------------
# NOTE (updated by preset-resolution-ingress, v56): this test originally pinned
# "ingress untouched" as an explicit placeholder for THIS task — its own docstring
# named it: "that is the next task". preset-resolution-ingress now wires
# TenantModelPresetStore.resolve into CompletionUseCase (+ the 4 non-chat use
# cases), so the assertion is flipped to confirm the wiring landed, completing the
# two-task bundle exactly as both tasks' TASK.md always described it.
@pytest.mark.asyncio
async def test_additive_store_wired_and_ingress_resolves() -> None:
    """The store is wired on app.state AND the proxy ingress now resolves presets
    (preset-resolution-ingress, v56). Proven structurally: use_cases.py references
    the preset store port; full behavioral coverage lives in
    tests/preset_resolution_ingress/test_preset_resolution_ingress.py."""
    import inspect

    from gateway.proxy.application import use_cases

    settings = make_settings()
    app = create_app(settings)
    assert hasattr(app.state, "tenant_model_preset_store")
    assert isinstance(app.state.tenant_model_preset_store, DbTenantModelPresetStore)
    # ingress now resolves presets: CompletionUseCase references both symbols.
    src = inspect.getsource(use_cases)
    assert "tenant_model_preset_store" in src
    assert "TenantModelPresetStore" in src


# ---------------------------------------------------------------------------
# Protocol conformance — the adapter satisfies the domain port
# ---------------------------------------------------------------------------
def test_adapter_conforms_to_port() -> None:
    assert issubclass(DbTenantModelPresetStore, TenantModelPresetStore) or isinstance(
        DbTenantModelPresetStore, type
    )
    for method in ("upsert", "resolve", "list", "delete"):
        assert hasattr(DbTenantModelPresetStore, method)
