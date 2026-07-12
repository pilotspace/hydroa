"""Suite-local fixtures/helpers for plan-seat-cap (TASK.md §3 CONTRACT, FROZEN @ v1).

Reuses the top-level `app`/`client`/`db_session`/`settings` fixtures (real Postgres,
`public_signup_enabled=True` baseline — tests/conftest.py) for every seam that needs no
extra feature flag (invite-accept, domain-capture, SCIM). OIDC/SAML need their OWN app
instance (feature-specific Settings, mirrors tests/sso_oidc + tests/saml_sso's own
conventions) — `build_oidc_app`/`build_saml_app` below construct one sharing the SAME
Postgres schema the primary `app` fixture already created (never a second drop/create).

Seat-cap seeding idiom: the tenant OWNER created at signup is already active user #1 —
`seed_extra_active_users` tops a tenant up to `N` total active members via direct SQL
INSERT (mirrors plan_catalog's own `_seed_customer_tenant` "seed the baseline via SQL,
exercise only the LAST admission through the real seam" idiom) rather than replaying N
real invite-accepts/SSO logins per test.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import contextmanager
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

REDIS_URL = "redis://localhost:6380/9"

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"

FAKE_OIDC_ISSUER = "https://fake-idp.test"
FAKE_OIDC_CLIENT_ID = "test-client-id"
FAKE_OIDC_NONCE = "test-nonce-seatcap"
FAKE_OIDC_STATE = "test-state-seatcap"
FAKE_OIDC_SIGNING_KEY = "fake-idp-signing-secret-for-tests"

SAML_SP_ENTITY_ID_BASE = "https://gw.test/saml/sp"
SAML_ACS_URL = "https://gw.test/auth/saml/acs"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """RFC 9457 envelope assertion — status AND the specific `code` field."""
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code!r}: {body}"
    return body


def assert_scim_error(
    resp: httpx.Response, status: int, *, detail_contains: str | None = None
) -> dict[str, Any]:
    """RFC 7644 envelope assertion (scim/api/errors.py) — no `scimType` for a seat-cap
    reject (mirrors scim_invalid_token()'s own no-scimType precedent, TASK.md §3 M5)."""
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("schemas") == ["urn:ietf:params:scim:api:messages:2.0:Error"], body
    assert body.get("status") == str(status), body
    assert "scimType" not in body, f"expected no scimType, got {body}"
    if detail_contains is not None:
        assert detail_contains in body.get("detail", ""), body
    return body


# ---------------------------------------------------------------------------
# Signup / plan / seat seeding
# ---------------------------------------------------------------------------


async def signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str | None = None, email: str | None = None
) -> dict[str, str]:
    """Register a tenant, log in. Returns {tenant_id, owner_token, email}. The owner IS
    active seat #1 for every seat-cap scenario below."""
    name = tenant_name or f"SeatCapCo-{uuid.uuid4().hex[:8]}"
    owner_email = email or f"owner-{uuid.uuid4().hex[:8]}@seatcaptest.io"
    password = "seat-cap-horse-battery-01"
    signup = await client.post(
        SIGNUP, json={"tenant_name": name, "email": owner_email, "password": password}
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post(LOGIN, json={"email": owner_email, "password": password})
    assert login.status_code == 200, f"login failed: {login.text}"
    return {
        "tenant_id": tenant_id,
        "owner_token": login.json()["access_token"],
        "email": owner_email,
    }


async def seed_plan(db_session: AsyncSession, *, name: str, seat_cap: int | None) -> str:
    """Insert a `plans` row directly via ORM (create_all doesn't replay the migration's own
    seed INSERT — mirrors tests/plan_enforcement/conftest.py's own `seed_plan`)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=seat_cap,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)


async def assign_plan(db_session: AsyncSession, *, tenant_id: str, plan_id: str | None) -> None:
    await db_session.execute(
        text("UPDATE tenants SET plan_id = :pid WHERE id = :tid"),
        {"pid": plan_id, "tid": tenant_id},
    )
    await db_session.commit()


async def set_tenant_seat_cap(
    db_session: AsyncSession, *, tenant_id: str, seat_cap: int | None
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET seat_cap = :cap WHERE id = :tid"),
        {"cap": seat_cap, "tid": tenant_id},
    )
    await db_session.commit()


async def seed_extra_active_users(
    db_session: AsyncSession, *, tenant_id: str, count: int, email_prefix: str = "filler"
) -> list[str]:
    """Top a tenant up by `count` MORE active users via direct SQL INSERT — the owner
    (seat #1) is already real; this fills seats 2..N so only the LAST admission under
    test goes through the real seam."""
    emails: list[str] = []
    for i in range(count):
        email = f"{email_prefix}-{uuid.uuid4().hex[:8]}-{i}@seatcaptest.io".lower()
        await db_session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method) "
                "VALUES (:id, :tid, :email, 'x', 'member', 'password')"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "email": email},
        )
        emails.append(email)
    await db_session.commit()
    return emails


async def deactivate_user(db_session: AsyncSession, *, email: str) -> None:
    await db_session.execute(
        text("UPDATE users SET deactivated_at = now() WHERE email = :email"), {"email": email}
    )
    await db_session.commit()


async def active_user_count(db_session: AsyncSession, *, tenant_id: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE tenant_id = :tid AND deactivated_at IS NULL"),
        {"tid": tenant_id},
    )
    return int(result.scalar_one())


async def user_exists(db_session: AsyncSession, *, email: str) -> bool:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email = :email"), {"email": email.lower()}
    )
    return int(result.scalar_one()) > 0


async def audit_event_count(db_session: AsyncSession, *, action: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"), {"action": action}
    )
    return int(result.scalar_one())


async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve (or seed) the reserved platform tenant — mirrors tests/plan_catalog's own
    `platform_tenant_id` fixture (create_all doesn't replay the seed migration)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


def issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required
    (mirrors tests/plan_catalog's own `_issue_token`)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return str(token)


# ---------------------------------------------------------------------------
# SQL-capture — proves "no COUNT(*) query issued" (M2/M8/M9) observably, not
# via an internal mock — a before_cursor_execute hook on the real sync engine.
# ---------------------------------------------------------------------------


@contextmanager
def capture_executed_sql(app: Any) -> Any:
    statements: list[str] = []
    sync_engine = app.state.engine.sync_engine

    def _listener(
        _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: Any
    ) -> None:
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _listener)


# ---------------------------------------------------------------------------
# OIDC — second app instance, shares the primary app's schema/engine
# (mirrors tests/sso_oidc/test_sso_oidc.py's own bootstrap-then-reuse-engine idiom).
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    def __init__(self, *, id_token: str) -> None:
        self._id_token = id_token
        self.calls: list[dict[str, Any]] = []

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        self.calls.append({"code": code, "redirect_uri": redirect_uri})
        return {"id_token": self._id_token, "access_token": "fake-access", "token_type": "bearer"}


def make_oidc_id_token(*, email: str, nonce: str = FAKE_OIDC_NONCE, exp_offset: int = 3600) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "iss": FAKE_OIDC_ISSUER,
        "aud": FAKE_OIDC_CLIENT_ID,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": nonce,
    }
    return pyjwt.encode(claims, FAKE_OIDC_SIGNING_KEY, algorithm="HS256")


def build_oidc_app(
    *, engine: Any, tenant_id: str, domain: str, id_token_email: str
) -> tuple[Any, httpx.AsyncClient]:
    """A SECOND app instance, oidc_enabled=True with a domain_mapping pinning `domain` to
    `tenant_id` — reuses the PRIMARY app's engine (same Postgres schema/data, never a
    second drop/create — the primary app fixture already created it)."""
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=REDIS_URL,
        public_signup_enabled=True,
        oidc_enabled=True,
        oidc_issuer=FAKE_OIDC_ISSUER,
        oidc_client_id=FAKE_OIDC_CLIENT_ID,
        oidc_client_secret="test-client-secret-not-real",  # noqa: S106
        oidc_redirect_uri="http://test/auth/oidc/callback",
        oidc_domain_mapping=json.dumps([{"email_domain": domain, "tenant_id": str(tenant_id)}]),
    )  # type: ignore[call-arg]
    application = create_app(settings)
    install_stub_resolver(application)
    application.state.engine = engine
    application.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_oidc_id_token(email=id_token_email)
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    )
    return application, client


async def oidc_callback(client: httpx.AsyncClient, *, code: str = "good-code") -> httpx.Response:
    return await client.get(
        f"/auth/oidc/callback?code={code}&state={FAKE_OIDC_STATE}",
        cookies={"oidc_state": FAKE_OIDC_STATE, "oidc_nonce": FAKE_OIDC_NONCE},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# SAML — a dedicated app (static SP config, no tenant-id baked into Settings —
# mirrors tests/saml_sso/conftest.py's own `settings`/`app`/`client` fixtures).
# ---------------------------------------------------------------------------


@pytest.fixture
def saml_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=REDIS_URL,
        public_signup_enabled=True,
        saml_sp_entity_id_base=SAML_SP_ENTITY_ID_BASE,
        saml_acs_url=SAML_ACS_URL,
    )  # type: ignore[call-arg]


@pytest.fixture
async def saml_app(saml_settings: Settings) -> AsyncIterator[Any]:
    from gateway.core.db import Base

    application = create_app(saml_settings)
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def saml_client(saml_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=saml_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        yield c


@pytest.fixture
async def saml_db_session(saml_app: Any) -> AsyncIterator[AsyncSession]:
    async with saml_app.state.sessionmaker() as session:
        yield session


async def put_saml_config(
    client: httpx.AsyncClient,
    *,
    owner_token: str,
    idp_x509_cert: str,
    idp_entity_id: str,
    idp_sso_url: str,
    email_domains: list[str],
) -> httpx.Response:
    return await client.put(
        "/admin/saml",
        json={
            "idp_entity_id": idp_entity_id,
            "idp_sso_url": idp_sso_url,
            "idp_x509_cert": idp_x509_cert,
            "email_domains": email_domains,
            "enabled": True,
        },
        headers=bearer(owner_token),
    )


def extract_authn_request_id(location: str) -> str:
    import urllib.parse

    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

    parsed = urllib.parse.urlparse(location)
    qs = urllib.parse.parse_qs(parsed.query)
    saml_request = qs["SAMLRequest"][0]
    xml_bytes = OneLogin_Saml2_Utils.decode_base64_and_inflate(saml_request)
    doc = OneLogin_Saml2_XML.to_etree(xml_bytes)
    request_id: str = doc.get("ID")
    return request_id


async def start_saml_login_and_get_request_id(client: httpx.AsyncClient, *, domain: str) -> str:
    resp = await client.get("/auth/saml/login", params={"domain": domain}, follow_redirects=False)
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    return extract_authn_request_id(resp.headers["location"])


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


# ---------------------------------------------------------------------------
# SCIM
# ---------------------------------------------------------------------------


async def create_scim_token(
    client: httpx.AsyncClient, *, owner_token: str, name: str = "SeatCapIdP"
) -> dict[str, Any]:
    resp = await client.post("/admin/scim/tokens", json={"name": name}, headers=bearer(owner_token))
    assert resp.status_code == 201, f"setup failed creating SCIM token: {resp.text}"
    return resp.json()


def scim_bearer(scim_token: dict[str, Any]) -> dict[str, str]:
    return bearer(str(scim_token["token"]))


__all__ = [
    "FAKE_OIDC_NONCE",
    "FAKE_OIDC_STATE",
    "FakeOidcExchanger",
    "active_user_count",
    "assert_problem",
    "assert_scim_error",
    "assign_plan",
    "audit_event_count",
    "bearer",
    "build_oidc_app",
    "capture_executed_sql",
    "create_scim_token",
    "deactivate_user",
    "extract_authn_request_id",
    "get_cookies_from_response",
    "issue_token",
    "make_oidc_id_token",
    "oidc_callback",
    "platform_tenant_id",
    "put_saml_config",
    "scim_bearer",
    "seed_extra_active_users",
    "seed_plan",
    "set_tenant_seat_cap",
    "signup_owner",
    "start_saml_login_and_get_request_id",
    "user_exists",
]
