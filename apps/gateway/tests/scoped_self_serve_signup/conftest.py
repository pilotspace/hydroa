"""Suite-local fixtures for scoped-self-serve-signup (scoped-self-serve-signup TASK.md
§3/§4 — SECURITY, FROZEN @ v1).

Reuses three established conventions from sibling suites rather than reinventing them:
  - `make_second_app_client` factory: a SECOND `create_app()` instance sharing the SAME
    Postgres database as the primary `app`/`client`/`db_session` fixtures (tests/conftest.py),
    WITHOUT re-running drop_all/create_all — mirrors
    tests/signup_routing_authz/conftest.py's own factory of the same name exactly. The
    business/global `public_signup_enabled` flag is deliberately left at its production
    default (False) on every app this suite builds — proves M1's "zero coupling" by
    construction: every flag-ON test in this suite succeeds WITHOUT ever touching
    `public_signup_enabled`.
  - `FakeEmailSender`: imported cross-suite from tests/transactional_email/conftest.py
    (already reused this way by tests/invite_by_domain/test_invite_by_domain.py, which
    imports the sibling tests/domain_capture/conftest.py's own copy) — chosen over
    tests/domain_capture/conftest.py's copy because it alone supports `delay_seconds`
    (sleeps-then-appends), needed for this suite's structural fire-and-forget timing test
    (§3's ⚠ M6 timing-mask flag — see test_email_dispatch_never_blocks_the_response).
  - `_seed_free_plan`: mirrors tests/account_type_discriminator/test_account_type_
    discriminator.py's own idiom exactly (create_all doesn't replay the migration seed;
    the personal-signup path needs a real `free` PlanRow to resolve against).

RED at Ground SHA 8daf22c: neither `/admin/auth/signup/confirm` nor the new
`account_type=='personal' AND public_signup_personal_enabled` branch exist yet — every
request in this suite that depends on either hits a plain 404 (wrong body) or falls through
to the untouched S1 gate, not the assertions below. DO NOT weaken these tests to pass —
that is Build's job.
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
from gateway.tenants.infrastructure.orm import PlanRow
from tests import _redis_env
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver
from tests.transactional_email.conftest import FakeEmailSender

REDIS_URL = _redis_env.TEST_REDIS_URL

SIGNUP = "/admin/auth/signup"
CONFIRM = "/admin/auth/signup/confirm"

# >= MIN_PASSWORD_LENGTH (10, tenants/domain/entities.py) for every "valid" body below.
PASSWORD = "scoped-signup-horse-battery-01"
WEAK_PASSWORD = "short"

# Same character length by construction (13) — makes a Content-Length comparison between
# the two branches of the uniform-response test meaningful rather than coincidental.
FRESH_EMAIL = "fresh@acme.io"
TAKEN_EMAIL = "taken@acme.io"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{40,}")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def personal_body(
    *, email: str, password: str = PASSWORD, tenant_name: str = "Scoped Personal Co"
) -> dict[str, Any]:
    return {
        "tenant_name": tenant_name,
        "email": email,
        "password": password,
        "account_type": "personal",
    }


def business_body(
    *, email: str, password: str = PASSWORD, tenant_name: str = "Scoped Business Co"
) -> dict[str, Any]:
    return {
        "tenant_name": tenant_name,
        "email": email,
        "password": password,
        "account_type": "business",
    }


def build_settings(*, public_signup_personal_enabled: bool, **overrides: Any) -> Settings:
    """Minimal Settings for a second app sharing the primary test DB/Redis/secret.

    `public_signup_enabled` (the EXISTING business/global flag) is never set here — it
    stays at Settings' own production default (False) unless a specific test explicitly
    overrides it, so a passing suite proves the new flag never leans on the old one.
    """
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": REDIS_URL,
        "public_signup_personal_enabled": public_signup_personal_enabled,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def make_second_app_client() -> Any:
    """Factory fixture: build (app, client, fake_email_sender) triples sharing the primary
    test DB. Caller is responsible for closing the client (`async with`) and disposing the
    app's engine — see `scoped_app_client` / `low_rpm_app_client` below for the common
    single-instance case.

    Deliberately does NOT call Base.metadata.create_all — the primary `app` fixture (a
    dependency of `client`/`db_session`, which every test in this suite also requests)
    already created the schema against the SAME database_url.
    """

    def _factory(
        *, public_signup_personal_enabled: bool, **settings_overrides: Any
    ) -> tuple[Any, httpx.AsyncClient, FakeEmailSender]:
        settings = build_settings(
            public_signup_personal_enabled=public_signup_personal_enabled, **settings_overrides
        )
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
async def scoped_app_client(
    make_second_app_client: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, FakeEmailSender]]:
    """The primary flag-ON app+client for this suite: public_signup_personal_enabled=True,
    default rpm knobs, an installed FakeEmailSender for out-of-band capture. Disposed on
    teardown."""
    application, client, fake = make_second_app_client(public_signup_personal_enabled=True)
    async with client:
        yield application, client, fake
    await application.state.engine.dispose()


@pytest.fixture
async def low_rpm_app_client(
    make_second_app_client: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, FakeEmailSender]]:
    """Flag-ON app with artificially low personal_signup_ip_rpm / _email_rpm / _confirm_rpm
    (=2 each) — mirrors tests/member_invite_acceptance/conftest.py's own
    `low_rpm_app_and_client` shape exactly.

    NOTE (pre-Build): `Settings.model_config` sets `extra="ignore"` (core/config.py:84), so
    passing these three kwargs before Build adds the corresponding fields is silently
    accepted (no ValidationError) — this fixture never fails to construct, pre- or
    post-Build. What makes the rate-limit tests correctly RED pre-Build is that the new
    branch / `/admin/auth/signup/confirm` do not exist yet, not this fixture.
    """
    application, client, fake = make_second_app_client(
        public_signup_personal_enabled=True,
        personal_signup_ip_rpm=2,
        personal_signup_email_rpm=2,
        personal_signup_confirm_rpm=2,
    )
    async with client:
        yield application, client, fake
    await application.state.engine.dispose()


async def _seed_free_plan(db_session: AsyncSession) -> uuid.UUID:
    """Seed the `free` plan via ORM (create_all doesn't replay the migration seed) —
    mirrors tests/account_type_discriminator/test_account_type_discriminator.py's own
    `_seed_free_plan` idiom exactly."""
    row = PlanRow(
        id=uuid.uuid4(),
        name="free",
        display_name="Free",
        seat_cap=1,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _row_counts(db_session: AsyncSession) -> tuple[int, int]:
    tenants = (await db_session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    users = (await db_session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    return int(tenants), int(users)


async def _pending_count(db_session: AsyncSession, *, email: str | None = None) -> int:
    if email is None:
        return int(
            (await db_session.execute(text("SELECT count(*) FROM pending_personal_signups")))
            .scalar_one()
        )
    return int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM pending_personal_signups WHERE email = :email"),
                {"email": email},
            )
        ).scalar_one()
    )


async def _pending_row(db_session: AsyncSession, *, email: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT email, tenant_name, password_hash, confirm_token_hash, expires_at "
                "FROM pending_personal_signups WHERE email = :email"
            ),
            {"email": email},
        )
    ).fetchone()


async def _tenant_row_by_email(db_session: AsyncSession, email: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT t.id, t.account_type, t.plan_id FROM tenants t "
                "JOIN users u ON u.tenant_id = t.id WHERE u.email = :email"
            ),
            {"email": email},
        )
    ).one()


def extract_confirm_token(message: Any) -> str:
    """Pull the raw, high-entropy confirm token out of a captured EmailMessage.

    `secrets.token_urlsafe(32)` (the contracted generator, §3) emits ~43 chars drawn from
    `[A-Za-z0-9_-]` — mirrors tests/invite_by_domain/test_invite_by_domain.py's own
    `re.search(r"\\b(\\d{6})\\b", ...)` extraction idiom for the (differently-entropy-classed)
    member-verify code, generalized to this token's charset/length instead of assuming any
    particular template wording.
    """
    haystack = f"{message.text_body or ''}\n{message.html_body or ''}"
    match = _TOKEN_RE.search(haystack)
    assert match is not None, f"expected a URL-safe confirm token in the email body: {haystack!r}"
    return match.group(0)


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Mirrors tests/signup_routing_authz/test_signup_routing_authz.py's own
    `_assert_problem` exactly (status + code + the body's own duplicated `status` field —
    gateway.core.errors.ProblemError always includes both)."""
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    assert body.get("status") == status
    return body
