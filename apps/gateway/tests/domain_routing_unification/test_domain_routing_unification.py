"""Failing-first (RED) suite for domain-routing-unification (TASK.md §3 — FROZEN @ v2/CR-v2).

One test per §4 test-plan item (14 items; the combined "matrix" bullet expands to 3
distinct test functions) + a signup regression pin — 15 tests total in this file; the
migration backfill test (M7) lives in tests/migrations/test_domain_routing_backfill.py.

TRUE-RED RULE: every test asserts the target §3 behavior and fails NOW for a REAL
structural reason (wrong error code, wrong status, a wired-in domain-keyed lookup that
should be dead, a resolver that still raises MultipleResultsFound) — never a collection
or fixture error. Three tests are explicitly documented GREEN-BY-DESIGN regression pins
(the "accept a legitimately-verified domain" happy paths, and the password-signup
routing regression pin) — the same disclosed pattern tests/oidc_tenant_config's own
T4/T5 established for this repo. One test originally predicted as green-by-design
(SAML rejecting a domain verified by another tenant) turned out, on the actual isolated
run, to be genuinely RED: the existing SAML guard only checks sibling provider-config
rows, never tenant_domain_claims — see that test's corrected docstring (Ground Risk #4).
Every Must (M1-M8) and Reject (R1-R4) is covered by at least one assertion below.

Divergence technique used throughout (the load-bearing trick that makes the "routes via
the verified claim" tests genuinely red): configs are inserted DIRECTLY via db_session
with `email_domains` that deliberately do NOT match the domain under test — proving
routing is keyed by (verified claim -> tenant_id -> resolve_by_tenant_id), never by an
`email_domains` containment match. If routing were still domain-keyed (today's bug),
these logins would 404/403 with "not configured" instead of succeeding.

Infrastructure: real Postgres (GATEWAY_TEST_DATABASE_URL) + real Redis (SAML pending
store) + httpx.ASGITransport — see conftest.py.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

from .conftest import (
    ADMIN_OIDC,
    DEFAULT_PASSWORD,
    OIDC_CALLBACK,
    OIDC_LOGIN,
    SAML_LOGIN,
    SIGNUP,
    TEST_DATABASE_URL,
    TEST_JWT_SECRET,
    TEST_REDIS_URL,
    FakeDnsResolver,
    assert_problem,
    claim_and_verify_domain,
    get_cookies_from_response,
    insert_oidc_config_row,
    insert_saml_config_row,
    put_oidc_config,
    put_saml_config_body,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. OIDC login routes off the verified claim, not email_domains  (M1, M2)
# ===========================================================================


async def test_oidc_login_routes_via_verified_claim(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    """Tenant A holds a VERIFIED claim for acme-routes-oidc.com; its OIDC config row is
    inserted directly with email_domains that do NOT include that domain at all — the
    ONLY way this login can succeed is via resolve_verified_tenant + resolve_by_tenant_id,
    never a domain-keyed email_domains match.

    RED reason today: DbOidcConfigResolver.resolve() queries email_domains @> ARRAY[domain];
    since the stored config lists an unrelated domain, this returns None -> falls through
    to the (disabled) env path -> 404 ERR_OIDC_NOT_CONFIGURED instead of 302.
    """
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-routes-oidc.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-routes-oidc.com"
    )
    await insert_oidc_config_row(
        db_session,
        tenant_id=tenant_a,
        email_domains=["totally-unrelated-oidc.example"],
    )

    resp = await client.get(
        OIDC_LOGIN, params={"domain": "acme-routes-oidc.com"}, follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith("https://idp.test/authorize")
    cookies = get_cookies_from_response(resp)
    assert cookies.get("oidc_tenant_id") == str(tenant_a), (
        "the OIDC config must be loaded by tenant_id (resolve_by_tenant_id), not by an "
        "email_domains match on the query domain"
    )


# ===========================================================================
# 2. SAML login routes off the verified claim, not email_domains  (M1, M3)
# ===========================================================================


async def test_saml_login_routes_via_verified_claim(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    """Symmetric to test 1 for SAML: tenant A's SAML config row is inserted directly with
    email_domains that do NOT include the verified domain.

    RED reason today: DbSamlConfigResolver.resolve() is ALSO email_domains-keyed; no row
    matches -> SamlNotConfiguredError -> 404 ERR_SAML_NOT_CONFIGURED instead of a 302 to
    the IdP SSO URL.
    """
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-routes-saml.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-routes-saml.com"
    )
    await insert_saml_config_row(
        db_session,
        tenant_id=tenant_a,
        email_domains=["totally-unrelated-saml.example"],
    )

    resp = await client.get(
        SAML_LOGIN, params={"domain": "acme-routes-saml.com"}, follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith("https://fake-idp.test/sso")


# ===========================================================================
# 3. OIDC login for an unclaimed domain fails closed, never 500  (M2, R1)
# ===========================================================================


async def test_oidc_login_unclaimed_domain_fails_closed(client: httpx.AsyncClient) -> None:
    """No tenant holds a verified claim for ghost-oidc.com, no DB config references it, and
    no env GATEWAY_OIDC_DOMAIN_MAPPING entry exists either — still fails closed, never 500.

    SANCTIONED per CR-v2 (domain-routing-unification, 2026-07-18): M2 is narrowed to
    claim-FIRST-then-fallback, not claim-ONLY — when no verified claim exists, the EXISTING
    pre-unification resolution (DbOidcConfigResolver.resolve, then the env-Settings path) is
    retained as a fallback (oidc_router.py). For THIS exact domain, nothing resolves at any
    stage, so the call falls all the way through to the pre-existing terminal branch, which
    reuses the EXISTING 404 ERR_OIDC_NOT_CONFIGURED — the same code this exact call shape
    (domain given, no config, env disabled) already produced before this task existed. This
    is still one of the two contracted fail-closed codes (403 OIDC_DOMAIN_NOT_MAPPED / 404
    OIDC_NOT_CONFIGURED) and still never a 500 — no new code introduced.
    """
    resp = await client.get(OIDC_LOGIN, params={"domain": "ghost-oidc.com"}, follow_redirects=False)

    assert resp.status_code != 500, resp.text
    assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")


# ===========================================================================
# 4. SAML login for an unclaimed domain fails closed, never 500  (M3, R1)
# ===========================================================================


async def test_saml_login_unclaimed_domain_fails_closed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Tenant B has ADMIN-TYPED (never DNS-verified) "ghost-saml.com" into its SAML config
    (inserted directly — the write-time gate this task adds would reject this at PUT time,
    so this simulates legacy/pre-existing data). Ground Risk #4: an admin-typed config must
    NEVER confer routing priority over an actually-verified claim — and here NOBODY has
    verified this domain at all.

    RED reason today: domain-keyed DbSamlConfigResolver.resolve("ghost-saml.com") FINDS
    tenant B's config (email_domains contains it) and succeeds with a 302 — exactly the
    "unverified admin config still routes" bug this task closes. Under CR-v2 the write-gate
    (M5) prevents such a config being created in the first place; a domain with no verified
    claim fails closed with the EXISTING 404 ERR_SAML_NOT_CONFIGURED (no new 403 code — v1
    churn reverted). This test uses insert_saml_config_row to simulate legacy pre-gate data.
    """
    tenant_b, _token_b = await signup_and_login(
        client, tenant_name="Beta", email="owner@ghost-saml-tenant.com"
    )
    await insert_saml_config_row(db_session, tenant_id=tenant_b, email_domains=["ghost-saml.com"])

    resp = await client.get(SAML_LOGIN, params={"domain": "ghost-saml.com"}, follow_redirects=False)

    assert_problem(resp, 404, "ERR_SAML_NOT_CONFIGURED")


# ===========================================================================
# 5. Cross-tenant OIDC domain collision can no longer DoS  (M2, M6, R4)
# ===========================================================================


async def test_oidc_collision_no_longer_dos(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Two OIDC configs (tenants A and B) both list "dup-oidc.example" (legacy data,
    inserted directly — the new PUT-time collision guard could never create this).
    Nobody holds a verified claim on the domain.

    RED reason today: domain-keyed .resolve("dup-oidc.example") matches BOTH enabled rows;
    `.scalar_one_or_none()` raises an unhandled `sqlalchemy.exc.MultipleResultsFound` —
    the live OIDC cross-tenant DoS this task closes (Ground Risk #2). Confirmed via
    isolated run: httpx.ASGITransport's default `raise_app_exceptions=True` re-raises
    this THROUGH the `client.get(...)` call itself rather than handing back a 500
    Response, so the call is wrapped below and normalized into a clear assertion
    failure instead of an opaque pytest ERROR/traceback. Mirrors the failure MODE
    (never crash) of tests/saml_sso/test_verify_domain_collision_dos.py, whose own
    resolver is already fixed and therefore never actually exercises this raise.
    """
    tenant_a, _token_a = await signup_and_login(
        client, tenant_name="CollideOidcA", email="owner@collide-oidc-a.com"
    )
    tenant_b, _token_b = await signup_and_login(
        client, tenant_name="CollideOidcB", email="owner@collide-oidc-b.com"
    )
    await insert_oidc_config_row(
        db_session,
        tenant_id=tenant_a,
        email_domains=["dup-oidc.example"],
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    await insert_oidc_config_row(
        db_session,
        tenant_id=tenant_b,
        email_domains=["dup-oidc.example"],
        created_at=datetime.now(UTC),
    )

    try:
        resp = await client.get(
            OIDC_LOGIN, params={"domain": "dup-oidc.example"}, follow_redirects=False
        )
    except Exception as exc:  # noqa: BLE001 — the live bug IS an unhandled exception today
        pytest.fail(
            "cross-tenant OIDC domain collision must never crash the login route "
            f"(got an unhandled {type(exc).__name__}: {exc})"
        )
    else:
        assert resp.status_code != 500, (
            f"cross-tenant OIDC domain collision must never crash the login route: {resp.text}"
        )


# ===========================================================================
# 6. DbOidcConfigResolver.resolve is deterministic on a raw collision  (M6)
# ===========================================================================


async def test_db_oidc_resolver_deterministic(
    client: httpx.AsyncClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Direct unit-level test of the resolver class named in §3 CONTRACT — two enabled
    rows share a domain; the earliest (created_at, then tenant_id) must win, and the
    call must never raise.

    RED reason today: DbOidcConfigResolver.resolve() has no ORDER BY/LIMIT — awaiting
    `.resolve("dup-resolver.example")` raises `sqlalchemy.exc.MultipleResultsFound`
    directly (a real exception, not merely a failed assertion) — a true "missing
    ORDER BY created_at, tenant_id LIMIT 1" red, mirroring DbSamlConfigResolver's own
    already-fixed shape (Ground: "SAML's parallel path; resolver already hardened").
    """
    from gateway.auth.infrastructure.db_oidc_config_resolver import DbOidcConfigResolver

    tenant_a, _token_a = await signup_and_login(
        client, tenant_name="ResolverA", email="owner@dup-resolver-a.com"
    )
    tenant_b, _token_b = await signup_and_login(
        client, tenant_name="ResolverB", email="owner@dup-resolver-b.com"
    )
    earlier = datetime.now(UTC) - timedelta(days=1)
    later = datetime.now(UTC)
    await insert_oidc_config_row(
        db_session, tenant_id=tenant_a, email_domains=["dup-resolver.example"], created_at=earlier
    )
    await insert_oidc_config_row(
        db_session, tenant_id=tenant_b, email_domains=["dup-resolver.example"], created_at=later
    )

    resolver = DbOidcConfigResolver(session=db_session, settings=settings)
    config = await resolver.resolve("dup-resolver.example")

    assert config is not None
    assert config.tenant_id == tenant_a, (
        "the earliest claimant (created_at, then tenant_id) must win deterministically"
    )


# ===========================================================================
# 7. Callback still routes via GATEWAY_OIDC_DOMAIN_MAPPING as a fallback  (M4, CR-v2)
# ===========================================================================


async def test_callback_env_mapping_still_routes_as_fallback() -> None:
    """Tenant C exists; GATEWAY_OIDC_DOMAIN_MAPPING maps envonly-oidc.com -> tenant C, but
    C has NO per-tenant DB OIDC config and NO verified domain claim — env mapping is the
    ONLY mechanism that could ever route this login. Calls /auth/oidc/callback directly
    (bypassing /login) with no oidc_tenant_id cookie, exactly matching the sso_oidc/
    oidc_jwks backward-compat call shape the router's own comments document.

    SANCTIONED per CR-v2 (domain-routing-unification, 2026-07-18): this test used to
    assert the OPPOSITE (v1 deleted the env-mapping `else:` branch outright and this test
    pinned that deletion). Tin's change-request narrowed M4: env GATEWAY_OIDC_DOMAIN_MAPPING
    is a trusted OPERATOR-set routing source (a platform-admin env var is a different trust
    class than tenant-self-declared email_domains) — v1's wholesale deletion broke ~23
    legacy env-routing tests, including core OIDC SSO happy-paths, so it was reverted.
    Precedence is still verified-claim > per-tenant DB config > operator env mapping: a
    verified claim (none exists here) or a per-tenant config (none exists here) would both
    win over env mapping if either were present — this test's whole point is the FLOOR case
    where NEITHER exists, so the env mapping is consulted and the login succeeds.
    """
    bootstrap_settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
    )
    bootstrap_app = create_app(bootstrap_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        signup_resp = await c.post(
            SIGNUP,
            json={
                "tenant_name": "EnvOnlyCo",
                "email": "owner@envonly-oidc.com",
                "password": DEFAULT_PASSWORD,
            },
        )
        assert signup_resp.status_code == 201, signup_resp.text
        tenant_c = signup_resp.json()["tenant_id"]

    mapping_json = json.dumps([{"email_domain": "envonly-oidc.com", "tenant_id": tenant_c}])
    oidc_settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
        oidc_enabled=True,
        oidc_issuer="https://env-idp.test",
        oidc_client_id="env-client-id",
        oidc_client_secret="env-client-secret",  # noqa: S106
        oidc_redirect_uri="http://gw.test/auth/oidc/callback",
        oidc_domain_mapping=mapping_json,
    )
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker

    now = int(time.time())
    id_token = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": "alice@envonly-oidc.com",
            "iss": "https://env-idp.test",
            "aud": "env-client-id",
            "iat": now,
            "exp": now + 3600,
            "nonce": "test-nonce-envonly-oidc",
        },
        "hs256-signing-key-not-verified",  # noqa: S106
        algorithm="HS256",
    )

    class _FakeExchanger:
        async def exchange(self, code: str, redirect_uri: str) -> dict[str, str]:
            return {"id_token": id_token, "access_token": "fake-access", "token_type": "bearer"}

    oidc_app.state.oidc_exchanger = _FakeExchanger()

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test") as c2:
        resp = await c2.get(
            f"{OIDC_CALLBACK}?code=code1&state=test-state-envonly-oidc",
            cookies={
                "oidc_state": "test-state-envonly-oidc",
                "oidc_nonce": "test-nonce-envonly-oidc",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 302, (
        "CR-v2 (M4 reverted): a callback with no per-tenant pinned config AND no verified "
        f"claim must still fall back to GATEWAY_OIDC_DOMAIN_MAPPING and route: {resp.text}"
    )
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies

    async with bootstrap_app.state.sessionmaker() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM users WHERE email = :e"),
                {"e": "alice@envonly-oidc.com"},
            )
        ).scalar_one()
    assert count == 1, (
        "the env-mapping fallback must provision the user into tenant C when neither a "
        "verified claim nor a per-tenant DB config exists"
    )

    async with bootstrap_app.state.sessionmaker() as session:
        tenant_row = (
            await session.execute(
                text("SELECT tenant_id FROM users WHERE email = :e"),
                {"e": "alice@envonly-oidc.com"},
            )
        ).scalar_one()
    assert str(tenant_row) == tenant_c, (
        "env mapping must route the user into the mapped tenant C, not an unrelated tenant"
    )

    await engine.dispose()


# ===========================================================================
# 8. PUT /admin/oidc accepts this tenant's own verified domain  (M5)
# ===========================================================================


async def test_put_oidc_accepts_own_verified_domain(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    """GREEN-BY-DESIGN regression pin (documented, mirrors tests/oidc_tenant_config's own
    T4/T5 precedent): tenant A owns a verified claim for its own domain, so the write
    succeeds both before and after this task's build — the write-time gate (M5) is a
    positive-case pin here; the negative cases (tests 9, 10, 12) carry the true reds for
    M5's own gate existing at all.
    """
    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-oidc-own.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token_a, domain="acme-oidc-own.com")

    resp = await put_oidc_config(client, owner_token=token_a, email_domains=["acme-oidc-own.com"])

    assert resp.status_code == 200, resp.text
    assert resp.json()["email_domains"] == ["acme-oidc-own.com"]


# ===========================================================================
# 9. PUT /admin/oidc rejects a domain verified by ANOTHER tenant  (M5, R2)
# ===========================================================================


async def test_put_oidc_rejects_other_tenant_domain(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    """Tenant B holds the VERIFIED claim for beta-oidc.com; tenant A's owner tries to PUT
    that same domain into ITS OWN OIDC config.

    RED reason today: oidc_admin_router.py::put_oidc_config has NO collision check at all
    (confirmed by reading the file — unlike put_saml_config, which already has one) — the
    PUT succeeds (200) instead of failing 409. This IS the live OIDC cross-tenant DoS
    write-time root cause (Ground Risk #2).
    """
    _tenant_b, token_b = await signup_and_login(
        client, tenant_name="Beta", email="owner@beta-oidc.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token_b, domain="beta-oidc.com")

    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-vs-beta-oidc.com"
    )

    resp = await put_oidc_config(client, owner_token=token_a, email_domains=["beta-oidc.com"])

    assert_problem(resp, 409, "ERR_OIDC_DOMAIN_ALREADY_CLAIMED")

    # tenant A's config is unchanged — no row at all was ever created for it.
    get_resp = await client.get(ADMIN_OIDC, headers={"Authorization": f"Bearer {token_a}"})
    assert get_resp.status_code == 404, get_resp.text


# ===========================================================================
# 10. PUT /admin/saml rejects a domain NOBODY has verified  (M5, R3)
# ===========================================================================


async def test_put_saml_rejects_unverified_domain(client: httpx.AsyncClient) -> None:
    """No tenant holds a verified claim for unproven-saml.com.

    RED reason today: saml_admin_router.py::put_saml_config only rejects a domain
    claimed by a DIFFERENT tenant — it never checks whether the CALLING tenant has
    verified the domain at all. The PUT succeeds (200) instead of failing 422.
    """
    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@unproven-saml.com"
    )

    resp = await put_saml_config_body(
        client, owner_token=token_a, email_domains=["unproven-saml.com"]
    )

    assert_problem(resp, 422, "ERR_DOMAIN_NOT_VERIFIED")

    get_resp = await client.get("/admin/saml", headers={"Authorization": f"Bearer {token_a}"})
    assert get_resp.status_code == 404, get_resp.text


# ===========================================================================
# 11. PUT /admin/saml accepts this tenant's own verified domain  (M5)
# ===========================================================================


async def test_put_saml_accepts_own_verified_domain(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    """GREEN-BY-DESIGN regression pin (same rationale as test 8's docstring)."""
    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-saml-own.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token_a, domain="acme-saml-own.com")

    resp = await put_saml_config_body(
        client, owner_token=token_a, email_domains=["acme-saml-own.com"]
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["email_domains"] == ["acme-saml-own.com"]


# ===========================================================================
# 12. PUT /admin/oidc rejects a domain NOBODY has verified  (M5, R3)
# ===========================================================================


async def test_put_oidc_rejects_unverified(client: httpx.AsyncClient) -> None:
    """OIDC's own unverified-domain gate (symmetric to test 10's SAML case).

    RED reason today: put_oidc_config has NO domain_capture check whatsoever (no
    collision check, no verified-claim check) — the PUT succeeds (200) instead of
    failing 422.
    """
    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@unproven-oidc.com"
    )

    resp = await put_oidc_config(client, owner_token=token_a, email_domains=["unproven-oidc.com"])

    assert_problem(resp, 422, "ERR_DOMAIN_NOT_VERIFIED")

    get_resp = await client.get(ADMIN_OIDC, headers={"Authorization": f"Bearer {token_a}"})
    assert get_resp.status_code == 404, get_resp.text


# ===========================================================================
# 13. PUT /admin/saml rejects a domain verified by ANOTHER tenant  (M5, R2)
# ===========================================================================


async def test_put_saml_rejects_other_tenant(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    """NOT green-by-design (corrected after the actual RED run below) — the existing
    SAML guard (tests/saml_sso/test_verify_domain_collision_dos.py) only compares
    against OTHER TENANTS' EXISTING saml_provider_configs.email_domains rows; it never
    consults tenant_domain_claims. Tenant B here never registers a SAML config at all —
    it only holds a DNS-TXT verified domain claim via domain_capture — so the existing
    guard sees zero sibling SAML rows and lets tenant A's PUT through. This is exactly
    Ground Risk #4 (no code compares tenant_domain_claims against provider-config
    tables): a tenant can "steal" a domain another tenant has verified, so long as the
    verifying tenant hasn't also configured SAML/OIDC yet.

    RED reason today: 200 (config written) instead of the contracted 409
    ERR_SAML_DOMAIN_ALREADY_CLAIMED — confirmed via isolated run.
    """
    _tenant_b, token_b = await signup_and_login(
        client, tenant_name="Beta", email="owner@beta-saml.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token_b, domain="beta-saml.com")

    _tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-vs-beta-saml.com"
    )

    resp = await put_saml_config_body(client, owner_token=token_a, email_domains=["beta-saml.com"])

    assert_problem(resp, 409, "ERR_SAML_DOMAIN_ALREADY_CLAIMED")


# ===========================================================================
# 14. Domain matching is exact + case-normalized on the login path  (M8)
# ===========================================================================


async def test_domain_match_case_normalized_and_exact(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    """Tenant A holds a verified claim for acme-case.com. A mixed-case query param must
    still resolve A; an exact-match-only subdomain must NOT.

    RED reason today (mixed-case assertion): the raw, un-normalized query param is used
    directly in the email_domains containment query, which is case-sensitive — mixed
    case never matches the lowercase-stored value, so it falls through to 404
    ERR_OIDC_NOT_CONFIGURED instead of the contracted 302.

    SANCTIONED per CR-v2 (domain-routing-unification, 2026-07-18): the subdomain
    assertion's expected code was narrowed from 403 ERR_OIDC_DOMAIN_NOT_MAPPED to 404
    ERR_OIDC_NOT_CONFIGURED. M2 is now claim-FIRST-then-fallback (not claim-ONLY): a
    subdomain never resolves a verified claim (M8, unchanged), so it falls to the SAME
    EXISTING fallback resolution as an unclaimed domain (oidc_router.py) — the deterministic
    DbOidcConfigResolver.resolve() also finds no match (email_domains stores the base
    domain only), and env OIDC is disabled in this fixture, so the pre-existing terminal
    404 fires. Assertion INTENT preserved: the subdomain still never resolves/routes (no
    302, no tenant leak) and still never 500s — only the specific 4xx code changed, per
    the same "match the real /login behavior, do not invent a new code" rule applied to
    test_oidc_login_unclaimed_domain_fails_closed above.
    """
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-case.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token_a, domain="acme-case.com")
    await insert_oidc_config_row(db_session, tenant_id=tenant_a, email_domains=["acme-case.com"])

    mixed_case = await client.get(
        OIDC_LOGIN, params={"domain": "ACME-CASE.com"}, follow_redirects=False
    )
    assert mixed_case.status_code == 302, mixed_case.text
    cookies = get_cookies_from_response(mixed_case)
    assert cookies.get("oidc_tenant_id") == str(tenant_a)

    subdomain = await client.get(
        OIDC_LOGIN, params={"domain": "mail.acme-case.com"}, follow_redirects=False
    )
    assert_problem(subdomain, 404, "ERR_OIDC_NOT_CONFIGURED")


# ===========================================================================
# 15. Regression: password signup still routes via the SAME resolver  (M1)
# ===========================================================================


async def test_signup_still_routes_via_same_resolver(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    """GREEN-BY-DESIGN regression pin: password signup already routes exclusively through
    resolve_verified_tenant (Ground: "Signup's sole routing surface = tenant_domain_
    claims") and this task does not touch tenants/api/router.py::signup. Pinned here so
    a future change cannot silently regress the ONE invariant M1 depends on: signup and
    SSO must agree on a single resolver.
    """
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-signup-regress.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-signup-regress.com"
    )

    resp = await client.post(
        SIGNUP,
        json={
            "tenant_name": "ignored",
            "email": "newhire@acme-signup-regress.com",
            "password": DEFAULT_PASSWORD,
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tenant_a)
    assert body["joined_existing_tenant"] is True
