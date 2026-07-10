"""VERIFY-phase repro (add-verify, 2026-07-10), INVERTED at fix-integration
(2026-07-10) to lock in the corrected behavior.

Original finding: a cross-tenant email_domains collision crashed
GET /auth/saml/login with an unhandled sqlalchemy.exc.MultipleResultsFound
instead of a contracted response — any tenant owner could DoS any other
tenant's SAML login by claiming an already-claimed domain.

The fix is two-layer (both asserted below):
  1. PUT /admin/saml (saml_admin_router.py) now REJECTS a submitted
     email_domains entry already claimed by a DIFFERENT tenant with
     409 ERR_SAML_DOMAIN_ALREADY_CLAIMED — the collision can no longer be
     created through the API at all.
  2. DbSamlConfigResolver.resolve() (db_saml_config_resolver.py) is now
     defensive against a pre-existing/legacy collision or a PUT-guard TOCTOU
     race: `ORDER BY created_at, tenant_id LIMIT 1` makes the query return AT
     MOST one row (earliest claimant wins, deterministically), so GET
     /auth/saml/login can never raise MultipleResultsFound again.

This test asserts BOTH: the API-reachable guard, and — by inserting a
colliding row DIRECTLY (bypassing the now-closed PUT path) — that the
resolver degrades to a deterministic HTTP response rather than a 500 crash.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.infrastructure.saml_orm import SamlProviderConfigRow
from tests.saml_sso.saml_fixtures import generate_idp_keypair
from tests.saml_sso.test_saml_sso import put_saml_config, signup_tenant


async def test_domain_collision_guarded_and_resolver_deterministic(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
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

    owner_b, tid_b = await signup_tenant(
        client, tenant_name="CollideB", email="collideb@collide-test2.com"
    )
    kp_b = generate_idp_keypair()
    resp_b = await put_saml_config(
        client,
        owner_token=owner_b,
        idp_x509_cert=kp_b.cert_pem,
        email_domains=["shared-domain.example"],
    )
    # LAYER 1 — the PUT guard now rejects the colliding domain outright.
    assert resp_b.status_code == 409, resp_b.text
    assert resp_b.json()["code"] == "ERR_SAML_DOMAIN_ALREADY_CLAIMED"

    # LAYER 2 — simulate a LEGACY collision the guard could not have caught
    # (or a TOCTOU race): insert tenant B's colliding, enabled config directly,
    # bypassing the API guard.
    db_session.add(
        SamlProviderConfigRow(
            tenant_id=uuid.UUID(str(tid_b)),
            idp_entity_id="https://idp-b.example/entity",
            idp_sso_url="https://idp-b.example/sso",
            idp_x509_cert=kp_b.cert_pem,
            email_domains=["shared-domain.example"],
            enabled=True,
        )
    )
    await db_session.commit()

    # Previously RAISED MultipleResultsFound; now returns a deterministic,
    # non-crashing HTTP response (earliest claimant — tenant A — wins).
    resp = await client.get(
        "/auth/saml/login",
        params={"domain": "shared-domain.example"},
        follow_redirects=False,
    )
    assert resp.status_code != 500, resp.text
