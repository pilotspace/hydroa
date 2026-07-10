"""VERIFY-phase repro (add-verify, saml-sso, security HARD-STOP review,
2026-07-10).

FINDING: M5.4's "TRUST-BEARING check" ("the SIGNED SubjectConfirmationData/
@InResponseTo equals the request_id retrieved from the pending-request
store") is NOT independently enforced by saml_use_cases.py / python3-saml
when the signed assertion's SubjectConfirmationData simply OMITS the
InResponseTo attribute (legal per SAML 2.0 core §2.4.1.2 — it is OPTIONAL).

Root cause (two compounding gaps):
  1. `SamlAcsUseCase.execute()` passes `request_id` into
     `response.is_valid(request_data, request_id=request_id, ...)` where
     `request_id` is the SAME value `_peek_in_response_to()` already read
     from the raw, UNSIGNED, attacker-controlled document
     (saml_use_cases.py:382, :438). python3-saml's own early check
     (response.py:122-129, `in_response_to = self.get_in_response_to()` vs
     the `request_id` param) therefore compares the unverified document
     against itself — tautological, adds no security value.
  2. python3-saml's SubjectConfirmationData loop (response.py:235-268) only
     rejects on InResponseTo mismatch when the SCD actually CARRIES an
     InResponseTo (`if in_response_to and irt and irt != in_response_to:
     continue`) — when `irt` is falsy/absent, that SubjectConfirmation is
     accepted regardless of correlation to the pending request.

Net effect: the ACTUAL trust anchor for tenant binding reduces to "the
attacker-chosen outer InResponseTo happens to name a currently-pending,
not-yet-consumed request_id" — a value ANY unauthenticated caller can mint
for themselves via GET /auth/saml/login (no auth required). An attacker who
obtains ANY validly-signed assertion for a victim user of the SAME tenant
whose SubjectConfirmationData omits InResponseTo (e.g. a genuine
IdP-initiated assertion — legal per spec, and exactly the shape M6 is
supposed to reject) can re-wrap it (splicing, not re-signing — the
signature covers only the <saml:Assertion> sub-tree, so a new outer
<samlp:Response> wrapper does not invalidate it — the identical technique
saml_fixtures.py itself uses to build test fixtures) inside a fresh Response
envelope whose InResponseTo points at a pending request the ATTACKER
initiated, and mint a session AS THE VICTIM. This is the account-takeover
class of bug this task's own risk header names.

This test builds a validly-signed assertion (real xmlsec signature over the
tenant's real registered IdP keypair) whose SubjectConfirmationData omits
InResponseTo, wrapped in an (unsigned) outer Response envelope whose
InResponseTo is set to a genuinely-pending AuthnRequest ID. Per the frozen
contract (§1 M5.4 / §2 Reject list: signed InResponseTo mismatch ->
ERR_SAML_RESPONSE_MISMATCH), this MUST be rejected. It currently is NOT —
confirmed reproducible 3/3 runs (not a test-order flake).

Reported per TASK.md §6 verify (add-verify persona: tdd-verifier /
security-gatekeeper). NOT fixed here — verify never edits production code.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.saml_sso.saml_fixtures import AssertionSpec, build_saml_response_b64
from tests.saml_sso.test_saml_sso import (
    IDP_ENTITY_ID,
    SAML_ACS,
    count_users_by_email,
    seed_saml_config,
    signup_tenant,
    start_login_and_get_request_id,
)


async def test_scd_missing_inresponseto_must_be_rejected_not_accepted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner-verify-scd@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    # A pending AuthnRequest the login-init flow actually created — this is
    # the request_id the (unsigned) M3 store lookup keys off.
    request_id_pending = await start_login_and_get_request_id(client)

    # A validly-signed assertion (real xmlsec signature, the tenant's real
    # registered IdP keypair) that is NOT cryptographically bound to
    # request_id_pending at all:
    #   - outer Response InResponseTo = request_id_pending (forgeable —
    #     unsigned, only ever used as the M3 lookup key by design)
    #   - signed SubjectConfirmationData InResponseTo = OMITTED entirely
    #     (a legal SAML variant per spec, e.g. IdP-initiated SSO)
    spec = AssertionSpec(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id_pending,
        subject_nameid="victim-verify-scd@acme.com",
        sign=True,
        sign_with=keypair,
        scd_in_response_to_override="",  # omit SCD InResponseTo entirely
    )
    forged_b64 = build_saml_response_b64(spec)

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": forged_b64}, follow_redirects=False
    )

    # EXPECTED (per frozen contract M5.4 / Reject list): rejected, no
    # session, no user provisioned — the signed content carries no
    # verifiable binding to request_id_pending.
    # ACTUAL (confirmed 2026-07-10, 3/3 runs): 302 + a session IS minted.
    assert resp.status_code in (400, 401), (
        "SECURITY FINDING: an assertion whose signed SubjectConfirmationData "
        "omits InResponseTo was ACCEPTED (mints a session) instead of "
        f"rejected. status={resp.status_code} body={resp.text}"
    )
    user_count = await count_users_by_email(db_session, "victim-verify-scd@acme.com")
    assert user_count == 0, (
        f"SECURITY FINDING: a user WAS provisioned ({user_count} row(s)) for "
        "an assertion that should have been rejected at the trust-bearing "
        "InResponseTo check."
    )
