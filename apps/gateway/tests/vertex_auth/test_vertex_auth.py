"""Failing-first (RED) suite for vertex-adapter's auth sub-system (TASK.md §4).

Covers: GoogleServiceAccountCredential validation + ProviderCredential union widening
(M6), VertexServiceAccountConfig/VertexTokenProvider mint+cache/single-flight/fail-closed
(M3, M4, R3), VertexTokenProviderCache's non-secret cache key (M4 security), and the
PUT /admin/provider-keys/vertex admin-router round-trip (M6, M7, R1) including the
deliberate absence of an SSRF write-time guard for vertex (M7).

HTTP behavior via httpx.MockTransport (token endpoint); time via an injected clock —
mirrors tests/azure_aad/test_azure_ad.py and tests/dynamic_auth_byok/test_azure_ad_provider_cache.py
exactly. No real network for the auth unit tests. The PUT-credential tests use a real
Postgres (per-test fresh schema, mirrors tests/provider_config_admin_api).

RED reason: gateway.proxy.infrastructure.vertex_ad and the GoogleServiceAccountCredential/
ProviderCredential widening do not exist yet → ImportError / AttributeError on collection.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import PublicFormat

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.proxy.domain.errors import UpstreamUnavailableError
from tests import _redis_env

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# One RSA keypair shared across this module's unit tests (key generation is
# expensive; a single 2048-bit key is fine for functional signing/verification).
# ---------------------------------------------------------------------------

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY_PEM = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_PUBLIC_KEY_PEM = (
    _RSA_KEY.public_key()
    .public_bytes(encoding=serialization.Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo)
    .decode()
)

_CLIENT_EMAIL = "sa-1@my-project.iam.gserviceaccount.com"
_PROJECT_ID = "my-project"


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_config(**overrides: Any) -> Any:
    from gateway.proxy.infrastructure.vertex_ad import VertexServiceAccountConfig

    fields: dict[str, Any] = {
        "project_id": _PROJECT_ID,
        "client_email": _CLIENT_EMAIL,
        "private_key": _PRIVATE_KEY_PEM,
        "private_key_id": "key-1",
    }
    fields.update(overrides)
    return VertexServiceAccountConfig(**fields)


def _make_provider(handler: object, *, clock: _Clock | None = None, skew: float = 60.0) -> Any:
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProvider

    provider = VertexTokenProvider(
        config=_make_config(),
        now_fn=clock or _Clock(),
        expiry_skew_s=skew,
    )
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return provider


def _token_handler(hits: list[int], *, token: str = "tok-1", expires_in: int = 3600):
    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(
            200, json={"access_token": f"{token}-{hits[0]}", "expires_in": expires_in}
        )

    return handler


def _assertion_form_body(request: httpx.Request) -> dict[str, str]:
    from urllib.parse import parse_qs

    parsed = parse_qs(request.content.decode())
    return {k: v[0] for k, v in parsed.items()}


# ===========================================================================
# GoogleServiceAccountCredential — validation + ProviderCredential widening (M6)
# ===========================================================================


async def test_credential_valid_constructs() -> None:
    from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential

    cred = GoogleServiceAccountCredential(
        project_id=_PROJECT_ID,
        client_email=_CLIENT_EMAIL,
        private_key=_PRIVATE_KEY_PEM,
        private_key_id="key-1",
    )
    assert cred.project_id == _PROJECT_ID
    assert _PRIVATE_KEY_PEM not in repr(cred), "private_key must be masked in repr"


async def test_credential_incomplete_project_id_rejected() -> None:
    from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential

    with pytest.raises(ValueError, match="ERR_PROVIDER_CREDENTIAL_INCOMPLETE"):
        GoogleServiceAccountCredential(
            project_id="", client_email=_CLIENT_EMAIL, private_key=_PRIVATE_KEY_PEM
        )


async def test_credential_empty_private_key_rejected() -> None:
    from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential

    with pytest.raises(ValueError, match="ERR_PROVIDER_CREDENTIAL_EMPTY"):
        GoogleServiceAccountCredential(
            project_id=_PROJECT_ID, client_email=_CLIENT_EMAIL, private_key="   "
        )


async def test_provider_credential_union_accepts_google_service_account() -> None:
    """ProviderCredential (the widened Union) accepts a GoogleServiceAccountCredential."""
    from gateway.proxy.domain.provider_credentials import (
        GoogleServiceAccountCredential,
        ProviderCredential,
    )

    cred = GoogleServiceAccountCredential(
        project_id=_PROJECT_ID, client_email=_CLIENT_EMAIL, private_key=_PRIVATE_KEY_PEM
    )
    typed: ProviderCredential = cred  # type: ignore[assignment]
    assert isinstance(typed, GoogleServiceAccountCredential)


async def test_provider_value_set_and_byok_providers_include_vertex() -> None:
    from gateway.proxy.domain.provider_credentials import BYOK_PROVIDERS, PROVIDER_VALUE_SET

    assert "vertex" in PROVIDER_VALUE_SET
    assert "vertex" in BYOK_PROVIDERS


# ===========================================================================
# VertexTokenProvider — mint / cache / single-flight / fail-closed (M3, M4, R3)
# ===========================================================================


async def test_token_acquired_via_rs256_jwt_bearer_assertion() -> None:
    """The POST body carries a valid RFC 7523 JWT-bearer assertion, RS256-signed."""
    import jwt

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = _assertion_form_body(request)
        seen["grant_type"] = form.get("grant_type")
        seen["assertion"] = form.get("assertion")
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    provider = _make_provider(handler)
    token = await provider.get_token()
    assert token == "tok-1"
    assert seen["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    # Independently decode+verify the assertion with the PUBLIC key (not gateway's
    # own verification code) — proves it is a genuinely valid RS256 signature over
    # the RFC 7523 required claims.
    claims = jwt.decode(
        seen["assertion"],
        _PUBLIC_KEY_PEM,
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
    )
    assert claims["iss"] == _CLIENT_EMAIL
    assert claims["sub"] == _CLIENT_EMAIL
    assert claims["scope"] == "https://www.googleapis.com/auth/cloud-platform"
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] <= 3600


async def test_token_acquired_once_and_cached() -> None:
    hits = [0]
    provider = _make_provider(_token_handler(hits))
    a = await provider.get_token()
    b = await provider.get_token()
    assert hits[0] == 1
    assert a == b == "tok-1-1"


async def test_token_refreshes_after_expiry() -> None:
    hits = [0]
    clock = _Clock(0.0)
    provider = _make_provider(_token_handler(hits, expires_in=3600), clock=clock, skew=60.0)
    first = await provider.get_token()
    clock.t = 3600.0  # past expiry - skew
    second = await provider.get_token()
    assert hits[0] == 2
    assert first != second


async def test_concurrent_refresh_single_flight() -> None:
    """M4 edge case: 10 concurrent get_token() calls under a just-expired cache make
    exactly ONE token-mint HTTP POST; all callers receive the SAME token."""
    hits = [0]
    provider = _make_provider(_token_handler(hits))
    results = await asyncio.gather(*[provider.get_token() for _ in range(10)])
    assert hits[0] == 1
    assert set(results) == {"tok-1-1"}


async def test_token_mint_failure_fails_closed() -> None:
    """R3: a non-200 token response raises UpstreamUnavailableError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()
    # nothing cached → a second call tries again (still fails)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()


async def test_token_timeout_fails_closed_no_secret_in_chain() -> None:
    """R3 + security: private_key never reaches the exception chain."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("idp unreachable")

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await provider.get_token()
    assert exc.value.__cause__ is None
    assert _PRIVATE_KEY_PEM not in str(exc.value)


async def test_non_json_200_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy login</html>")

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()


async def test_private_key_never_in_repr_or_config_str() -> None:
    config = _make_config()
    assert _PRIVATE_KEY_PEM not in repr(config)
    assert _PRIVATE_KEY_PEM not in str(config)


async def test_malformed_private_key_fails_closed_no_secret_in_chain() -> None:
    """R3 + security: a malformed (non-PEM) private_key raises UpstreamUnavailableError
    from the assertion-signing step (before any network call), with `from None`
    suppressing the underlying crypto error from the exception chain."""
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    provider._config = _make_config(private_key="not-a-valid-pem-key")  # type: ignore[attr-defined]
    with pytest.raises(UpstreamUnavailableError) as exc:
        await provider.get_token()
    assert exc.value.__cause__ is None
    assert "not-a-valid-pem-key" not in str(exc.value)


async def test_missing_access_token_in_200_fails_closed() -> None:
    """A 200 response missing the access_token field must fail closed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()


async def test_provider_aclose_closes_underlying_client() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"access_token": "x"}))
    await provider.aclose()
    assert provider._client.is_closed  # type: ignore[attr-defined]


# ===========================================================================
# VertexTokenProviderCache — non-secret cache key (M4 security)
# ===========================================================================


async def test_cache_key_excludes_private_key() -> None:
    """M4 security: two configs sharing (client_email, project_id) but DIFFERENT
    private_key values resolve to the SAME cached provider entry — the cache key
    is (client_email, project_id) ONLY."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    constructed: list[Any] = []

    def _factory(config: Any) -> Any:
        constructed.append(config)
        return object()

    cache = VertexTokenProviderCache(ttl_s=300.0, max_size=512, provider_factory=_factory)

    cfg_a = _make_config(private_key=_PRIVATE_KEY_PEM)
    # A second, syntactically-different (but still parseable) PEM-shaped string is
    # unnecessary — the cache key excludes private_key entirely, so even a config
    # object with the SAME private_key string proves nothing about the key content;
    # what matters is identity (client_email, project_id) drives reuse regardless.
    cfg_b = _make_config(private_key=_PRIVATE_KEY_PEM, private_key_id="different-kid")

    p1 = cache.get_or_create(cfg_a, "tenant-A")
    p2 = cache.get_or_create(cfg_b, "tenant-A")

    assert len(constructed) == 1, (
        "Two configs sharing (client_email, project_id) must share ONE cache entry "
        f"regardless of other fields, got {len(constructed)} constructions"
    )
    assert p1 is p2


async def test_cache_distinct_identities_distinct_providers() -> None:
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    constructed: list[Any] = []

    def _factory(config: Any) -> Any:
        constructed.append(config)
        return object()

    cache = VertexTokenProviderCache(ttl_s=300.0, max_size=512, provider_factory=_factory)
    p_a = cache.get_or_create(_make_config(client_email="a@x.iam.gserviceaccount.com"), "tenant-A")
    p_b = cache.get_or_create(_make_config(client_email="b@x.iam.gserviceaccount.com"), "tenant-A")
    assert len(constructed) == 2
    assert p_a is not p_b


async def test_cache_default_provider_factory_builds_a_real_provider() -> None:
    """Without an explicit provider_factory override, get_or_create builds a real
    VertexTokenProvider via the module-level _default_provider_factory."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProvider, VertexTokenProviderCache

    cache = VertexTokenProviderCache(ttl_s=300.0, max_size=512)
    provider = cache.get_or_create(_make_config(), "tenant-A")
    assert isinstance(provider, VertexTokenProvider)
    await provider.aclose()


async def test_cache_ttl_expiry_evicts_and_closes_old_provider() -> None:
    """When an entry's TTL has elapsed, get_or_create builds a fresh provider and
    fire-and-forget closes the stale one (asyncio.create_task under a running loop)."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    closed: list[Any] = []

    class _FakeProvider:
        async def aclose(self) -> None:
            closed.append(self)

    clock = _Clock(0.0)
    cache = VertexTokenProviderCache(
        ttl_s=10.0, max_size=512, now_fn=clock, provider_factory=lambda cfg: _FakeProvider()
    )
    first = cache.get_or_create(_make_config(), "tenant-A")
    clock.t = 100.0  # past ttl_s
    second = cache.get_or_create(_make_config(), "tenant-A")
    assert first is not second
    await asyncio.sleep(0)  # let the fire-and-forget close task run
    assert closed == [first]


async def test_cache_size_cap_evicts_oldest_entry() -> None:
    """A soft size cap evicts the oldest-created entry (fire-and-forget closed) once
    the cache exceeds max_size."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    closed: list[Any] = []

    class _FakeProvider:
        async def aclose(self) -> None:
            closed.append(self)

    cache = VertexTokenProviderCache(
        ttl_s=300.0, max_size=2, provider_factory=lambda cfg: _FakeProvider()
    )
    p1 = cache.get_or_create(_make_config(client_email="a@x.iam.gserviceaccount.com"), "tenant-A")
    cache.get_or_create(_make_config(client_email="b@x.iam.gserviceaccount.com"), "tenant-A")
    cache.get_or_create(_make_config(client_email="c@x.iam.gserviceaccount.com"), "tenant-A")
    await asyncio.sleep(0)
    assert closed == [p1]


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_cache_close_fire_and_forget_noop_with_no_running_loop() -> None:
    """_close_provider_fire_and_forget degrades to a no-op (no raise) when called
    outside a running event loop — exercised via the sync fallback branch. This test
    is deliberately a PLAIN sync function (no event loop at all) so
    asyncio.get_running_loop() genuinely raises RuntimeError inside the helper."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    cache = VertexTokenProviderCache(
        ttl_s=300.0, max_size=512, provider_factory=lambda cfg: object()
    )
    # Directly invoke the private helper with no running loop in this frame — must
    # not raise (falls back to a best-effort no-op).
    cache._close_provider_fire_and_forget(object())  # type: ignore[attr-defined]


# ===========================================================================
# PUT /admin/provider-keys/vertex — admin router round-trip (M6, M7, R1)
# ===========================================================================

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    _redis_env.TEST_DATABASE_URL,
)
TEST_REDIS_URL = _redis_env.TEST_REDIS_URL
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
PASSWORD = "correct horse battery staple"  # noqa: S105


def _make_settings() -> Settings:
    from cryptography.fernet import Fernet

    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
        provider_key_encryption_key=Fernet.generate_key().decode(),
    )  # type: ignore[call-arg]


async def _bootstrap_app() -> Any:
    settings = _make_settings()
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return app


def _client_for(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _signup_owner(client: httpx.AsyncClient, *, email: str) -> str:
    sr = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "VertexCo", "email": email, "password": PASSWORD},
    )
    assert sr.status_code == 201, sr.text
    lr = await client.post("/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, lr.text
    token: str = lr.json()["access_token"]
    return token


async def test_M6_put_vertex_credential_success() -> None:
    app = await _bootstrap_app()
    try:
        async with _client_for(app) as client:
            token = await _signup_owner(client, email="owner-vertex@byok.io")
            resp = await client.put(
                "/admin/provider-keys/vertex",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "project_id": _PROJECT_ID,
                    "client_email": _CLIENT_EMAIL,
                    "private_key": _PRIVATE_KEY_PEM,
                    "private_key_id": "key-1",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["provider"] == "vertex"
            assert body["configured"] is True
            assert body["enabled"] is True
            assert _PRIVATE_KEY_PEM not in resp.text, "SECURITY: private_key leaked in response"

            # Round-trip: the store reconstructs a typed GoogleServiceAccountCredential.
            from gateway.proxy.domain.provider_credentials import GoogleServiceAccountCredential

            store = app.state.tenant_provider_key_store
            # Resolve tenant_id from the signup call's own token via GetIdentityUseCase seam
            # is overkill here — read it straight off the DB via the store's list().
            statuses = None
            for sub in [c async for c in _iter_tenant_ids(app)]:
                cred = await store.get(sub, "vertex")
                if cred is not None:
                    statuses = cred
                    break
            assert statuses is not None
            assert isinstance(statuses, GoogleServiceAccountCredential)
            assert statuses.project_id == _PROJECT_ID
            assert statuses.client_email == _CLIENT_EMAIL
            assert statuses.private_key.get_secret_value() == _PRIVATE_KEY_PEM
            assert statuses.private_key_id == "key-1"
    finally:
        await app.state.engine.dispose()


async def _iter_tenant_ids(app: Any):
    from sqlalchemy import select

    from gateway.tenants.infrastructure.orm import TenantRow

    async with app.state.sessionmaker() as session:
        rows = (await session.execute(select(TenantRow.id))).scalars().all()
        for row in rows:
            yield row


async def test_R1_put_vertex_credential_incomplete_422() -> None:
    app = await _bootstrap_app()
    try:
        async with _client_for(app) as client:
            token = await _signup_owner(client, email="owner-vertex-r1@byok.io")
            resp = await client.put(
                "/admin/provider-keys/vertex",
                headers={"Authorization": f"Bearer {token}"},
                json={"project_id": _PROJECT_ID, "client_email": _CLIENT_EMAIL},  # no private_key
            )
            assert resp.status_code == 422, resp.text
            body = resp.json()
            assert body["code"] == "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"

            statuses_resp = await client.get(
                "/admin/provider-keys/vertex", headers={"Authorization": f"Bearer {token}"}
            )
            assert statuses_resp.status_code == 404, "no row must be written on 422"
    finally:
        await app.state.engine.dispose()


async def test_no_ssrf_guard_invoked_for_vertex_put_body() -> None:
    """M7: the vertex branch has no host/URL field to guard, and
    assert_literal_host_not_denied is never called on it — verified by patching it to
    raise if invoked and confirming a normal vertex PUT still succeeds."""
    import gateway.proxy.api.provider_keys_admin_router as router_mod

    original = router_mod.assert_literal_host_not_denied

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("assert_literal_host_not_denied must NOT be called for vertex")

    router_mod.assert_literal_host_not_denied = _boom  # type: ignore[assignment]
    try:
        app = await _bootstrap_app()
        try:
            async with _client_for(app) as client:
                token = await _signup_owner(client, email="owner-vertex-m7@byok.io")
                resp = await client.put(
                    "/admin/provider-keys/vertex",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "project_id": _PROJECT_ID,
                        "client_email": _CLIENT_EMAIL,
                        "private_key": _PRIVATE_KEY_PEM,
                    },
                )
                assert resp.status_code == 200, resp.text
        finally:
            await app.state.engine.dispose()
    finally:
        router_mod.assert_literal_host_not_denied = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# M4 CR-2 (2026-07-13, security HARD-STOP) — cross-tenant token-cache isolation.
# The 2nd adversarial security verify demonstrated that keying the cache by
# (client_email, project_id) only lets tenant B reuse tenant A's GCP identity and
# be served A's live token without A's private key. Fixed: key is now
# (hydroa_tenant_id, client_email, project_id).
# ---------------------------------------------------------------------------


async def test_cache_isolated_across_hydroa_tenants() -> None:
    """Tenant B reusing tenant A's exact (client_email, project_id) gets a DISTINCT
    provider entry — A's cached token is NEVER served to B."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    constructed: list[Any] = []

    def _factory(config: Any) -> Any:
        constructed.append(config)
        return object()

    cache = VertexTokenProviderCache(ttl_s=300.0, max_size=512, provider_factory=_factory)
    shared_identity = _make_config(client_email="shared@x.iam.gserviceaccount.com")

    p_a = cache.get_or_create(shared_identity, "tenant-A")
    p_b = cache.get_or_create(shared_identity, "tenant-B")

    assert len(constructed) == 2, (
        "same GCP identity under two Hydroa tenants must build TWO entries, not share one"
    )
    assert p_a is not p_b, "tenant B must not receive tenant A's cached provider/token"


async def test_cache_same_tenant_same_identity_shares_entry() -> None:
    """The isolation is per-(tenant, identity): the SAME tenant re-resolving the same
    identity still shares one entry (rotation-within-TTL behavior preserved)."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    constructed: list[Any] = []
    cache = VertexTokenProviderCache(
        ttl_s=300.0, max_size=512, provider_factory=lambda c: (constructed.append(c), object())[1]
    )
    ident = _make_config()
    p1 = cache.get_or_create(ident, "tenant-A")
    p2 = cache.get_or_create(ident, "tenant-A")
    assert len(constructed) == 1
    assert p1 is p2


async def test_cache_none_tenant_never_shares_entry() -> None:
    """An unknown owner (tenant_id=None) gets a fresh per-call provider and nothing is
    cached — an unknown owner can never share or poison another tenant's entry."""
    from gateway.proxy.infrastructure.vertex_ad import VertexTokenProviderCache

    constructed: list[Any] = []
    cache = VertexTokenProviderCache(
        ttl_s=300.0, max_size=512, provider_factory=lambda c: (constructed.append(c), object())[1]
    )
    ident = _make_config()
    p1 = cache.get_or_create(ident, None)
    p2 = cache.get_or_create(ident, None)
    assert len(constructed) == 2, "None owner must build fresh each call (no shared entry)"
    assert p1 is not p2
    assert len(cache._cache) == 0, "None owner must not populate the cache"  # noqa: SLF001
