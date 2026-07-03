"""RED->GREEN suite for superadmin-audit-foundation Part B (TASK.md §2/§3 — FROZEN @ v1).

Part B retrofits `LoginUseCase` (gateway.tenants.application.use_cases) — the
`/admin/auth/login` password path — with a new required, keyword-only `session_factory`
constructor param, wired at its one DI construction site
(gateway.tenants.api.deps::get_login_use_case, which gains a `request: Request` param to
reach `request.app.state.sessionmaker` — mirroring `get_oidc_use_case_with_config`), plus
a post-issuance fire-and-forget, fail-open audit-write branch identical in kind to Part
C's OIDC-side branch (test_part_c_oidc_login_audit.py) except `auth_method="password"`.
Part A (ops-side retrofit) and Part C (OIDC-side) are covered elsewhere in this directory.

RED before BUILD: `LoginUseCase.__init__` does not accept a `session_factory` keyword
yet. Tests 1-3 (below) drive the use case indirectly through the real router/DI chain, so
pre-build they would NOT fail from an arity error the way Part A/C's direct-construction
tests do — a naive "assert 0 audit rows" negative test would pass vacuously pre-build
(zero audit code also produces zero rows). To keep every test genuinely RED for the right
reason, tests 2 and 3 each include a POSITIVE CONTROL: a real superadmin login in the same
test/schema that must itself be audited before the negative assertion is checked — pre-
build this control fails (0 rows, not 1), so the whole test fails for the right reason
(missing feature), not a coincidence. Test 4 constructs `LoginUseCase(..., session_factory=
...)` directly via a dependency override (see below) and fails immediately pre-build with
TypeError: __init__() got an unexpected keyword argument 'session_factory'. Test 1 fails
pre-build simply because no row is ever written. See the Build report for the literal
errors observed.

Test strategy — HTTP-level, unlike Part C's direct-construction approach: §2 SCENARIOS'
own wording for all four Part-B scenarios says "via POST /admin/auth/login" (contrast
Part C's "OidcLoginUseCase.execute(...) completes"), and unlike OIDC there is no protocol
machinery (state/nonce/JWKS/cookies) that direct construction needs to avoid re-exercising
— going through the real `client` fixture is simpler here, not harder, and additionally
exercises the actual DI-wiring change (`get_login_use_case` gaining `request: Request`),
the highest-risk regression point for this retrofit (mirrors how the sibling
`superadmin_login/test_superadmin_login.py` regression suite, re-run unmodified as this
task's gate, already drives this exact same endpoint end-to-end).

The one exception is the audit-write-failure scenario (test 4): `get_session` (the
login's OWN per-request DB session) and the audit's `session_factory` both resolve from
the SAME `request.app.state.sessionmaker` global (confirmed by direct read of
gateway.core.db.get_session and gateway.auth.api.deps::get_oidc_use_case_with_config) — so
swapping `app.state.sessionmaker` wholesale for a failing one would break the login's own
user lookup too, not just the audit write, proving nothing about fail-open behavior
specifically. Instead this test uses FastAPI's own `app.dependency_overrides` seam to
substitute a `LoginUseCase` built with a REAL, working repository/hasher/tokens (bound to
the normal per-test `db_session`) and ONLY a failing `session_factory` — the audit write's
session-open call is what raises, nothing else. This is still a REAL, verified failure
injection (the factory call itself raises, exactly matching §2's own wording "the supplied
session_factory raises on every session open" and mirroring Part A/C's own
`failing_session_factory` design principle), not a mocked/vacuous stand-in.

Fixtures reused verbatim from ./conftest.py (Part A/C's own): seed_platform_tenant,
fetch_one_audit_row, count_audit_rows, failing_session_factory. The superadmin-with-a-
real-password fixture is intentionally NOT imported from tests/superadmin_login/ (a
different task's own suite, out of scope to touch or import from) — adapted fresh here,
matching this repo's own established convention of suite-local, non-cross-imported
fixtures (see ./conftest.py's own module docstring).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import count_audit_rows, fetch_one_audit_row, seed_platform_tenant

AUDIT_ACTION = "auth.superadmin_login"

_SUPERADMIN_PASSWORD = "part-b-superadmin-horse-battery-01"  # noqa: S105 — test fixture credential


async def _seed_superadmin_with_real_password(
    session: AsyncSession,
    app: Any,
    *,
    tenant_id: uuid.UUID,
    email: str,
) -> uuid.UUID:
    """Insert a real, loginable superadmin User row directly under `tenant_id`.

    Mirrors tests/superadmin_login/test_superadmin_login.py's own `superadmin_user`
    fixture (direct SQL + a REAL Argon2 hash via app.state.password_hasher.hash) —
    adapted fresh here rather than imported, per this repo's suite-local-fixtures
    convention (./conftest.py's own docstring). A real hash is required (not
    ./conftest.py's seed_user, which hardcodes a non-loginable '!sso-no-password'
    sentinel for OIDC-only test users) because this suite exercises genuine
    POST /admin/auth/login round-trips.
    """
    user_id = uuid.uuid4()
    password_hash = app.state.password_hasher.hash(_SUPERADMIN_PASSWORD)
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, :email, :ph, 'superadmin')"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "ph": password_hash},
    )
    await session.commit()
    return user_id


# ---------------------------------------------------------------------------
# Scenario: a superadmin's successful login is audited
# ---------------------------------------------------------------------------


async def test_superadmin_login_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415

    platform_id = await seed_platform_tenant(db_session)
    email = "partb-root@platform.example"
    user_id = await _seed_superadmin_with_real_password(
        db_session, app, tenant_id=platform_id, email=email
    )

    resp = await client.post(
        "/admin/auth/login", json={"email": email, "password": _SUPERADMIN_PASSWORD}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    token = body["access_token"]
    assert isinstance(body.get("expires_in"), int)

    identity = app.state.token_service.decode(token)
    assert identity.role == Role.SUPERADMIN, "JWT shape must be byte-identical to today's"
    assert identity.tenant_id == platform_id

    await asyncio.sleep(0.05)  # let the fire-and-forget audit write complete
    row = await fetch_one_audit_row(db_session, action=AUDIT_ACTION)
    assert row is not None, "Expected exactly one auth.superadmin_login audit row"
    tenant_id, actor_user_id, actor_email, action, target_type, target_id, result, metadata = row
    assert tenant_id == platform_id
    assert actor_user_id == user_id
    assert actor_email == email
    assert action == AUDIT_ACTION
    assert target_type == "user"
    assert target_id == str(user_id)
    assert result == "success"
    assert metadata == {"role": "superadmin", "auth_method": "password"}
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1


# ---------------------------------------------------------------------------
# Scenario: a non-superadmin's successful login is not audited by this primitive
# ---------------------------------------------------------------------------


async def test_non_superadmin_login_not_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415

    # Positive control FIRST: proves this test's harness genuinely detects an audit row
    # when one is expected. Without this, a build that emits ZERO audit rows for ANY
    # login (including a superadmin's) would make the "0 new rows" assertion below
    # trivially true, proving nothing. See module docstring.
    platform_id = await seed_platform_tenant(db_session)
    control_email = "partb-control@platform.example"
    await _seed_superadmin_with_real_password(
        db_session, app, tenant_id=platform_id, email=control_email
    )
    control_resp = await client.post(
        "/admin/auth/login",
        json={"email": control_email, "password": _SUPERADMIN_PASSWORD},
    )
    assert control_resp.status_code == 200, control_resp.text
    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1, (
        "positive control: a superadmin login in this same test/schema must be audited "
        "for the negative assertion below to mean anything"
    )

    # The actual scenario under test: a non-superadmin's successful login.
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "PartBCo",
            "email": "owner@partbco.io",
            "password": "ordinary-horse-battery-02",
        },
    )
    assert signup.status_code == 201, signup.text

    resp = await client.post(
        "/admin/auth/login",
        json={"email": "owner@partbco.io", "password": "ordinary-horse-battery-02"},
    )
    assert resp.status_code == 200, resp.text
    identity = app.state.token_service.decode(resp.json()["access_token"])
    assert identity.role == Role.OWNER, "JWT issued exactly as today, unchanged by this task"

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1, (
        "row count for auth.superadmin_login must be unchanged by the non-superadmin "
        "login — still just the positive-control row from above"
    )


# ---------------------------------------------------------------------------
# Scenario: a failed login attempt against a superadmin email is not audited
# ---------------------------------------------------------------------------


async def test_wrong_password_against_superadmin_email_not_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    platform_id = await seed_platform_tenant(db_session)
    email = "partb-root2@platform.example"
    await _seed_superadmin_with_real_password(db_session, app, tenant_id=platform_id, email=email)

    # Positive control FIRST: prove a genuine successful login by this SAME superadmin
    # email is audited — establishes the harness detects a row when one is expected,
    # before proving the wrong-password attempt below produces no ADDITIONAL row.
    good_resp = await client.post(
        "/admin/auth/login", json={"email": email, "password": _SUPERADMIN_PASSWORD}
    )
    assert good_resp.status_code == 200, good_resp.text
    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1

    # The actual scenario under test: a wrong-password attempt against that SAME email.
    resp = await client.post(
        "/admin/auth/login", json={"email": email, "password": "definitely-wrong-password"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json().get("code") == "ERR_AUTH_INVALID_CREDENTIALS"
    assert "access_token" not in resp.json()

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1, (
        "issuance was never reached for the wrong-password attempt — the row count must "
        "still be exactly 1 (only the earlier successful login), proving the rejection "
        "path added no additional row; the audit branch sits strictly after "
        "tokens.issue() succeeds, so it is structurally unreachable from the raise path "
        "(no role lookup was added ahead of the existing constant-time password check)"
    )


# ---------------------------------------------------------------------------
# Scenario: a login-side audit write failure never blocks a superadmin's login response
# ---------------------------------------------------------------------------


async def test_audit_write_failure_never_blocks_login(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    failing_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from gateway.tenants.api.deps import get_login_use_case  # noqa: PLC0415
    from gateway.tenants.application.use_cases import LoginUseCase  # noqa: PLC0415
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository  # noqa: PLC0415

    platform_id = await seed_platform_tenant(db_session)
    email = "partb-root3@platform.example"
    await _seed_superadmin_with_real_password(db_session, app, tenant_id=platform_id, email=email)

    # See module docstring: app.state.sessionmaker backs BOTH the login's own per-request
    # DB session (gateway.core.db.get_session) AND the audit write's session_factory —
    # swapping it wholesale would break the login lookup too. Override the USE CASE
    # dependency instead: real repository/hasher/tokens (bound to the normal db_session),
    # ONLY session_factory is the real, verified-raising failing_session_factory fixture.
    def _override_with_failing_audit_db() -> LoginUseCase:
        return LoginUseCase(
            SqlAlchemyIdentityRepository(db_session),
            app.state.password_hasher,
            app.state.token_service,
            session_factory=failing_session_factory,
        )

    app.dependency_overrides[get_login_use_case] = _override_with_failing_audit_db
    try:
        with caplog.at_level(logging.WARNING):
            resp = await client.post(
                "/admin/auth/login", json={"email": email, "password": _SUPERADMIN_PASSWORD}
            )
            await asyncio.sleep(0.05)  # let the fire-and-forget (failing) audit write complete
    finally:
        app.dependency_overrides.pop(get_login_use_case, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("access_token"), (
        "login response must be returned exactly as in the healthy-audit-DB case"
    )
    assert isinstance(body.get("expires_in"), int)
    identity = app.state.token_service.decode(body["access_token"])
    assert identity.role == Role.SUPERADMIN

    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "the failing write must not have persisted a row"
    )
    assert "failed to persist audit event" in caplog.text, (
        "record_audit's existing, unchanged fail-open warning must still be logged"
    )
