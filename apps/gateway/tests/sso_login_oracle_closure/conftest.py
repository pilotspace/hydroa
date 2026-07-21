"""Suite-local fixtures for sso-login-oracle-closure (TASK.md §3 — FROZEN @ v1).

Mirrors the fixture SHAPES of tests/domain_routing_unification/conftest.py by
COPYING the pattern (never cross-importing between test packages — the
convention this repo already follows for FakeDnsResolver and friends).

What this suite needs that no sibling conftest already combines:
  - OIDC per-tenant config (Fernet key) + domain_capture DNS-TXT claim/verify,
  - the SAME app wired under BOTH Settings.oidc_enabled=False (the production
    shape) and Settings.oidc_enabled=True (the e2e/test shape), because the
    §3 ANTI-ORACLE INVARIANT is asserted to be deployment-INDEPENDENT.

The env-enabled shape is selected per-test by parametrizing the
`oidc_env_enabled` fixture directly:

    @pytest.mark.parametrize("oidc_env_enabled", [True])

Real Postgres at GATEWAY_TEST_DATABASE_URL; httpx.ASGITransport (no network).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.domain_capture.domain.errors import DnsLookupFailedError
from gateway.main import create_app
from tests import _redis_env

TEST_DATABASE_URL = os.environ.get("GATEWAY_TEST_DATABASE_URL", _redis_env.TEST_DATABASE_URL)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_REDIS_URL = _redis_env.TEST_REDIS_URL

SP_ENTITY_ID_BASE = "https://gw.test/saml/sp"
ACS_URL = "https://gw.test/auth/saml/acs"

# Deterministic Fernet key for the whole suite — OIDC client_secret_enc round-trips
# through this exact key when a test inserts a config row directly.
TEST_FERNET_KEY = Fernet.generate_key()

# Env-Settings OIDC fallback identity (only used when oidc_enabled=True).
ENV_ISSUER = "https://env-idp.test"
ENV_AUTHORIZE_URL = "https://env-idp.test/authorize"
ENV_CLIENT_ID = "env-client-id"
ENV_CLIENT_SECRET = "env-client-secret-never-returned"  # noqa: S105
ENV_REDIRECT_URI = "https://gw.test/auth/oidc/callback"

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
DOMAIN_CLAIMS = "/admin/domain-claims"
OIDC_LOGIN = "/auth/oidc/login"
SAML_LOGIN = "/auth/saml/login"

DEFAULT_PASSWORD = "correct horse battery staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeDnsResolver:
    """Deterministic DnsTxtResolver test double — mirrors
    tests/domain_routing_unification/conftest.py EXACTLY (duplicated rather than
    imported: sibling suites in this repo consistently duplicate this fixture)."""

    def __init__(self) -> None:
        self._records: dict[str, list[str]] = {}
        self._timeout_names: set[str] = set()

    def set_record(self, name: str, token: str) -> None:
        self._records[name] = [f"ai-proxy-domain-verification={token}"]

    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109
        if name in self._timeout_names:
            raise DnsLookupFailedError(f"stub: simulated resolver timeout for {name!r}")
        return list(self._records.get(name, []))


def _record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


@pytest.fixture
def oidc_env_enabled() -> bool:
    """Whether Settings.oidc_enabled is True for this test.

    Defaults to False — the PRODUCTION shape (GATEWAY_OIDC_ENABLED appears in no
    chart values file; the config.py default governs). Parametrize to [True] to
    exercise the e2e/test shape.
    """
    return False


@pytest.fixture
def settings(oidc_env_enabled: bool) -> Settings:
    env_kwargs: dict[str, Any] = {}
    if oidc_env_enabled:
        env_kwargs = {
            "oidc_enabled": True,
            "oidc_issuer": ENV_ISSUER,
            "oidc_authorize_url": ENV_AUTHORIZE_URL,
            "oidc_client_id": ENV_CLIENT_ID,
            "oidc_client_secret": ENV_CLIENT_SECRET,
            "oidc_redirect_uri": ENV_REDIRECT_URI,
        }
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
        saml_sp_entity_id_base=SP_ENTITY_ID_BASE,
        saml_acs_url=ACS_URL,
        oidc_config_encryption_key=TEST_FERNET_KEY.decode(),
        **env_kwargs,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gw.test", follow_redirects=False
    ) as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session


@pytest.fixture
def fake_dns(app: Any) -> FakeDnsResolver:
    resolver = FakeDnsResolver()
    app.state.dns_resolver = resolver
    return resolver


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> tuple[uuid.UUID, str]:
    """Bootstrap a REAL tenant+owner and log in; returns (tenant_id, bearer_token)."""
    signup_resp = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password}
    )
    assert signup_resp.status_code == 201, signup_resp.text
    tenant_id = uuid.UUID(signup_resp.json()["tenant_id"])

    login_resp = await client.post(LOGIN, json={"email": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    return tenant_id, str(login_resp.json()["access_token"])


async def claim_and_verify_domain(
    client: httpx.AsyncClient,
    fake_dns: FakeDnsResolver,
    *,
    owner_token: str,
    domain: str,
) -> str:
    """Real DNS-TXT claim+verify round trip (domain_capture) — returns claim_id.

    After this call, `domain` has a VERIFIED tenant_domain_claims row for the
    caller's tenant — the state that, combined with NO enabled OIDC config, is
    the leaking leg this task closes.
    """
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    assert create.status_code == 201, create.text
    claim_id: str = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)

    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(owner_token))
    assert verify.status_code == 200, verify.text
    return claim_id


async def insert_oidc_config_row(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email_domains: list[str],
    issuer: str = "https://idp.test",
    client_id: str = "test-client-id",
    client_secret: str = "test-client-secret-never-returned",  # noqa: S107
    token_url: str = "https://idp.test/token",
    jwks_url: str = "https://idp.test/.well-known/jwks.json",
    enabled: bool = True,
) -> None:
    """Insert an oidc_provider_configs row DIRECTLY, bypassing PUT /admin/oidc —
    simulates pre-existing/legacy data the write-time gate could never produce.
    """
    from gateway.auth.infrastructure.orm import OidcProviderConfigRow

    fernet = Fernet(TEST_FERNET_KEY)
    row = OidcProviderConfigRow(
        tenant_id=tenant_id,
        issuer=issuer,
        client_id=client_id,
        client_secret_enc=fernet.encrypt(client_secret.encode()),
        token_url=token_url,
        jwks_url=jwks_url,
        email_domains=email_domains,
        enabled=enabled,
    )
    db_session.add(row)
    await db_session.commit()


def get_cookies_from_response(resp: httpx.Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        parts = header.split(";")
        if parts:
            name_value = parts[0].strip()
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                cookies[name.strip()] = value.strip()
    return cookies
