"""Suite-local fixtures for transactional-email (TASK.md §4).

Reuses two established conventions from sibling suites rather than reinventing them:
  - `seeded_tenant`-style signup/login helper (tests/member_invite_issuance's own
    fixture), generalized here to work against ANY (app, client) pair — not just the
    primary top-level `app`/`client` fixtures — because the SMTP-enabled scenarios need
    a SECOND app instance (email_smtp_enabled=True requires a non-empty
    email_smtp_host + dashboard_public_origin at construction; the primary `settings`
    fixture in tests/conftest.py never sets those).
  - `make_second_app_client`-style factory (tests/signup_routing_authz/conftest.py's own
    docstring explains the pattern in full): a SECOND `create_app()` instance sharing the
    SAME Postgres database as the primary `app` fixture, WITHOUT re-running
    drop_all/create_all (the primary `app` fixture already created the schema for this
    test; ASGITransport never triggers lifespan, so schema bootstrap only ever happens
    where a fixture does it explicitly).

No real SMTP/network is ever exercised anywhere in this suite: every SMTP-enabled second
app additionally overrides `app.state.email_sender` with an in-process fake immediately
after construction, so `build_email_sender`'s own selection logic is exercised at
construction time (the only thing scenario 13/M9 cares about — which channel the 201
response reports) without ever letting a real SmtpEmailSender attempt a socket call.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.main import create_app
from tests import _redis_env
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

REDIS_URL = _redis_env.TEST_REDIS_URL


def build_settings(**overrides: Any) -> Settings:
    """Minimal Settings for a second app sharing the primary test DB/Redis/secret."""
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
    """Factory fixture: build (app, client) pairs sharing the primary test DB.

    Deliberately does NOT call Base.metadata.create_all — the primary `app` fixture (a
    dependency of `client`/`db_session`, which every test in this suite also requests)
    already created the schema against the SAME database_url.
    """

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


class FakeEmailSender:
    """In-process EmailSender test double — captures dispatched messages, never touches
    a real socket. Optionally raises (sync) or sleeps-then-appends (to prove fire-and-
    forget timing) per constructor args.
    """

    def __init__(
        self,
        *,
        raises: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.sent: list[Any] = []
        self._raises = raises
        self._delay_seconds = delay_seconds

    async def send(self, message: Any) -> None:
        import asyncio

        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._raises is not None:
            raise self._raises
        self.sent.append(message)


async def seed_tenant(client: httpx.AsyncClient, app: Any, *, suffix: str = "") -> dict[str, Any]:
    """Signup + login helper, generalized over ANY (client, app) pair (mirrors
    tests/member_invite_issuance/test_member_invite_issuance.py's `seeded_tenant` fixture,
    which is bound only to the PRIMARY app/client; this suite also needs it against a
    second, SMTP-enabled app).
    """
    email = f"owner{suffix}@txemail.io"
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": f"TxEmailCo{suffix}",
            "email": email,
            "password": "invite-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": email, "password": "invite-horse-battery-01"},
    )
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]
    tenant_id = uuid.UUID(signup.json()["tenant_id"])
    owner_identity = app.state.token_service.decode(owner_token)

    return {
        "owner_token": owner_token,
        "tenant_id": tenant_id,
        "owner_user_id": owner_identity.user_id,
    }


@pytest.fixture
async def seeded_tenant(client: httpx.AsyncClient, app: Any) -> dict[str, Any]:
    """Signup creates a tenant + owner on the PRIMARY app/client. Byte-identical to
    tests/member_invite_issuance's own fixture of the same name.
    """
    return await seed_tenant(client, app)


@pytest.fixture
async def smtp_app_client(
    make_second_app_client: Any,
    app: Any,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient, FakeEmailSender]]:
    """A second app with email_smtp_enabled=True (boot-validated), its email_sender
    swapped for a FakeEmailSender so NO real socket is ever touched, sharing the primary
    test DB. Disposed on teardown.

    Depends on the top-level `app` fixture (unused directly) purely for ordering: `app`
    is what runs Base.metadata.drop_all/create_all against TEST_DATABASE_URL — this
    second app shares that SAME database_url without re-running create_all itself (see
    module docstring), so the schema must already exist before this fixture builds it.
    """
    application, client = make_second_app_client(
        email_smtp_enabled=True,
        email_smtp_host="smtp.txemail.example",
        dashboard_public_origin="https://app.txemail.example",
    )
    fake = FakeEmailSender()
    application.state.email_sender = fake
    async with client:
        yield application, client, fake
    await application.state.engine.dispose()
