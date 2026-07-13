"""Production-wiring regression tests for the residency-policy seam.

v6 foundation rule: every new app.state seam introduced by a task must have a
paired production-wiring regression test asserting that create_app() correctly
threads the new adapter through to the live infrastructure (Pattern:
tests/model_fallbacks_wiring/test_model_fallbacks_wiring.py).

New seam introduced by residency-policy (always-on — no Settings feature flag;
the pin lives in tenants.residency_region, an additive-nullable DB column, so
absence of a pin is the byte-identical no-op default, M1):
  - app.state.residency_lookup: SqlAlchemyResidencyLookup(sessionmaker=app.state.sessionmaker)
  - FallbackModelRouter.residency_lookup: threaded from app.state.residency_lookup
    (Tier 2 — the router-layer dial-constraint pre-loop filter)

NonChatGovernance / CompletionUseCase / EmbeddingsUseCase are constructed fresh
per-request from request.app.state.residency_lookup (not stored as their own
app.state singletons) — that Tier 1 threading is proven end-to-end by the real
HTTP requests in tests/residency_policy/test_residency_policy_router.py instead
(a static create_app() inspection cannot observe a per-request construction).

No DB or Redis required — create_app() is called synchronously; app.state is
inspected without triggering lifespan.
"""

from __future__ import annotations

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.infrastructure.residency_lookup import SqlAlchemyResidencyLookup
from tests import _redis_env


def _make_settings(**kwargs: object) -> Settings:
    """Build a minimal Settings for unit tests (no live DB/Redis needed)."""
    defaults: dict[str, object] = {
        "database_url": _redis_env.TEST_DATABASE_URL,
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": _redis_env.TEST_REDIS_URL,
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_residency_lookup_exists_on_app_state() -> None:
    """create_app() wires app.state.residency_lookup as a SqlAlchemyResidencyLookup."""
    settings = _make_settings()
    app = create_app(settings)

    assert hasattr(app.state, "residency_lookup"), (
        "app.state.residency_lookup must be set by create_app()"
    )
    assert isinstance(app.state.residency_lookup, SqlAlchemyResidencyLookup), (
        f"Expected SqlAlchemyResidencyLookup, got {type(app.state.residency_lookup)}"
    )


def test_residency_lookup_shares_the_app_sessionmaker() -> None:
    """The adapter is constructed with app.state.sessionmaker — never its own pool
    (mirrors DbTenantModelPresetStore / DbTenantProviderKeyStore precedent)."""
    settings = _make_settings()
    app = create_app(settings)

    lookup: SqlAlchemyResidencyLookup = app.state.residency_lookup
    assert lookup._sessionmaker is app.state.sessionmaker  # noqa: SLF001


def test_model_router_residency_lookup_threaded_through() -> None:
    """create_app() threads app.state.residency_lookup into the model_router's Tier 2
    dial-constraint filter — the SAME instance, not a second independent adapter."""
    settings = _make_settings()
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._residency_lookup is app.state.residency_lookup  # noqa: SLF001
