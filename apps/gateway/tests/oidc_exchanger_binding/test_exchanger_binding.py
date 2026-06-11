"""Regression suite for the per-tenant OIDC token-exchange binding defect.

Found by v5 LIVE verification (scripts/live_v5_verify.py C5f), 2026-06-12:
HttpxOidcExchanger bound ONLY env Settings (oidc_token_url / oidc_client_id /
oidc_client_secret), so a tenant with a DB-backed OidcProviderConfig had its
authorization code exchanged at the ENV IdP's token endpoint with ENV client
credentials. The foreign IdP then minted a token whose kid was absent from the
tenant's JWKS → 401 ERR_OIDC_TOKEN_INVALID for every per-tenant-IdP login.

The frozen oidc_tenant_config suite missed this because it injects
FakeOidcExchanger at app.state.oidc_exchanger, bypassing the production
exchanger construction entirely. This suite pins the production binding.

Contract conformance: oidc-tenant-config §3 requires the callback to use the
cookie-resolved per-tenant config for the WHOLE login (v5 exit criterion: two
tenants authenticate via two DIFFERENT IdP configs in one deployment). This
fix implements that contract; no frozen artifact pinned the env-only binding.
"""

from __future__ import annotations

import pytest

from gateway.auth.domain.entities import OidcProviderConfig
from gateway.auth.infrastructure.httpx_oidc_exchanger import HttpxOidcExchanger
from gateway.core.config import Settings


def make_base_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "oidc_enabled": True,
        "oidc_issuer": "https://env-idp.example",
        "oidc_client_id": "env-client",
        "oidc_client_secret": "env-secret",
        "oidc_redirect_uri": "https://gw.example/auth/oidc/callback",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def make_tenant_config() -> OidcProviderConfig:
    return OidcProviderConfig(
        tenant_id="019eb805-07e0-7a01-bafe-a26533c481df",
        issuer="https://tenant-idp.example",
        client_id="tenant-client",
        client_secret="tenant-secret",  # noqa: S106 — test literal
        authorize_url="https://tenant-idp.example/authorize",
        token_url="https://tenant-idp.example/token",
        jwks_url="https://tenant-idp.example/jwks",
        email_domains=["tenant.example"],
        enabled=True,
    )


class TestExchangerPerTenantBinding:
    """E1–E3: the production exchanger must bind the per-tenant IdP, not env."""

    def test_e1_config_binds_tenant_token_endpoint_and_credentials(self) -> None:
        """E1: with oidc_config, token_url/client_id/client_secret come from it.

        RED before fix: HttpxOidcExchanger.__init__ does not accept oidc_config
        (TypeError) — the production path cannot bind a per-tenant IdP at all.
        """
        exchanger = HttpxOidcExchanger(
            settings=make_base_settings(), oidc_config=make_tenant_config()
        )
        assert exchanger._token_endpoint == "https://tenant-idp.example/token"
        assert exchanger._client_id == "tenant-client"
        assert exchanger._client_secret == "tenant-secret"  # noqa: S105

    def test_e2_no_config_binds_env_settings(self) -> None:
        """E2: without oidc_config the env binding is unchanged (v4/C1 path)."""
        exchanger = HttpxOidcExchanger(settings=make_base_settings())
        assert exchanger._token_endpoint == "https://env-idp.example/token"
        assert exchanger._client_id == "env-client"
        assert exchanger._client_secret == "env-secret"  # noqa: S105

    def test_e3_deps_thread_config_into_exchanger(self) -> None:
        """E3: get_oidc_exchanger(request, oidc_config=...) builds a tenant-bound
        exchanger when no app.state seam is injected.

        RED before fix: get_oidc_exchanger has no oidc_config parameter.
        """
        from types import SimpleNamespace

        from gateway.auth.api.deps import get_oidc_exchanger

        state = SimpleNamespace(settings=make_base_settings())
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        exchanger = get_oidc_exchanger(request, oidc_config=make_tenant_config())  # type: ignore[arg-type]
        assert isinstance(exchanger, HttpxOidcExchanger)
        assert exchanger._token_endpoint == "https://tenant-idp.example/token"

    def test_e4_seam_precedence_preserved(self) -> None:
        """E4 green-by-design: an injected app.state.oidc_exchanger still wins
        even when a per-tenant config is supplied (frozen-suite seam unchanged).
        """
        from types import SimpleNamespace

        from gateway.auth.api.deps import get_oidc_exchanger

        sentinel = object()
        state = SimpleNamespace(settings=make_base_settings(), oidc_exchanger=sentinel)
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        exchanger = get_oidc_exchanger(request, oidc_config=make_tenant_config())  # type: ignore[arg-type]
        assert exchanger is sentinel


class TestResolverProductionWiring:
    """E5–E6: production must construct DbOidcConfigResolver when no seam is injected.

    Second defect found by the same live run: create_app leaves
    app.state.oidc_config_resolver = None and NOTHING constructed the production
    resolver — per-tenant OIDC silently fell back to the env path for every
    request (login?domain= set the "env-config" sentinel cookie instead of the
    tenant id). The frozen suite missed it by always injecting a fake resolver.
    """

    def test_e5_no_seam_constructs_db_resolver(self) -> None:
        """E5: with the seam None (create_app default), the helper returns a
        session-bound DbOidcConfigResolver.

        RED before fix: deps has no get_oidc_config_resolver helper.
        """
        from types import SimpleNamespace

        from gateway.auth.api.deps import get_oidc_config_resolver
        from gateway.auth.infrastructure.db_oidc_config_resolver import (
            DbOidcConfigResolver,
        )

        state = SimpleNamespace(settings=make_base_settings(), oidc_config_resolver=None)
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        session = object()  # session is only stored, not used at construction

        resolver = get_oidc_config_resolver(request, session)  # type: ignore[arg-type]
        assert isinstance(resolver, DbOidcConfigResolver)

    def test_e6_resolver_seam_precedence_preserved(self) -> None:
        """E6 green-by-design intent: an injected resolver (frozen-suite fake)
        still wins over production construction."""
        from types import SimpleNamespace

        from gateway.auth.api.deps import get_oidc_config_resolver

        sentinel = object()
        state = SimpleNamespace(settings=make_base_settings(), oidc_config_resolver=sentinel)
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        resolver = get_oidc_config_resolver(request, object())  # type: ignore[arg-type]
        assert resolver is sentinel


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
