"""VERIFY-phase repro (add-verify, 2026-07-10): cross-tenant email_domains
collision crashes GET /auth/saml/login with an unhandled
sqlalchemy.exc.MultipleResultsFound instead of a contracted response.

Root cause: DbSamlConfigResolver.resolve() (db_saml_config_resolver.py:59-78)
uses `.scalar_one_or_none()` against
`SamlProviderConfigRow.email_domains.contains([domain])` with NO
cross-tenant uniqueness constraint on email_domains and no defensive
LIMIT 1 / ORDER BY + first(). PUT /admin/saml (saml_admin_router.py) does
not validate that a submitted email_domains entry is not already claimed
by a DIFFERENT tenant. Any tenant owner can therefore make ANY OTHER
tenant's (and their own) `/auth/saml/login?domain=<X>` 500 by configuring
`email_domains` to include a domain a different tenant has already
claimed for SAML — no privilege beyond "owner of some tenant" is needed.

This is a FAITHFUL MIRROR of the identical pattern already shipped in
db_oidc_config_resolver.py (`.scalar_one_or_none()` on the same
`.contains([domain])` predicate, no cross-tenant domain uniqueness) — not
a defect newly introduced by this task, but newly reachable via a second,
independently-configurable per-tenant domain list. Reported per TASK.md
§6 verify (add-verify persona: tdd-verifier / security-gatekeeper),
NOT fixed here (verify never edits production code).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.exc import MultipleResultsFound

from tests.saml_sso.saml_fixtures import generate_idp_keypair
from tests.saml_sso.test_saml_sso import put_saml_config, signup_tenant


async def test_repro_domain_collision_across_tenants_crashes_login(
    client: httpx.AsyncClient,
) -> None:
    owner_a, _tid_a = await signup_tenant(
        client, tenant_name="CollideA", email="collidea@collide-test.com"
    )
    kp_a = generate_idp_keypair()
    resp_a = await put_saml_config(
        client,
        owner_token=owner_a,
        idp_x509_cert=kp_a.cert_pem,
        email_domains=["shared-domain.example"],
    )
    assert resp_a.status_code == 200, resp_a.text

    owner_b, _tid_b = await signup_tenant(
        client, tenant_name="CollideB", email="collideb@collide-test2.com"
    )
    kp_b = generate_idp_keypair()
    resp_b = await put_saml_config(
        client,
        owner_token=owner_b,
        idp_x509_cert=kp_b.cert_pem,
        email_domains=["shared-domain.example"],
    )
    assert resp_b.status_code == 200, resp_b.text

    # EXPECTED (contract-consistent) behavior would be SOME deterministic,
    # non-crashing outcome (e.g. 404 ERR_SAML_NOT_CONFIGURED, or a
    # deterministic pick of one config) — never an unhandled exception.
    # This currently RAISES sqlalchemy.exc.MultipleResultsFound instead of
    # returning any HTTP response at all (confirmed via direct ASGI call;
    # a real uvicorn deployment would turn this into a bare 500 for every
    # subsequent login attempt against the colliding domain, by EITHER
    # tenant, until an admin notices and removes the collision).
    with pytest.raises(MultipleResultsFound):
        await client.get(
            "/auth/saml/login",
            params={"domain": "shared-domain.example"},
            follow_redirects=False,
        )
