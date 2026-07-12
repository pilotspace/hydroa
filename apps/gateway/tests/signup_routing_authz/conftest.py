"""Suite-local fixtures for signup-routing-authz (signup-and-routing-authz TASK.md §3/§4 — S1).

Two harnesses, reused rather than reinvented:
  - DB-backed signup/invite flow: the top-level `tests/conftest.py` `app`/`client`/`db_session`/
    `settings` fixtures (real Postgres, drop_all/create_all per test). `settings` now carries
    `public_signup_enabled=True` (flipped at the top-level fixture — see tests/conftest.py) so
    every test in this suite starts from the "signup enabled" baseline; flag-OFF tests build
    their OWN app explicitly (see `second_app_client` below).
  - Routing-admin auth: identity JWTs are issued directly via `app.state.token_service.issue()`
    (same pattern as tests/member_invite_issuance/test_member_invite_issuance.py's `_issue_token`)
    — no signup/login round-trip needed for role-only routing-admin checks, since
    `require_superadmin`/`_resolve_identity` only decode the bearer JWT, never look up a DB row.

`second_app_client`: builds a SECOND `create_app()` instance pointed at the SAME Postgres
database as the primary `app` fixture, WITHOUT re-running drop_all/create_all (the primary
`app` fixture already created the schema for this test; ASGITransport does not trigger the
app's lifespan startup, so schema bootstrap only ever happens where a fixture does it
explicitly — mirrors tests/member_invite_acceptance/conftest.py's `_make_app_and_client`,
minus the drop_all/create_all since the schema must NOT be wiped mid-test). Used to prove a
flag flip is genuinely a per-app/per-process knob acting on a shared DB, not a per-request one
(tests 6, 7, 8).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.tenants.domain.entities import Role
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

REDIS_URL = "redis://localhost:6380/9"

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_INVITES = "/admin/invites"
ADMIN_ROUTING = "/admin/routing"

ADA = {"tenant_name": "Acme", "email": "ada@acme.io", "password": "correct horse battery"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def build_settings(*, public_signup_enabled: bool, **overrides: Any) -> Settings:
    """Minimal Settings for a second app sharing the primary test DB/Redis/secret."""
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": REDIS_URL,
        "environment": "test",
        "public_signup_enabled": public_signup_enabled,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def make_second_app_client() -> Any:
    """Factory fixture: build (app, client) pairs sharing the primary test DB.

    Caller is responsible for closing the returned client (`async with` or explicit aclose())
    and disposing the app's engine — use the `second_app_client` fixture below for the common
    single-instance case, or call this factory directly for tests needing more than one
    extra app (e.g. test_bootstrap_flip_on_then_off).

    Deliberately does NOT call Base.metadata.create_all — the primary `app` fixture (a
    dependency of `client`/`db_session`, which every test in this suite also requests) already
    created the schema against the SAME database_url; re-running it here would be redundant
    (and, on drop_all, DESTRUCTIVE) not additive.
    """

    def _factory(*, public_signup_enabled: bool, **settings_overrides: Any) -> Any:
        settings = build_settings(public_signup_enabled=public_signup_enabled, **settings_overrides)
        application = create_app(settings)
        install_stub_resolver(application)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),  # type: ignore[arg-type]
            base_url="http://test",
        )
        return application, client

    return _factory


@pytest.fixture
async def second_app_client(
    make_second_app_client: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """A single flag-OFF second app+client sharing the primary test DB. Disposed on teardown."""
    application, client = make_second_app_client(public_signup_enabled=False)
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

    Mirrors tests/member_invite_issuance/test_member_invite_issuance.py's `_issue_token`.
    Valid for `require_superadmin`/`require_permission`/`_resolve_identity` checks, which
    decode-only (no DB lookup) — but NOT sufficient for endpoints that also look up the
    caller's user/tenant row.
    """
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
        email=email or f"{role.value}@routingtest.io",
    )
    return str(token)
