"""RED->GREEN suite for superadmin-audit-foundation Part C (TASK.md §2/§3 — FROZEN @ v1).

Part C retrofits `OidcLoginUseCase` (gateway.auth.application.use_cases) with a new
required, keyword-only `session_factory` constructor param, wired at its one DI
construction site (gateway.auth.api.deps::get_oidc_use_case_with_config), plus a
post-issuance fire-and-forget, fail-open audit-write branch. Part A (ops-side retrofit,
test_part_a_platform_credential_audit.py) and Part B (/admin/auth password-login
integration, out of scope for this task) are covered elsewhere.

RED before BUILD: constructing `OidcLoginUseCase(...)` without `session_factory=...`
raises TypeError: __init__() missing 1 required keyword-only argument: 'session_factory'
(the RIGHT reason — feature genuinely absent, not a broken test).

Test strategy: `OidcLoginUseCase` is constructed DIRECTLY with REAL collaborators
(SqlAlchemyIdentityRepository against the real per-test Postgres schema, real
JwtTokenService) and only the OidcTokenExchanger faked (the one genuine external-HTTP
port boundary — the same thing every OIDC test in this codebase fakes, including
tests/sso_oidc/test_sso_oidc.py). This is a deliberate choice over going through the
FastAPI router/cookie layer: `OidcLoginUseCase` is never constructed directly by any
EXISTING test in this repo (confirmed via mcp__serena — tests/sso_oidc exercises it only
through /auth/oidc/callback), so there is no existing convention to match exactly; direct
construction gives tight, deterministic control over `user.role` (the condition this
task's new branch keys on) without re-implementing state/nonce cookie machinery that is
irrelevant to what changed. The full protocol flow (state/nonce/redirect/cookies) stays
covered, unmodified, by tests/sso_oidc — re-run as this task's regression gate.
"""

from __future__ import annotations

import asyncio
import logging

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import (
    FAKE_NONCE,
    FAKE_STATE,
    TEST_JWT_SECRET,
    FakeOidcExchanger,
    await_audit_count,
    count_audit_rows,
    fetch_one_audit_row,
    make_id_token,
    make_oidc_use_case_settings,
    seed_customer_tenant,
    seed_platform_tenant,
    seed_user,
)

AUDIT_ACTION = "auth.superadmin_login"


def _decode(token: str) -> dict[str, object]:
    result: dict[str, object] = jwt.decode(
        token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_iss": False}
    )
    return result


def _build_use_case(
    *,
    exchanger: FakeOidcExchanger,
    db_session: AsyncSession,
    settings: object,
    session_factory: async_sessionmaker[AsyncSession],
) -> object:
    from gateway.auth.application.use_cases import OidcLoginUseCase
    from gateway.tenants.infrastructure.jwt_service import JwtTokenService
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    return OidcLoginUseCase(
        exchanger=exchanger,
        repository=SqlAlchemyIdentityRepository(db_session),
        tokens=JwtTokenService(settings),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# Scenario: an existing superadmin's successful OIDC login is audited
# ---------------------------------------------------------------------------


async def test_existing_superadmin_oidc_login_is_audited(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    platform_id = await seed_platform_tenant(db_session)
    email = "root@platform.example"
    user_id = await seed_user(db_session, tenant_id=platform_id, email=email, role="superadmin")

    settings = make_oidc_use_case_settings(
        domain_mapping=[{"email_domain": "platform.example", "tenant_id": str(platform_id)}]
    )
    use_case = _build_use_case(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email=email)),
        db_session=db_session,
        settings=settings,
        session_factory=session_factory,
    )

    jwt_token, expires_in = await use_case.execute(  # type: ignore[attr-defined]
        code="good-code", state=FAKE_STATE, cookie_state=FAKE_STATE, cookie_nonce=FAKE_NONCE
    )

    assert jwt_token
    assert expires_in > 0
    claims = _decode(jwt_token)
    assert claims["role"] == "superadmin", "JWT shape must be byte-identical to today's"
    assert claims["tenant_id"] == str(platform_id)

    await await_audit_count(db_session, action=AUDIT_ACTION, expected=1)
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
    assert metadata == {"role": "superadmin", "auth_method": "oidc"}
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 1


# ---------------------------------------------------------------------------
# Scenario: a non-superadmin OIDC login, new or existing, is not audited
# ---------------------------------------------------------------------------


async def test_non_superadmin_oidc_login_not_audited(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    customer_id = await seed_customer_tenant(db_session)
    settings = make_oidc_use_case_settings(
        domain_mapping=[{"email_domain": "customer.example", "tenant_id": str(customer_id)}]
    )

    # (a) a brand-new email completes a successful OIDC exchange -> provisioned
    # role=member exactly as today.
    new_email = "brandnew@customer.example"
    use_case_a = _build_use_case(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email=new_email)),
        db_session=db_session,
        settings=settings,
        session_factory=session_factory,
    )
    jwt_a, _ = await use_case_a.execute(  # type: ignore[attr-defined]
        code="code-a", state=FAKE_STATE, cookie_state=FAKE_STATE, cookie_nonce=FAKE_NONCE
    )
    assert _decode(jwt_a)["role"] == "member"

    # (b) an existing non-superadmin (owner) under its own correctly-mapped tenant ->
    # logged in with its stored role exactly as today.
    owner_email = "owner@customer.example"
    await seed_user(db_session, tenant_id=customer_id, email=owner_email, role="owner")
    use_case_b = _build_use_case(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email=owner_email)),
        db_session=db_session,
        settings=settings,
        session_factory=session_factory,
    )
    jwt_b, _ = await use_case_b.execute(  # type: ignore[attr-defined]
        code="code-b", state=FAKE_STATE, cookie_state=FAKE_STATE, cookie_nonce=FAKE_NONCE
    )
    assert _decode(jwt_b)["role"] == "owner"

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "row count for auth.superadmin_login must be unchanged by either call"
    )


# ---------------------------------------------------------------------------
# Scenario: OIDC rejections never reach the audit hook
# ---------------------------------------------------------------------------


async def test_oidc_rejections_never_reach_audit_hook(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from gateway.auth.domain.errors import OidcTenantConflictError, OidcTokenInvalidError

    platform_id = await seed_platform_tenant(db_session)
    other_tenant_id = await seed_customer_tenant(db_session)
    email = "root2@platform.example"
    # A REAL superadmin — the strongest version of this test: even a user who WOULD be
    # audit-worthy on success must produce zero audit rows when rejected beforehand.
    await seed_user(db_session, tenant_id=platform_id, email=email, role="superadmin")

    # (a) the domain mapping resolves this email's domain to a DIFFERENT tenant than the
    # one the existing user is actually bound to -> OidcTenantConflictError; issuance
    # (and therefore the audit hook, which reads user.role AFTER issuance) never reached.
    conflict_settings = make_oidc_use_case_settings(
        domain_mapping=[{"email_domain": "platform.example", "tenant_id": str(other_tenant_id)}]
    )
    use_case_conflict = _build_use_case(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email=email)),
        db_session=db_session,
        settings=conflict_settings,
        session_factory=session_factory,
    )
    with pytest.raises(OidcTenantConflictError):
        await use_case_conflict.execute(  # type: ignore[attr-defined]
            code="code-conflict",
            state=FAKE_STATE,
            cookie_state=FAKE_STATE,
            cookie_nonce=FAKE_NONCE,
        )

    # (b) an ID-token claims validation failure (wrong issuer) for the SAME, correctly
    # mapped, otherwise-valid superadmin email -> OidcTokenInvalidError; issuance never
    # reached (this task adds no logic upstream of get_or_provision_oidc_user).
    correct_settings = make_oidc_use_case_settings(
        domain_mapping=[{"email_domain": "platform.example", "tenant_id": str(platform_id)}]
    )
    use_case_invalid = _build_use_case(
        exchanger=FakeOidcExchanger(
            id_token=make_id_token(email=email, issuer="https://WRONG-ISSUER.test")
        ),
        db_session=db_session,
        settings=correct_settings,
        session_factory=session_factory,
    )
    with pytest.raises(OidcTokenInvalidError):
        await use_case_invalid.execute(  # type: ignore[attr-defined]
            code="code-invalid",
            state=FAKE_STATE,
            cookie_state=FAKE_STATE,
            cookie_nonce=FAKE_NONCE,
        )

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "get_or_provision_oidc_user / tokens.issue() were never reached for either rejection"
    )


# ---------------------------------------------------------------------------
# Scenario: an OIDC-side audit write failure never blocks a superadmin's OIDC
# login response
# ---------------------------------------------------------------------------


async def test_audit_write_failure_never_blocks_oidc_login(
    db_session: AsyncSession,
    failing_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    platform_id = await seed_platform_tenant(db_session)
    email = "root3@platform.example"
    await seed_user(db_session, tenant_id=platform_id, email=email, role="superadmin")

    settings = make_oidc_use_case_settings(
        domain_mapping=[{"email_domain": "platform.example", "tenant_id": str(platform_id)}]
    )
    use_case = _build_use_case(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email=email)),
        db_session=db_session,
        settings=settings,
        session_factory=failing_session_factory,
    )

    with caplog.at_level(logging.WARNING):
        jwt_token, expires_in = await use_case.execute(  # type: ignore[attr-defined]
            code="code", state=FAKE_STATE, cookie_state=FAKE_STATE, cookie_nonce=FAKE_NONCE
        )
        await asyncio.sleep(0.05)  # let the fire-and-forget (failing) audit write complete

    assert jwt_token, "login response must be returned exactly as in the healthy-audit-DB case"
    assert expires_in > 0
    assert _decode(jwt_token)["role"] == "superadmin"

    assert await count_audit_rows(db_session, action=AUDIT_ACTION) == 0, (
        "the failing write must not have persisted a row"
    )
    assert "failed to persist audit event" in caplog.text, (
        "record_audit's existing, unchanged fail-open warning must still be logged"
    )
