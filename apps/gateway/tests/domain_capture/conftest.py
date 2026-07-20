"""Suite-local fixtures for domain-capture (domain-capture TASK.md §3/§4 — FROZEN @ v1).

Reuses the top-level `app`/`client`/`db_session`/`settings` fixtures (real Postgres,
`public_signup_enabled=True` baseline — tests/conftest.py). Two extra pieces this suite
needs that no sibling suite has:

  - `fake_dns`: a FakeDnsResolver test double installed on `app.state.dns_resolver`
    BEFORE any request — deterministic control over the M6/M13 DNS TXT lookup without a
    real network call (the actual dnspython adapter is exercised separately if desired;
    this suite's job is the APPLICATION behavior around the DNS answer, not dnspython
    itself).
  - `make_second_app_client` / `low_limit_client`: a SECOND `create_app()` instance
    sharing the SAME Postgres database as the primary `app` fixture (mirrors
    tests/signup_routing_authz/conftest.py's own pattern EXACTLY) — used for (a) the
    flag-OFF verified-domain-still-joins scenario and (b) a low-rpm rate-limit knob so
    scenario 14 doesn't need 11 real requests against the default rpm=10.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.domain_capture.domain.errors import DnsLookupFailedError
from gateway.main import create_app
from gateway.tenants.domain.entities import Role
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver
from tests import _redis_env

REDIS_URL = _redis_env.TEST_REDIS_URL

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
DOMAIN_CLAIMS = "/admin/domain-claims"

DEFAULT_PASSWORD = "correct horse battery staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeDnsResolver:
    """Deterministic DnsTxtResolver test double — no real network IO.

    `set_record(name, value)` — the next lookup for `name` returns `[value]`.
    `fail_timeout(name)` — the next lookup for `name` raises DnsLookupFailedError
    (simulates M13's fail-closed timeout/resolver-error path).
    Any unconfigured name returns `[]` (simulates a missing/NXDOMAIN record).
    """

    def __init__(self) -> None:
        self._records: dict[str, list[str]] = {}
        self._timeout_names: set[str] = set()

    def set_record(self, name: str, token: str) -> None:
        """`token` is the raw value the caller wants "published" — this stub prefixes it
        with `ai-proxy-domain-verification=` itself, mirroring what a real TXT record's
        full string content would contain (the SAME prefix VerifyDomainClaimUseCase
        expects), so callers only ever need to pass the bare token extracted from a
        create-response's `dns_record_value`."""
        self._records[name] = [f"ai-proxy-domain-verification={token}"]

    def fail_timeout(self, name: str) -> None:
        self._timeout_names.add(name)

    def clear_timeout(self, name: str) -> None:
        self._timeout_names.discard(name)

    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — mirrors the real DnsTxtResolver Protocol's own signature
        if name in self._timeout_names:
            raise DnsLookupFailedError(f"stub: simulated resolver timeout for {name!r}")
        return list(self._records.get(name, []))


@pytest.fixture
def fake_dns(app: Any) -> FakeDnsResolver:
    resolver = FakeDnsResolver()
    app.state.dns_resolver = resolver
    return resolver


class FakeEmailSender:
    """In-process EmailSender test double for domain-verify-notify (TASK.md §4) — captures
    dispatched messages, never touches a real socket. Mirrors
    tests/transactional_email/conftest.py's own FakeEmailSender shape exactly.
    """

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture
def fake_email_sender() -> FakeEmailSender:
    return FakeEmailSender()


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> tuple[uuid.UUID, str]:
    """Bootstrap a REAL tenant+owner (via the flag-ON primary client) and log in.

    Returns (tenant_id, owner_bearer_token). A real signup+login round trip — not a
    directly-issued JWT — because tenant_domain_claims.tenant_id/created_by_user_id are
    FK-constrained to real tenants/users rows (M1's schema, enforced even in tests
    against the real Postgres harness)."""
    signup_resp = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password}
    )
    assert signup_resp.status_code == 201, signup_resp.text
    tenant_id = uuid.UUID(signup_resp.json()["tenant_id"])

    login_resp = await client.post(LOGIN, json={"email": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    return tenant_id, str(login_resp.json()["access_token"])


def build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": REDIS_URL,
        "environment": "test",
        "public_signup_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def make_second_app_client() -> Any:
    """Factory: build (app, client) pairs sharing the primary test DB — mirrors
    tests/signup_routing_authz/conftest.py's own `make_second_app_client` EXACTLY.
    Deliberately does NOT call Base.metadata.create_all — the primary `app` fixture
    already created the schema for this test."""

    def _factory(**settings_overrides: Any) -> Any:
        settings = build_settings(**settings_overrides)
        application = create_app(settings)
        install_stub_resolver(application)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),  # type: ignore[arg-type]
            base_url="http://test",
        )
        return application, client

    return _factory


@pytest.fixture
async def second_app_client_signup_disabled(
    make_second_app_client: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """A flag-OFF (public_signup_enabled=False) second app+client sharing the primary
    test DB — used for the "verified domain still joins while invite-only" scenario and
    the S1-regression scenarios (M8)."""
    application, client = make_second_app_client(public_signup_enabled=False)
    async with client:
        yield application, client
    await application.state.engine.dispose()


@pytest.fixture
async def low_limit_client(
    make_second_app_client: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """A second app+client sharing the primary test DB with domain_claim_create_rpm=1 /
    domain_claim_verify_rpm=1 — makes scenario 14 (rate limiting) deterministic in 2
    requests instead of needing to exhaust the production default of 10/30 per hour."""
    application, client = make_second_app_client(
        domain_claim_create_rpm=1, domain_claim_verify_rpm=1
    )
    async with client:
        yield application, client
    await application.state.engine.dispose()


def issue_token(
    app: Any,
    *,
    role: Role = Role.OWNER,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> str:
    """Issue a real HS256 JWT directly via the app's token_service — no DB round-trip.

    Mirrors tests/signup_routing_authz/conftest.py's own `issue_token`. Valid for
    `_get_owner_identity`'s role-only check (decode-only, no DB lookup for a
    non-impersonation identity) — used ONLY for the non-OWNER/missing-token rejection
    scenarios that never reach a real DB write.
    """
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
        email=email or f"{role.value}@domaincapturetest.io",
    )
    return str(token)
