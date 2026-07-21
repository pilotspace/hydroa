"""sso-login-oracle-closure — OIDC login-init terminal-response collapse (§3 FROZEN @ v1).

THE LEAK THIS SUITE CLOSES
--------------------------
`GET /auth/oidc/login?domain=X` is unauthenticated and public. Before this task it
answered a domain probe with THREE distinguishable terminals:

    verified claim + enabled config      -> 302 to the IdP
    verified claim + NO enabled config   -> 403 ERR_OIDC_DOMAIN_NOT_MAPPED
    no verified claim, nothing resolves  -> 404 ERR_OIDC_NOT_CONFIGURED

The 403/404 split is an enumeration oracle: one unauthenticated GET per domain
separates "X IS a verified customer domain whose tenant has not enabled SSO"
(commercially sensitive — it names customers and prospects) from "X is unknown to
us". `GATEWAY_OIDC_ENABLED` is FALSE in production, so both legs are reachable and
the leak is LIVE, not theoretical.

WHY THE OBVIOUS FIX IS WRONG (and what these tests actually pin)
---------------------------------------------------------------
Swapping the ErrorSpec at the raise site does NOT close it. The 403 branch raises
BEFORE the env-Settings fallback, so under `oidc_enabled=True` a code-swap yields
claimed-unconfigured -> 404 (early raise) vs unclaimed -> 302 (env fallback): the
oracle survives, merely INVERTED. That is why every anti-oracle assertion below is
run under BOTH `oidc_enabled=False` and `oidc_enabled=True` — the invariant is
deployment-INDEPENDENT, and only a control-flow fall-through satisfies it.

Two further traps these tests are shaped to catch:
  - `assert_problem` (the sibling helper) checks status + code ONLY. error_catalog
    holds TWO specs sharing code "ERR_OIDC_NOT_CONFIGURED" with DIFFERENT titles
    (OIDC_NOT_CONFIGURED / OIDC_TENANT_NOT_CONFIGURED). Collapsing onto the wrong
    one re-opens the oracle one layer down, beneath every code-level assertion.
    So the invariant here is asserted on the FULL body, and the title explicitly.
  - The fall-through must NOT reach `resolver.resolve(domain)`. A claimed domain
    routed by another tenant's `email_domains` containment match is the
    cross-tenant hijack domain-routing-unification M1/M8 closed.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from gateway.auth.api import oidc_router as oidc_router_module
from gateway.auth.infrastructure.settings_oidc_config_resolver import ENV_CONFIG_COOKIE_VALUE
from gateway.core.error_catalog import (
    OIDC_DOMAIN_NOT_MAPPED,
    OIDC_NOT_CONFIGURED,
    OIDC_TENANT_NOT_CONFIGURED,
)

from .conftest import (
    ENV_AUTHORIZE_URL,
    OIDC_LOGIN,
    SAML_LOGIN,
    FakeDnsResolver,
    claim_and_verify_domain,
    get_cookies_from_response,
    insert_oidc_config_row,
    signup_and_login,
)

# asyncio_mode = "auto" (pyproject) runs the async tests below without a marker;
# this suite mixes async HTTP probes with sync source-level guards, so no global
# pytest.mark.asyncio is applied.

# The domain tenant A verifies but never configures SSO for — the leaking leg.
CLAIMED_NO_SSO = "claimed-nosso.com"
# A domain no tenant claims and no config resolves — the reference leg.
GHOST_UNKNOWN = "ghost-unknown.com"
# A domain tenant A verifies AND configures — the legitimate flow.
ACME_SSO = "acme-sso.com"

OIDC_COOKIE_NAMES = {"oidc_state", "oidc_nonce", "oidc_tenant_id"}


async def _seed_claimed_but_unconfigured(
    client: httpx.AsyncClient,
    db_session: object,
    fake_dns: FakeDnsResolver,
    *,
    domain: str = CLAIMED_NO_SSO,
    tenant_name: str = "Tenant A",
    email: str | None = None,
) -> object:
    """Tenant A holds a VERIFIED claim for `domain` and has NO enabled OIDC config.

    The owner's email must sit AT the claimed domain — POST /admin/domain-claims
    rejects a claim for a domain the caller has no address at (422). Every sibling
    call site in tests/domain_routing_unification follows the same shape.
    """
    tenant_id, token = await signup_and_login(
        client, tenant_name=tenant_name, email=email or f"owner@{domain}"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token, domain=domain)
    return tenant_id


def _function_ast(fn: Callable[..., Any]) -> ast.AST:
    """Parse a live function's source into an AST node (decorators stripped)."""
    src = textwrap.dedent(inspect.getsource(fn))
    module = ast.parse(src)
    node = module.body[0]
    assert isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef), type(node)
    node.decorator_list = []
    return node


def _login_url(domain: str | None) -> str:
    if domain is None:
        return OIDC_LOGIN
    return f"{OIDC_LOGIN}?domain={urllib.parse.quote(domain)}"


# ---------------------------------------------------------------------------
# M1, M4, R1 — the leaking leg returns the collapsed terminal, never 403
# ---------------------------------------------------------------------------


async def test_claimed_but_unconfigured_returns_collapsed_404(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """A verified claim with no enabled config terminates on the SHARED 404, not 403.

    RED before build: the `if oidc_config is None:` guard inside the claim branch
    raises OIDC_DOMAIN_NOT_MAPPED (403) — a distinct terminal that identifies the
    domain as a verified customer domain.
    """
    await _seed_claimed_but_unconfigured(client, db_session, fake_dns)

    resp = await client.get(_login_url(CLAIMED_NO_SSO))

    assert resp.status_code == 404, (
        f"expected the collapsed 404, got {resp.status_code}: {resp.text}"
    )
    assert resp.status_code != 403
    body = resp.json()
    assert body["code"] == "ERR_OIDC_NOT_CONFIGURED", body
    assert body["code"] != "ERR_OIDC_DOMAIN_NOT_MAPPED", body
    # M4 — the collapse target is OIDC_NOT_CONFIGURED, NOT the same-code/
    # different-title OIDC_TENANT_NOT_CONFIGURED. Asserted on `title` because a
    # title-level oracle passes every code-level assertion silently.
    assert body["title"] == OIDC_NOT_CONFIGURED.title_template, body
    assert body["title"] != OIDC_TENANT_NOT_CONFIGURED.title_template, body
    assert body["title"] != OIDC_DOMAIN_NOT_MAPPED.title_template, body
    # No session-shaped state is handed out on a rejection.
    assert set(get_cookies_from_response(resp)) & OIDC_COOKIE_NAMES == set()


# ---------------------------------------------------------------------------
# M3 — THE ANTI-ORACLE INVARIANT, asserted in BOTH deployment shapes
# ---------------------------------------------------------------------------


async def test_anti_oracle_responses_identical_env_disabled(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """oidc_enabled=False (PRODUCTION shape): the two probe answers are identical.

    Compares status, the FULL body field-for-field (including `title`), and the
    SET of Set-Cookie names — the three observables §3 defines `≡` over.
    """
    await _seed_claimed_but_unconfigured(client, db_session, fake_dns)

    claimed = await client.get(_login_url(CLAIMED_NO_SSO))
    unknown = await client.get(_login_url(GHOST_UNKNOWN))

    assert claimed.status_code == unknown.status_code == 404, (
        f"claimed={claimed.status_code} unknown={unknown.status_code}"
    )
    assert claimed.json() == unknown.json(), (
        "ORACLE: probe bodies differ — a prober can separate a verified customer "
        f"domain from a stranger.\n  claimed={claimed.json()}\n  unknown={unknown.json()}"
    )
    assert claimed.json()["title"] == unknown.json()["title"]
    assert set(get_cookies_from_response(claimed)) == set(get_cookies_from_response(unknown))


@pytest.mark.parametrize("oidc_env_enabled", [True])
async def test_anti_oracle_responses_identical_env_enabled(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """oidc_enabled=True (e2e/test shape): the invariant is deployment-INDEPENDENT.

    This is the test a code-swap-only "fix" fails. Swapping the ErrorSpec at the
    raise site leaves the claimed leg short-circuiting to 404 while the unclaimed
    leg reaches the env fallback and 302s — the oracle inverted, not closed. Only
    a fall-through to the SHARED terminal makes both legs 302 here.
    """
    await _seed_claimed_but_unconfigured(client, db_session, fake_dns)

    claimed = await client.get(_login_url(CLAIMED_NO_SSO))
    unknown = await client.get(_login_url(GHOST_UNKNOWN))

    assert claimed.status_code == unknown.status_code == 302, (
        "ORACLE (inverted): under the env fallback the two legs took different "
        f"terminals — claimed={claimed.status_code} unknown={unknown.status_code}"
    )

    claimed_loc = urllib.parse.urlsplit(claimed.headers["location"])
    unknown_loc = urllib.parse.urlsplit(unknown.headers["location"])
    assert (claimed_loc.scheme, claimed_loc.netloc, claimed_loc.path) == (
        unknown_loc.scheme,
        unknown_loc.netloc,
        unknown_loc.path,
    ), f"claimed -> {claimed.headers['location']}\nunknown -> {unknown.headers['location']}"
    env_authorize = urllib.parse.urlsplit(ENV_AUTHORIZE_URL)
    assert (claimed_loc.scheme, claimed_loc.netloc, claimed_loc.path) == (
        env_authorize.scheme,
        env_authorize.netloc,
        env_authorize.path,
    ), "both legs must land on the ENV authorize endpoint, not a tenant IdP"

    claimed_cookies = get_cookies_from_response(claimed)
    unknown_cookies = get_cookies_from_response(unknown)
    assert set(claimed_cookies) == set(unknown_cookies) == OIDC_COOKIE_NAMES
    # The tenant pin must be the env sentinel on BOTH legs — leaking tenant A's id
    # into the claimed leg's cookie would re-open the oracle in a cookie value.
    assert claimed_cookies["oidc_tenant_id"] == ENV_CONFIG_COOKIE_VALUE
    assert unknown_cookies["oidc_tenant_id"] == ENV_CONFIG_COOKIE_VALUE


# ---------------------------------------------------------------------------
# M2 — the fall-through must NOT reach the domain-keyed resolver
# ---------------------------------------------------------------------------


async def test_claimed_domain_never_routed_by_foreign_email_domains(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """A claimed domain is never routed by ANOTHER tenant's email_domains match.

    The cross-tenant hijack guard (domain-routing-unification M1/M8). If the
    claimed-but-unconfigured fall-through were written as a bare fall-out instead
    of a structural `elif`, tenant A's users would be redirected to tenant B's IdP.
    """
    tenant_a = await _seed_claimed_but_unconfigured(client, db_session, fake_dns)
    tenant_b, _ = await signup_and_login(
        client, tenant_name="Tenant B", email="owner@tenant-b-foreign.com"
    )
    # Tenant B claims CLAIMED_NO_SSO in its email_domains without owning it.
    await insert_oidc_config_row(
        db_session,  # type: ignore[arg-type]
        tenant_id=tenant_b,
        email_domains=[CLAIMED_NO_SSO],
        issuer="https://idp-b.test",
        client_id="tenant-b-client-id",
    )

    resp = await client.get(_login_url(CLAIMED_NO_SSO))

    assert resp.status_code == 404, (
        f"HIJACK: claimed domain routed somewhere ({resp.status_code}) instead of "
        f"the shared terminal: {resp.headers.get('location') or resp.text}"
    )
    assert resp.json()["code"] == "ERR_OIDC_NOT_CONFIGURED"
    cookies = get_cookies_from_response(resp)
    assert "oidc_tenant_id" not in cookies
    assert str(tenant_b) not in resp.text
    assert str(tenant_a) not in resp.text


# ---------------------------------------------------------------------------
# M5 — every legitimate flow is unchanged
# ---------------------------------------------------------------------------


async def test_configured_sso_flow_unchanged(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """Verified claim + ENABLED config still 302s to that tenant's own IdP."""
    tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-sso.com"
    )
    await claim_and_verify_domain(client, fake_dns, owner_token=token, domain=ACME_SSO)
    await insert_oidc_config_row(
        db_session,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        email_domains=[ACME_SSO],
        issuer="https://acme-idp.test",
        client_id="acme-client-id",
    )

    resp = await client.get(_login_url(ACME_SSO))

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith("https://acme-idp.test/authorize")
    cookies = get_cookies_from_response(resp)
    assert set(cookies) == OIDC_COOKIE_NAMES
    assert cookies["oidc_tenant_id"] == str(tenant_id)


@pytest.mark.parametrize("oidc_env_enabled", [True])
async def test_unclaimed_with_env_fallback_still_routes(client: httpx.AsyncClient) -> None:
    """An unclaimed domain still reaches the env fallback when it is enabled."""
    resp = await client.get(_login_url(GHOST_UNKNOWN))

    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith(ENV_AUTHORIZE_URL)
    assert get_cookies_from_response(resp)["oidc_tenant_id"] == ENV_CONFIG_COOKIE_VALUE


async def test_no_domain_shape_unchanged(client: httpx.AsyncClient) -> None:
    """The pre-existing no-domain call keeps its exact shape (R3)."""
    resp = await client.get(_login_url(None))

    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "ERR_OIDC_NOT_CONFIGURED"
    assert body["title"] == OIDC_NOT_CONFIGURED.title_template
    assert get_cookies_from_response(resp) == {}


async def test_no_domain_probe_is_indistinguishable_too(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """All THREE no-resolution legs collapse onto one response, not just two.

    §3's `≡` is defined over the (claimed, unclaimed) pair; the no-domain leg is
    the third terminal and must not become a distinguishable outlier.
    """
    await _seed_claimed_but_unconfigured(client, db_session, fake_dns)

    claimed = await client.get(_login_url(CLAIMED_NO_SSO))
    unknown = await client.get(_login_url(GHOST_UNKNOWN))
    no_domain = await client.get(_login_url(None))

    statuses = {claimed.status_code, unknown.status_code, no_domain.status_code}
    bodies = [claimed.json(), unknown.json(), no_domain.json()]
    assert statuses == {404}, statuses
    assert bodies[0] == bodies[1] == bodies[2], bodies


# ---------------------------------------------------------------------------
# M6 — the SAML sibling stays collapsed; the two paths now agree
# ---------------------------------------------------------------------------


async def test_saml_sibling_stays_collapsed_and_consistent(
    client: httpx.AsyncClient, db_session: object, fake_dns: FakeDnsResolver
) -> None:
    """SAML already had ONE terminal 404; OIDC is brought into line, not vice versa."""
    await _seed_claimed_but_unconfigured(client, db_session, fake_dns)

    claimed = await client.get(f"{SAML_LOGIN}?domain={CLAIMED_NO_SSO}")
    unknown = await client.get(f"{SAML_LOGIN}?domain={GHOST_UNKNOWN}")

    assert claimed.status_code == unknown.status_code == 404
    assert claimed.json()["code"] == unknown.json()["code"] == "ERR_SAML_NOT_CONFIGURED"
    assert claimed.json() == unknown.json()


# ---------------------------------------------------------------------------
# M1, M6, M7, M8 — source-level guards
# ---------------------------------------------------------------------------


def test_login_source_has_no_domain_not_mapped_raise_and_one_terminal() -> None:
    """`oidc_login` raises the 403 nowhere, and has exactly ONE raise in total.

    A SECOND exit is how the oracle comes back (§5 safety rule). The callback's
    own 403 is asserted still present here so the collapse cannot over-reach;
    its behavioral proof stays in tests/sso_oidc (S5, build_callback_url).
    """
    # Parsed with `ast`, not grepped: the property is "the 403 spec is never
    # RAISED or referenced by a statement here", not "the string never appears in
    # the file". The docstring legitimately names the code to record that it is no
    # longer raised on this route — documenting a removed terminal is the opposite
    # of the overclaiming-prose defect this task also fixes. ast walks executable
    # nodes only, so docstrings and comments are structurally excluded.
    login_node = _function_ast(oidc_router_module.oidc_login)

    referenced = {n.id for n in ast.walk(login_node) if isinstance(n, ast.Name)}
    assert "OIDC_DOMAIN_NOT_MAPPED" not in referenced, (
        f"oidc_login must not reference the 403 spec in executable code: {sorted(referenced)}"
    )

    raises = [n for n in ast.walk(login_node) if isinstance(n, ast.Raise)]
    assert len(raises) == 1, (
        f"oidc_login must have exactly ONE terminal raise (a second exit is how the "
        f"oracle comes back); found {len(raises)}: "
        f"{[ast.unparse(r) for r in raises]}"
    )
    assert ast.unparse(raises[0]) == "raise OIDC_NOT_CONFIGURED.exc()", ast.unparse(raises[0])

    # M6/M7 — the post-authentication 403 is preserved, not collapsed away.
    callback_node = _function_ast(oidc_router_module.oidc_callback)
    callback_raises = {ast.unparse(n) for n in ast.walk(callback_node) if isinstance(n, ast.Raise)}
    assert any("OIDC_DOMAIN_NOT_MAPPED" in r for r in callback_raises), (
        "the callback's post-auth 403 must survive this collapse: " + str(callback_raises)
    )
    assert OIDC_DOMAIN_NOT_MAPPED.status == 403
    assert OIDC_DOMAIN_NOT_MAPPED.code == "ERR_OIDC_DOMAIN_NOT_MAPPED"


def test_login_docstring_does_not_overclaim() -> None:
    """M8 — the docstring must describe the SHIPPED behavior, no overclaim.

    The defect this task also fixes was precisely a docstring asserting a security
    property ("no oracle between 'unclaimed' and 'claimed but unconfigured'") that
    the code beneath it never delivered — and that overclaim is plausibly WHY
    nobody re-checked. A prose security claim is an assertion requiring a test.
    """
    doc = inspect.getdoc(oidc_router_module.oidc_login) or ""

    lowered = doc.lower()

    # The specific false claim, gone.
    assert "no oracle" not in lowered, (
        "the false 'no oracle' gloss must be replaced with an accurate statement:\n" + doc
    )
    # A 403 must not be described as a LIVE terminal of this route. Asserted on the
    # claim, not on the bare substring "403" — the docstring should still be able to
    # say the 403 is no longer raised here (that is accurate documentation of a
    # removed terminal, and silently dropping the mention would lose the history).
    assert "fails closed with 403" not in lowered, doc
    assert "no longer raises" in lowered, (
        "the docstring must record that the 403 terminal is gone from this route:\n" + doc
    )
    # The accepted residual is disclosed in code, not silently carried.
    assert "302" in doc and "residual" in lowered, (
        "the docstring must name the accepted 302-vs-4xx residual:\n" + doc
    )
    # And the collapse itself is stated as the property it is.
    assert "terminal" in lowered, doc
