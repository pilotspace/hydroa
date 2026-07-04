"""Shared fixtures for superadmin-audit-foundation Part A + Part C (TASK.md §2-§5).

Part A retrofits `resolve_platform_credential` (gateway.ops.api.deps) with a fire-and-
forget, fail-open audit write. Part C retrofits `OidcLoginUseCase`
(gateway.auth.application.use_cases) the same way. Part B (the /admin/auth password-
login-side integration) is explicitly OUT OF SCOPE for this suite — it depends on the
sibling `superadmin-login` task's not-yet-shipped shape and is handled separately.

Fixture provenance (this repo's convention is suite-local fixtures, not cross-suite
imports — matched here rather than importing from sibling test packages):
  - `FakePlatformCredentialResolver` / `platform_resolver` / `minimax_credential` /
    `seed_platform_tenant` — copied verbatim from
    tests/ops_platform_job_identity/conftest.py (Part A's own retrofit-target suite;
    identical fake shape and helper, kept in sync deliberately).
  - `FakeOidcExchanger` / `make_id_token` — adapted (cosmetic renaming only) from
    tests/sso_oidc/test_sso_oidc.py's own helpers (Part C's existing regression suite).
    Part C's own tests construct `OidcLoginUseCase` directly rather than going through
    the router/HTTP/cookie layer that suite uses: direct construction gives tight,
    deterministic control over `user.role` without re-exercising the OIDC protocol flow
    itself (state/nonce cookies, redirects, JWKS) — sso_oidc already exhaustively covers
    that and is re-run unmodified as this task's regression gate. The NEW behavior under
    test here is only the post-issuance audit branch.
  - `session_factory` — thin wrapper exposing `app.state.sessionmaker`, the SAME live
    global both Part A's and Part C's real DI wiring reads (main.py:632-634).
  - `failing_session_factory` — a session_factory whose *call itself* raises, matching
    §2 SCENARIOS' own wording literally ("the supplied session_factory raises on every
    session open"). Deliberately NOT copied from tests/audit/test_audit_store.py's
    `AsyncMock(spec=AsyncSession)` pattern (which configures `.execute` to raise): the
    real `record_audit` body calls only `session.add()` (sync) and `await
    session.commit()` — never `.execute()` — so that existing pattern's `execute`
    side_effect is never actually exercised by the code under test. Raising directly
    from the factory call is simpler and is a REAL, verified failure (this suite's own
    tests assert record_audit's fail-open warning is actually logged, not just that
    nothing crashes).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.config import Settings
from gateway.proxy.domain.provider_credentials import (
    BearerCredential,
    ProviderCredential,
    ProviderKeyMissing,
)

TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"
FAKE_ISSUER = "https://fake-idp.test"
FAKE_CLIENT_ID = "test-client-id"
FAKE_NONCE = "test-nonce-abc123"
FAKE_STATE = "test-state-xyz789"
FAKE_SIGNING_KEY = "fake-idp-signing-secret-for-tests"


# ---------------------------------------------------------------------------
# Part A fixtures — verbatim copies of ops_platform_job_identity/conftest.py
# ---------------------------------------------------------------------------


class FakePlatformCredentialResolver:
    """Structural TenantCredentialResolver fake, programmable per (tenant_id, provider).

    Implements the resolver Protocol directly (.resolve, not .get) since this suite
    exercises resolve_platform_credential ABOVE the resolver layer — identical to
    tests/ops_platform_job_identity/conftest.py's own fixture.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[uuid.UUID, str], ProviderCredential] = {}
        self.calls: list[tuple[uuid.UUID, str]] = []

    def program(self, tenant_id: uuid.UUID, provider: str, credential: ProviderCredential) -> None:
        self._by_key[(tenant_id, provider)] = credential

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        self.calls.append((tenant_id, provider))
        cred = self._by_key.get((tenant_id, provider))
        if cred is None:
            raise ProviderKeyMissing(provider)
        return cred


@pytest.fixture
def platform_resolver() -> FakePlatformCredentialResolver:
    return FakePlatformCredentialResolver()


@pytest.fixture
def minimax_credential() -> BearerCredential:
    return BearerCredential(secret="platform-minimax-secret-XYZ")  # noqa: S106


async def seed_platform_tenant(session: AsyncSession, *, name: str = "Platform") -> uuid.UUID:
    """Insert a kind='platform' tenants row via raw SQL and return its id.

    At most one such row may exist (tenants_platform_kind_uidx partial unique index) —
    call this at most once per test.
    """
    platform_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'platform')"),
        {"id": platform_id, "name": name},
    )
    await session.commit()
    return platform_id


# ---------------------------------------------------------------------------
# Shared: session_factory (real) / failing_session_factory (simulated outage)
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(app: object) -> async_sessionmaker[AsyncSession]:
    """The SAME live sessionmaker real DI wiring reads (app.state.sessionmaker)."""
    return app.state.sessionmaker  # type: ignore[attr-defined,no-any-return]


@pytest.fixture
def failing_session_factory() -> async_sessionmaker[AsyncSession]:
    """A session_factory whose call raises immediately — simulates an audit DB outage
    at the point of "opening" a session (§2 SCENARIOS' own wording).
    """

    def _raise(*_args: object, **_kwargs: object) -> AsyncSession:
        raise RuntimeError("audit db unreachable (simulated outage)")

    return _raise  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Shared: audit_events row fetch helpers (raw SQL — mirrors tests/test_users_role.py's
# own `SELECT action, metadata FROM audit_events ...` + `asyncio.sleep(0.05)` idiom for
# waiting out a fire-and-forget asyncio.ensure_future(record_audit(...)) write).
# ---------------------------------------------------------------------------


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action`, or None.

    Each test in this suite uses a fresh per-test schema (app fixture drop_all/
    create_all) and triggers at most one audit write per distinct action, so
    "most recent" is unambiguous.
    """
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "
            "target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def count_audit_rows(session: AsyncSession, *, action: str) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
        {"action": action},
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Part C fixtures/helpers — adapted from tests/sso_oidc/test_sso_oidc.py
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    """In-process fake for the OidcTokenExchanger port (mirrors sso_oidc's own)."""

    def __init__(
        self,
        *,
        id_token: str | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._id_token = id_token
        self._raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        self.calls.append({"code": code, "redirect_uri": redirect_uri})
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._id_token is not None:
            return {
                "id_token": self._id_token,
                "access_token": "fake-access",
                "token_type": "bearer",
            }
        return {}


def make_id_token(
    *,
    email: str,
    issuer: str = FAKE_ISSUER,
    audience: str = FAKE_CLIENT_ID,
    nonce: str = FAKE_NONCE,
    exp_offset: int = 3600,
) -> str:
    """Mint a fake HS256 ID token (v4 TLS-channel mode — signature intentionally
    unverified by the use case when jwks_client=None; mirrors sso_oidc's own helper).
    """
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": nonce,
    }
    return jwt.encode(claims, FAKE_SIGNING_KEY, algorithm="HS256")


def make_oidc_use_case_settings(*, domain_mapping: list[dict[str, str]]) -> Settings:
    """Settings tuned for direct OidcLoginUseCase construction.

    v4 TLS-channel mode (no oidc_jwks_url) — jwks_client stays None, signature
    verification inactive, exactly as most of sso_oidc's own scenarios exercise.
    environment="test" (also exempt by default via the "dev" default) keeps the
    unrelated __init__-time signature-skip warning out of caplog-based assertions.
    """
    return Settings(
        jwt_secret=TEST_JWT_SECRET,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret="test-client-secret-not-real",  # noqa: S106
        oidc_redirect_uri="http://gateway.test/auth/oidc/callback",
        oidc_domain_mapping=json.dumps(domain_mapping),
        environment="test",
    )


async def seed_customer_tenant(session: AsyncSession, *, name: str = "Customer") -> uuid.UUID:
    """Insert an ordinary kind='customer' tenants row via raw SQL and return its id."""
    tenant_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tenant_id, "name": name},
    )
    await session.commit()
    return tenant_id


async def seed_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert one users row directly (mirrors tests/sso_oidc's own S16 seed pattern).

    email MUST already be lowercase (users_email_lowercase_check CHECK constraint).
    role='superadmin' is only legal when tenant_id is the sole kind='platform' tenant
    (DB trigger, migration 5b34ca5e1c4b) — callers are responsible for that pairing.
    """
    assert email == email.lower(), "seed_user: email must be pre-lowercased"
    resolved_user_id = user_id or uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, :email, :ph, :role)"
        ),
        {
            "id": resolved_user_id,
            "tid": tenant_id,
            "email": email,
            "ph": "!sso-no-password",
            "role": role,
        },
    )
    await session.commit()
    return resolved_user_id
