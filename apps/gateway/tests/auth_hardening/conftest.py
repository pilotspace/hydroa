"""Suite-local fixtures for auth-hardening-login-sessions (TASK.md §CHECKS — SECURITY,
direction beat, red-first).

Reuses the established sibling conventions rather than reinventing them:
  - second-app factory sharing the primary test DB WITHOUT re-running create_all —
    mirrors tests/scoped_self_serve_signup/conftest.py's `make_second_app_client` exactly.
  - `FakeEmailSender` imported cross-suite from tests/transactional_email/conftest.py
    (the copy with `delay_seconds`, same import sibling suites already use).
  - problem+json assertion mirrors tests/signup_routing_authz's `_assert_problem`.

RED at the current tree: /admin/auth/password-reset, /password-reset/confirm and
/admin/auth/logout do not exist (plain 404), /admin/auth/login has no limiter, and issued
JWTs carry no jti — every test below fails on those observable gaps, never on fixture
setup. DO NOT weaken these tests to pass; that is Build's job.

NOTE (pre-Build): `Settings.model_config` sets `extra="ignore"`, so the new knobs passed
below (login_ip_rpm, login_email_rpm, password_reset_*_rpm,
session_revocation_check_timeout_seconds) are silently accepted before Build adds the
fields — the fixtures never fail to construct pre- or post-Build; redness comes from the
missing behavior, not from Settings.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.tenants.infrastructure.orm import TenantRow, UserRow
from tests import _redis_env
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver
from tests.transactional_email.conftest import FakeEmailSender

REDIS_URL = _redis_env.TEST_REDIS_URL

LOGIN = "/admin/auth/login"
ME = "/admin/auth/me"
RESET = "/admin/auth/password-reset"
RESET_CONFIRM = "/admin/auth/password-reset/confirm"
LOGOUT = "/admin/auth/logout"

# >= MIN_PASSWORD_LENGTH (10, tenants/domain/entities.py).
PASSWORD = "hardening-horse-battery-01"
NEW_PASSWORD = "hardening-staple-rotated-02"
WEAK_PASSWORD = "short"

# Same character length by construction (16) — makes body byte-identity comparisons
# between the registered/unknown branches meaningful rather than coincidental.
KNOWN_EMAIL = "known@hardn.io"
GHOST_EMAIL = "ghost@hardn.io"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{40,}")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"{resp.status_code} != {status}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"code {body.get('code')!r} != {code!r}: {body}"
    return body


def extract_reset_token(message: Any) -> str:
    """Pull the raw high-entropy reset token out of a captured EmailMessage — same
    extraction idiom as tests/scoped_self_serve_signup's `extract_confirm_token`."""
    haystack = f"{message.text_body or ''}\n{message.html_body or ''}"
    match = _TOKEN_RE.search(haystack)
    assert match is not None, f"expected a URL-safe reset token in the email body: {haystack!r}"
    return match.group(0)


class CountingHasher:
    """Pass-through PasswordHasher spy counting verify() calls (M1/A8: a limited attempt
    must never reach the hasher)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.verify_calls = 0

    def hash(self, password: str) -> str:
        return self._inner.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        self.verify_calls += 1
        return self._inner.verify(password_hash, password)


class BrokenRedis:
    """Raises RedisError on every command — drives the limiter's documented fail-open."""

    def __getattr__(self, name: str) -> Any:
        async def _raise(*args: Any, **kwargs: Any) -> Any:
            from redis.exceptions import RedisError

            raise RedisError("redis down (test double)")

        return _raise


def build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": REDIS_URL,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def make_hardening_app() -> Any:
    """Factory: (app, client, fake_email_sender) sharing the primary test DB. Caller
    closes the client and disposes the engine; see `hardening_app` below."""

    def _factory(**settings_overrides: Any) -> tuple[Any, httpx.AsyncClient, FakeEmailSender]:
        settings = build_settings(**settings_overrides)
        application = create_app(settings)
        install_stub_resolver(application)
        fake = FakeEmailSender()
        application.state.email_sender = fake
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),  # type: ignore[arg-type]
            base_url="http://test",
        )
        return application, client, fake

    return _factory


@pytest.fixture
async def hardening_app(
    make_hardening_app: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, FakeEmailSender]]:
    """Default-knob app+client with a FakeEmailSender. Disposed on teardown."""
    application, client, fake = make_hardening_app()
    async with client:
        yield application, client, fake
    await application.state.engine.dispose()


@pytest.fixture
async def low_rpm_app(
    make_hardening_app: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, FakeEmailSender]]:
    """Artificially low login/reset limiter knobs (=2 each) — mirrors sibling
    `low_rpm_app_client` shape."""
    application, client, fake = make_hardening_app(
        login_ip_rpm=2,
        login_email_rpm=2,
        password_reset_ip_rpm=2,
        password_reset_email_rpm=2,
    )
    async with client:
        yield application, client, fake
    await application.state.engine.dispose()


async def seed_password_user(
    db_session: AsyncSession,
    application: Any,
    *,
    email: str = KNOWN_EMAIL,
    password: str = PASSWORD,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a customer tenant + active password user via ORM (create_all does not replay
    migration seeds). Hash minted through the app's own hasher instance."""
    tenant = TenantRow(id=uuid.uuid4(), name="Hardening Co", kind="customer")
    db_session.add(tenant)
    await db_session.flush()
    user = UserRow(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=email.lower(),
        password_hash=application.state.password_hasher.hash(password),
        role="owner",
    )
    db_session.add(user)
    await db_session.commit()
    return tenant.id, user.id


async def count_rows(db_session: AsyncSession, table: str) -> int:
    return int((await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
