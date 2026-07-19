"""Suite-local fixtures for domain-auto-assign-login (TASK.md §3/§4 — FROZEN @ v1).

Duplicated (not cross-imported) from tests/domain_routing_unification/conftest.py —
mirrors this repo's own established convention (that conftest's own docstring:
"sibling suites in this repo consistently duplicate this tiny fixture rather than
cross-import between test packages"). This suite needs the SAME three-way merge
(OIDC Fernet-encrypted per-tenant config + SAML real-Redis pending/replay stores +
domain_capture DNS-TXT verification) plus the FakeExchanger OIDC-callback seam and
a real signed-SAML-assertion round trip (tests/saml_sso/saml_fixtures.py).

Real Postgres at GATEWAY_TEST_DATABASE_URL; real Redis at TEST_REDIS_URL (saml:*
namespace only); httpx.ASGITransport (no network).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
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
# through this exact key when insert_oidc_config_row inserts a row directly.
TEST_FERNET_KEY = Fernet.generate_key()

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
DOMAIN_CLAIMS = "/admin/domain-claims"
OIDC_LOGIN = "/auth/oidc/login"
OIDC_CALLBACK = "/auth/oidc/callback"
SAML_LOGIN = "/auth/saml/login"
SAML_ACS = "/auth/saml/acs"
ADMIN_OIDC = "/admin/oidc"
ADMIN_SAML = "/admin/saml"

DEFAULT_PASSWORD = "correct horse battery staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeDnsResolver:
    """Deterministic DnsTxtResolver test double — duplicated per repo convention
    (see tests/domain_routing_unification/conftest.py's own docstring)."""

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


async def _clear_saml_redis_keys() -> None:
    r = aioredis.from_url(TEST_REDIS_URL)
    try:
        keys = [k async for k in r.scan_iter(match="saml:*", count=1000)]
        if keys:
            await r.delete(*keys)
    except Exception:  # noqa: BLE001 — best-effort cleanup, never fail a test on this
        pass
    finally:
        await r.aclose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
        saml_sp_entity_id_base=SP_ENTITY_ID_BASE,
        saml_acs_url=ACS_URL,
        oidc_config_encryption_key=TEST_FERNET_KEY.decode(),
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    await _clear_saml_redis_keys()
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()
    await _clear_saml_redis_keys()


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
    caller's tenant — the ONLY way, under this task's contract, that a tenant may
    legitimately route SSO or pass the PUT /admin/{oidc,saml} write-time gate.
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
    same rationale as tests/domain_routing_unification/conftest.py's own helper."""
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


async def put_saml_config_with_keypair(
    client: httpx.AsyncClient,
    *,
    owner_token: str,
    email_domains: list[str],
) -> tuple[httpx.Response, Any]:
    """PUT /admin/saml with a freshly generated IdP keypair — returns (response,
    keypair) so the caller can sign a real Assertion with the SAME private key
    the tenant's config trusts (cert_pem is what gets stored/validated against).
    """
    from tests.saml_sso.saml_fixtures import generate_idp_keypair

    keypair = generate_idp_keypair()
    body = {
        "idp_entity_id": "https://fake-idp.test/entity",
        "idp_sso_url": "https://fake-idp.test/sso",
        "idp_x509_cert": keypair.cert_pem,
        "email_domains": email_domains,
        "enabled": True,
    }
    resp = await client.put(ADMIN_SAML, json=body, headers=bearer(owner_token))
    return resp, keypair


def assert_problem(resp: httpx.Response, status: int, code: str | None = None) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    if code is not None:
        assert body.get("code") == code, f"expected code={code}: {body}"
    return body


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
