"""RED suite for routing-config-store (v32 TASK.md §4).

Persist an operator-wide routing config + apply it at BOOT over env Settings (DB-wins, env
fallback). Frozen contract (§3): singleton `routing_config` table (JSONB config), a repository
get()/upsert(), a pure merge_routing_config(settings, stored) that re-validates, an extracted
build_model_router(settings, ...deps), and a lifespan boot-apply that rebuilds the router.

RED before BUILD: the proxy.application.routing_config_merge / proxy.infrastructure.
routing_config_repository modules + main.build_model_router do not exist yet → ImportError /
the routing_config table is missing — the honest missing-implementation red.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.core.config import Settings

TEST_DB_URL = "postgresql+asyncpg://gateway@localhost:5433/gateway_test"
TEST_JWT = "test-secret-routing-config-store"


def _env_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "jwt_secret": TEST_JWT,
        "database_url": TEST_DB_URL,
        "model_groups": {"gpt": ["a"]},
        "routing_strategy": "ordered",
    }
    base.update(overrides)
    return Settings(**base)


# ── pure merge ────────────────────────────────────────────────────────────
def test_merge_none_is_env_fallback() -> None:
    from gateway.proxy.application.routing_config_merge import merge_routing_config

    env = _env_settings()
    merged = merge_routing_config(env, None)

    assert merged.routing_strategy == "ordered"
    assert merged.model_groups == {"gpt": ["a"]}


def test_merge_db_wins() -> None:
    from gateway.proxy.application.routing_config_merge import merge_routing_config

    env = _env_settings(routing_strategy="ordered", model_groups={"gpt": ["a"]})
    stored = {"routing_strategy": "simple-shuffle", "model_groups": {"gpt": ["a", "b"]}}

    merged = merge_routing_config(env, stored)

    assert merged.routing_strategy == "simple-shuffle"
    assert merged.model_groups == {"gpt": ["a", "b"]}
    # real (non-routing) settings preserved
    assert merged.jwt_secret == TEST_JWT


def test_merge_preserves_non_routing_and_secret() -> None:
    from gateway.proxy.application.routing_config_merge import merge_routing_config

    env = _env_settings()
    merged = merge_routing_config(env, {"routing_strategy": "simple-shuffle"})

    # database_url + jwt_secret unchanged after applying a routing-only override
    assert merged.jwt_secret == TEST_JWT
    assert merged.routing_strategy == "simple-shuffle"


def test_merge_invalid_strategy_raises() -> None:
    from gateway.proxy.application.routing_config_merge import merge_routing_config

    env = _env_settings()
    with pytest.raises(ValueError, match="UNKNOWN_ROUTING_STRATEGY"):
        merge_routing_config(env, {"routing_strategy": "bogus"})
    # env object itself was not mutated
    assert env.routing_strategy == "ordered"


def test_merge_invalid_deployment_raises() -> None:
    from gateway.proxy.application.routing_config_merge import merge_routing_config

    env = _env_settings()
    with pytest.raises(ValueError, match="DUPLICATE_DEPLOYMENT"):
        merge_routing_config(env, {"model_groups": {"gpt": ["a", "a"]}})


# ── build_model_router byte-identical ───────────────────────────────────────
async def test_build_model_router_byte_identical(app: Any) -> None:
    from gateway.main import build_model_router

    settings = app.state.settings
    router = build_model_router(
        settings,
        redis_client=app.state.redis_client,
        completion_upstream=app.state.completion_upstream,
        cooldown_gate=app.state.cooldown_gate,
        metrics_registry=app.state.metrics_registry,
    )
    # Behaviour-preserving extraction: the freshly-built router matches the one create_app
    # wired from the SAME settings — not just model_groups but the routing wiring too.
    env_router = app.state.model_router
    assert router.model_groups == settings.model_groups == env_router.model_groups
    assert router.deployments == env_router.deployments
    assert type(router._strategy) is type(env_router._strategy)  # noqa: SLF001 — wiring parity
    assert router._fallback_on_error == env_router._fallback_on_error  # noqa: SLF001


# ── repository (real Postgres) ──────────────────────────────────────────────
async def test_repo_upsert_and_get(app: Any) -> None:
    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    repo = RoutingConfigRepository(app.state.sessionmaker)
    assert await repo.get() is None

    config = {"routing_strategy": "simple-shuffle", "model_groups": {"gpt": ["a", "b"]}}
    await repo.upsert(config)

    assert await repo.get() == config
    assert await _row_count(app) == 1


async def test_repo_upsert_replaces_singleton(app: Any) -> None:
    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    repo = RoutingConfigRepository(app.state.sessionmaker)
    await repo.upsert({"routing_strategy": "ordered"})
    await repo.upsert({"routing_strategy": "simple-shuffle"})

    got = await repo.get()
    assert got == {"routing_strategy": "simple-shuffle"}
    assert await _row_count(app) == 1


# ── boot apply (drive the real lifespan; ASGITransport does not) ─────────────
async def test_boot_applies_persisted_config(app: Any) -> None:
    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    repo = RoutingConfigRepository(app.state.sessionmaker)
    await repo.upsert(
        {"routing_strategy": "simple-shuffle", "model_groups": {"gpt": ["x", "y"]}}
    )

    async with app.router.lifespan_context(app):
        assert app.state.settings.routing_strategy == "simple-shuffle"
        assert app.state.model_router.model_groups == {"gpt": ["x", "y"]}
    await app.state.engine.dispose()


async def test_boot_no_row_keeps_env_router(app: Any) -> None:
    env_groups = app.state.settings.model_groups
    env_router_groups = app.state.model_router.model_groups

    async with app.router.lifespan_context(app):
        assert app.state.model_router.model_groups == env_router_groups
        assert app.state.settings.model_groups == env_groups
    await app.state.engine.dispose()


async def test_boot_db_error_falls_back(app: Any, monkeypatch: Any) -> None:
    from gateway.proxy.infrastructure import routing_config_repository as repo_mod

    env_router_groups = app.state.model_router.model_groups
    env_strategy = app.state.settings.routing_strategy

    async def _boom(self: Any) -> dict[str, Any] | None:
        raise RuntimeError("simulated DB read failure at boot")

    monkeypatch.setattr(repo_mod.RoutingConfigRepository, "get", _boom)

    async with app.router.lifespan_context(app):
        # startup completed without crashing; BOTH env router and env settings intact
        # (settings must never drift from the router on the failure path)
        assert app.state.model_router.model_groups == env_router_groups
        assert app.state.settings.routing_strategy == env_strategy
        assert app.state.settings.model_groups == env_router_groups
    await app.state.engine.dispose()


async def test_boot_router_build_failure_keeps_settings_and_router_in_sync(
    app: Any, monkeypatch: Any
) -> None:
    """If build_model_router raises after a valid merge, settings must NOT drift.

    Guards the failure ordering: app.state.settings is committed only AFTER the router
    builds, so a build error leaves BOTH at the env config (never settings=DB / router=env).
    """
    import gateway.main as main_mod
    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    repo = RoutingConfigRepository(app.state.sessionmaker)
    await repo.upsert({"routing_strategy": "simple-shuffle", "model_groups": {"gpt": ["x", "y"]}})

    env_strategy = app.state.settings.routing_strategy
    env_router_groups = app.state.model_router.model_groups

    def _boom_build(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated router build failure at boot")

    monkeypatch.setattr(main_mod, "build_model_router", _boom_build)

    async with app.router.lifespan_context(app):
        # build raised → caught → env config preserved on BOTH fields (no drift)
        assert app.state.settings.routing_strategy == env_strategy
        assert app.state.model_router.model_groups == env_router_groups
    await app.state.engine.dispose()


# ── helper ──────────────────────────────────────────────────────────────────
async def _row_count(app: Any) -> int:
    from sqlalchemy import text

    async with app.state.sessionmaker() as session:
        row = (await session.execute(text("SELECT COUNT(*) FROM routing_config"))).fetchone()
        return int(row[0]) if row else 0
