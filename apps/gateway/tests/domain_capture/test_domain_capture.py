"""Red->green suite for domain-capture (domain-capture TASK.md §3/§4 — FROZEN @ v1).

One test per §2 SCENARIOS row (20 of the 21 — scenario 21, "the existing S1
bootstrap/regression suite is unaffected", is exercised by re-running
tests/signup_routing_authz/test_signup_routing_authz.py UNMODIFIED against the tree AFTER
this build, per that scenario's own prose — not duplicated here). Asserts observable
behavior only: HTTP status, `code` field, DB row effects — never internal implementation
details.

Coverage target: 90% (mirrors signup-and-routing-authz's own security-task bar).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    DEFAULT_PASSWORD,
    DOMAIN_CLAIMS,
    SIGNUP,
    FakeDnsResolver,
    bearer,
    issue_token,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    return body


async def _claim_row_count(db: AsyncSession, *, domain: str | None = None) -> int:
    if domain is None:
        return int(
            (await db.execute(text("SELECT count(*) FROM tenant_domain_claims"))).scalar_one()
        )
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM tenant_domain_claims WHERE domain = :d"),
                {"d": domain},
            )
        ).scalar_one()
    )


async def _claim_status(db: AsyncSession, claim_id: str) -> str:
    row = (
        await db.execute(
            text("SELECT status FROM tenant_domain_claims WHERE id = :id"), {"id": claim_id}
        )
    ).scalar_one()
    return str(row)


async def _user_count(db: AsyncSession, *, tenant_id: uuid.UUID | None = None) -> int:
    if tenant_id is None:
        return int((await db.execute(text("SELECT count(*) FROM users"))).scalar_one())
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM users WHERE tenant_id = :t"), {"t": str(tenant_id)}
            )
        ).scalar_one()
    )


def _record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


# ===========================================================================
# 1. OWNER claims a fresh domain, gets a DNS TXT challenge to publish  (M1, M2)
# ===========================================================================


async def test_owner_claims_fresh_domain_gets_challenge(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-claims.io"
    )

    resp = await client.post(DOMAIN_CLAIMS, json={"domain": "acme.io"}, headers=bearer(token))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["domain"] == "acme.io"
    assert body["status"] == "pending"
    assert body["dns_record_type"] == "TXT"
    assert body["dns_record_name"] == "_ai-proxy-challenge.acme.io"
    assert body["dns_record_value"].startswith("ai-proxy-domain-verification=")
    token_part = body["dns_record_value"].split("=", 1)[1]
    assert len(token_part) >= 40  # secrets.token_urlsafe(32) ~= 43 chars
    assert "expires_at" in body

    assert await _claim_row_count(db_session, domain="acme.io") == 1


# ===========================================================================
# 2. Re-claiming an already-pending domain reissues, does not duplicate  (M2)
# ===========================================================================


async def test_reclaim_pending_domain_reissues_no_duplicate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-reissue.io"
    )

    first = await client.post(DOMAIN_CLAIMS, json={"domain": "reissue.io"}, headers=bearer(token))
    assert first.status_code == 201, first.text
    first_value = first.json()["dns_record_value"]
    first_expiry = first.json()["expires_at"]

    second = await client.post(DOMAIN_CLAIMS, json={"domain": "reissue.io"}, headers=bearer(token))
    assert second.status_code == 201, second.text
    second_value = second.json()["dns_record_value"]
    second_expiry = second.json()["expires_at"]

    assert second_value != first_value, "reissue must generate a FRESH token"
    assert (second_expiry, second_value) != (first_expiry, first_value)
    assert await _claim_row_count(db_session, domain="reissue.io") == 1, (
        "exactly ONE row must exist for (tenant, domain) — no duplicate"
    )


# ===========================================================================
# 3. Malformed domain is rejected before any DB write  (M3, R1)
# ===========================================================================


@pytest.mark.parametrize(
    "bad_domain", ["not a domain!!", "com", "192.168.1.1", "", "-leading-hyphen.com"]
)
async def test_malformed_domain_rejected_before_db_write(
    client: httpx.AsyncClient, db_session: AsyncSession, bad_domain: str
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email=f"owner-{uuid.uuid4().hex[:8]}@malformed.io"
    )
    before = await _claim_row_count(db_session)

    resp = await client.post(DOMAIN_CLAIMS, json={"domain": bad_domain}, headers=bearer(token))

    _assert_problem(resp, 400, "ERR_DOMAIN_INVALID")
    assert await _claim_row_count(db_session) == before


# ===========================================================================
# 4. Claiming a domain another tenant has already verified is rejected up front (M4, R2)
# ===========================================================================


async def test_claim_already_verified_by_other_tenant_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@shared-corp-globex.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "shared-corp.com"}, headers=bearer(globex_token)
    )
    assert create.status_code == 201, create.text
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name("shared-corp.com"), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(globex_token))
    assert verify.status_code == 200, verify.text

    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@shared-corp-acme.io"
    )
    resp = await client.post(
        DOMAIN_CLAIMS, json={"domain": "shared-corp.com"}, headers=bearer(acme_token)
    )

    _assert_problem(resp, 409, "ERR_DOMAIN_ALREADY_VERIFIED")
    assert await _claim_row_count(db_session, domain="shared-corp.com") == 1, (
        "no claim row was created for acme"
    )
    assert await _claim_status(db_session, claim_id) == "verified", (
        "globex's verified claim is completely unchanged"
    )


# ===========================================================================
# 5. OWNER lists only their own tenant's claims  (M5)
# ===========================================================================


async def test_owner_lists_only_own_tenant_claims(client: httpx.AsyncClient) -> None:
    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@list-acme.io"
    )
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@list-globex.io"
    )

    assert (
        await client.post(
            DOMAIN_CLAIMS, json={"domain": "list-acme-1.io"}, headers=bearer(acme_token)
        )
    ).status_code == 201
    assert (
        await client.post(
            DOMAIN_CLAIMS, json={"domain": "list-acme-2.io"}, headers=bearer(acme_token)
        )
    ).status_code == 201
    assert (
        await client.post(
            DOMAIN_CLAIMS, json={"domain": "list-globex-1.io"}, headers=bearer(globex_token)
        )
    ).status_code == 201

    resp = await client.get(DOMAIN_CLAIMS, headers=bearer(acme_token))

    assert resp.status_code == 200, resp.text
    claims = resp.json()["claims"]
    domains = {c["domain"] for c in claims}
    assert domains == {"list-acme-1.io", "list-acme-2.io"}
    assert "list-globex-1.io" not in domains


# ===========================================================================
# 6. Verification succeeds when the DNS TXT record matches exactly  (M6)
# ===========================================================================


async def test_verification_succeeds_on_exact_txt_match(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@verify-ok.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "verify-ok.io"}, headers=bearer(token)
    )
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name("verify-ok.io"), claim_token)

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_id"] == claim_id
    assert body["domain"] == "verify-ok.io"
    assert body["status"] == "verified"
    assert body["verified_at"]
    assert await _claim_status(db_session, claim_id) == "verified"


# ===========================================================================
# 7. Verification fails when the TXT record is missing or wrong  (M6, R5)
# ===========================================================================


@pytest.mark.parametrize("mode", ["missing", "wrong_value"])
async def test_verification_fails_on_missing_or_wrong_txt(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
    mode: str,
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email=f"owner-{mode}@verify-bad.io"
    )
    domain = f"verify-bad-{mode.replace('_', '-')}.io"
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]

    if mode == "wrong_value":
        fake_dns.set_record(_record_name(domain), "totally-different-value")
    # "missing" mode: leave fake_dns unconfigured for this name -> lookup_txt returns []

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))

    _assert_problem(resp, 400, "ERR_DOMAIN_VERIFICATION_FAILED")
    assert await _claim_status(db_session, claim_id) == "pending"


# ===========================================================================
# 8. Verification fails closed on a DNS resolver timeout  (M13, R8)
# ===========================================================================


async def test_verification_fails_closed_on_dns_timeout(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@verify-timeout.io"
    )
    domain = "verify-timeout.io"
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]
    fake_dns.fail_timeout(_record_name(domain))

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))

    _assert_problem(resp, 503, "ERR_DNS_LOOKUP_FAILED")
    assert await _claim_status(db_session, claim_id) == "pending", (
        "NEVER marked verified on a timeout"
    )

    # The caller may retry once DNS is reachable — no internal auto-retry, but a second
    # explicit POST after the fake resolver is fixed succeeds.
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.clear_timeout(_record_name(domain))
    fake_dns.set_record(_record_name(domain), claim_token)
    retry = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))
    assert retry.status_code == 200, retry.text


# ===========================================================================
# 9. Verification is rejected once the challenge has expired  (R6)
# ===========================================================================


async def test_verification_rejected_when_expired(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@verify-expired.io"
    )
    domain = "verify-expired.io"
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)

    # Force expires_at into the past directly (no clock-mocking seam exists yet).
    await db_session.execute(
        text(
            "UPDATE tenant_domain_claims SET expires_at = now() - interval '1 hour' WHERE id = :id"
        ),
        {"id": claim_id},
    )
    await db_session.commit()

    resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))

    _assert_problem(resp, 410, "ERR_DOMAIN_CLAIM_EXPIRED")
    assert await _claim_status(db_session, claim_id) == "pending"


# ===========================================================================
# 10. Two tenants race to verify the same domain — exactly one wins  (M1, M6, R7)
# ===========================================================================


async def test_two_tenants_race_to_verify_same_domain(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    """A genuine race requires BOTH claims to exist as 'pending' BEFORE either verifies —
    M4's create-time pre-check only blocks a create AFTER one is already verified, so two
    concurrent pending claims on the same domain is a legitimate, reachable state; the
    partial unique index (M1) is what actually decides the winner at verify time."""
    domain = "race-corp-2.com"
    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@race2-acme.io"
    )
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@race2-globex.io"
    )

    acme_create = await client.post(
        DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(acme_token)
    )
    assert acme_create.status_code == 201, acme_create.text
    acme_claim_id = acme_create.json()["claim_id"]
    acme_claim_token = acme_create.json()["dns_record_value"].split("=", 1)[1]

    # Seed a SECOND pending claim for globex directly at the DB layer (bypassing the M4
    # pre-check, which only fires once ONE of the two is already verified — exactly the
    # pre-verification window a genuine concurrent race occupies).
    globex_claim_id = str(uuid.uuid4())
    globex_claim_token = "globex-race-token-" + uuid.uuid4().hex
    await db_session.execute(
        text(
            "INSERT INTO tenant_domain_claims "
            "(id, tenant_id, domain, verification_token, status, expires_at, created_by_user_id) "
            "SELECT :id, tenant_id, :domain, :token, 'pending', now() + interval '7 days', id "
            "FROM users WHERE email = :email"
        ),
        {
            "id": globex_claim_id,
            "domain": domain,
            "token": globex_claim_token,
            "email": "owner@race2-globex.io",
        },
    )
    await db_session.commit()

    fake_dns.set_record(_record_name(domain), acme_claim_token)
    acme_verify = await client.post(
        f"{DOMAIN_CLAIMS}/{acme_claim_id}/verify", headers=bearer(acme_token)
    )
    assert acme_verify.status_code == 200, acme_verify.text

    fake_dns.set_record(_record_name(domain), globex_claim_token)
    globex_verify = await client.post(
        f"{DOMAIN_CLAIMS}/{globex_claim_id}/verify", headers=bearer(globex_token)
    )

    _assert_problem(globex_verify, 409, "ERR_DOMAIN_ALREADY_VERIFIED")
    assert await _claim_status(db_session, acme_claim_id) == "verified"
    assert await _claim_status(db_session, globex_claim_id) == "pending", (
        "the loser's claim row remains pending, completely unchanged"
    )
    verified_count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM tenant_domain_claims WHERE domain = :d AND status = 'verified'"
            ),
            {"d": domain},
        )
    ).scalar_one()
    assert verified_count == 1


# ===========================================================================
# 11. A non-OWNER cannot claim, verify, list, or revoke a domain  (R3)
# ===========================================================================


async def test_non_owner_cannot_claim_verify_list_or_revoke(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@non-owner-guard.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "non-owner-guard.io"}, headers=bearer(owner_token)
    )
    claim_id = create.json()["claim_id"]

    admin_token = issue_token(app, role=Role.ADMIN, tenant_id=tenant_id)

    create_resp = await client.post(
        DOMAIN_CLAIMS, json={"domain": "should-fail.io"}, headers=bearer(admin_token)
    )
    list_resp = await client.get(DOMAIN_CLAIMS, headers=bearer(admin_token))
    verify_resp = await client.post(
        f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(admin_token)
    )
    delete_resp = await client.delete(f"{DOMAIN_CLAIMS}/{claim_id}", headers=bearer(admin_token))

    for resp in (create_resp, list_resp, verify_resp, delete_resp):
        _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await _claim_row_count(db_session, domain="should-fail.io") == 0
    assert await _claim_status(db_session, claim_id) == "pending"


# ===========================================================================
# 12. Missing or invalid bearer token is rejected for every domain-claims endpoint (R4)
# ===========================================================================


async def test_missing_bearer_token_rejected_every_endpoint(client: httpx.AsyncClient) -> None:
    claim_id = str(uuid.uuid4())

    create_resp = await client.post(DOMAIN_CLAIMS, json={"domain": "no-token.io"})
    list_resp = await client.get(DOMAIN_CLAIMS)
    verify_resp = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify")
    delete_resp = await client.delete(f"{DOMAIN_CLAIMS}/{claim_id}")

    for resp in (create_resp, list_resp, verify_resp, delete_resp):
        _assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")


# ===========================================================================
# 13. A claim_id from a different tenant is indistinguishable from unknown  (R9)
# ===========================================================================


async def test_cross_tenant_claim_id_indistinguishable_from_unknown(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@crosstenant-globex.io"
    )
    create = await client.post(
        DOMAIN_CLAIMS, json={"domain": "crosstenant.io"}, headers=bearer(globex_token)
    )
    globex_claim_id = create.json()["claim_id"]

    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@crosstenant-acme.io"
    )
    unknown_id = str(uuid.uuid4())

    verify_cross = await client.post(
        f"{DOMAIN_CLAIMS}/{globex_claim_id}/verify", headers=bearer(acme_token)
    )
    verify_unknown = await client.post(
        f"{DOMAIN_CLAIMS}/{unknown_id}/verify", headers=bearer(acme_token)
    )
    delete_cross = await client.delete(
        f"{DOMAIN_CLAIMS}/{globex_claim_id}", headers=bearer(acme_token)
    )
    delete_unknown = await client.delete(
        f"{DOMAIN_CLAIMS}/{unknown_id}", headers=bearer(acme_token)
    )

    for resp in (verify_cross, verify_unknown, delete_cross, delete_unknown):
        _assert_problem(resp, 404, "ERR_DOMAIN_CLAIM_NOT_FOUND")
    assert await _claim_status(db_session, globex_claim_id) == "pending", (
        "globex's claim is completely unchanged"
    )


# ===========================================================================
# 14. Claim-creation and verify are rate-limited per tenant  (M14, R10)
# ===========================================================================


async def test_claim_create_rate_limited_per_tenant(
    client: httpx.AsyncClient,
    low_limit_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="owner@ratelimit.io"
    )
    _low_app, low_client = low_limit_client

    first = await low_client.post(
        DOMAIN_CLAIMS, json={"domain": "ratelimit-1.io"}, headers=bearer(token)
    )
    assert first.status_code == 201, first.text
    before = await _claim_row_count(db_session)

    second = await low_client.post(
        DOMAIN_CLAIMS, json={"domain": "ratelimit-2.io"}, headers=bearer(token)
    )

    _assert_problem(second, 429, "ERR_RATE_LIMITED")
    assert second.headers.get("Retry-After") is not None
    assert await _claim_row_count(db_session) == before, "the rate-limited attempt created no row"


# ===========================================================================
# 15. Revoking a verified claim stops future signups but not existing members (M7)
# ===========================================================================


async def test_revoke_verified_claim_stops_future_signups_not_existing_members(
    client: httpx.AsyncClient,
    second_app_client_signup_disabled: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
) -> None:
    domain = "revoke-me.io"
    _tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@revoke-me.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(owner_token))
    assert verify.status_code == 200, verify.text

    alice_signup = await client.post(
        SIGNUP,
        json={
            "tenant_name": "ignored",
            "email": f"alice@{domain}",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert alice_signup.status_code == 201, alice_signup.text
    assert alice_signup.json()["joined_existing_tenant"] is True

    revoke = await client.delete(f"{DOMAIN_CLAIMS}/{claim_id}", headers=bearer(owner_token))
    assert revoke.status_code == 204, revoke.text

    # alice's row is completely unchanged.
    alice_row = (
        await db_session.execute(
            text("SELECT tenant_id, role FROM users WHERE email = :e"),
            {"e": f"alice@{domain}"},
        )
    ).one()
    assert str(alice_row.tenant_id) == str(_tenant_id)
    assert alice_row.role == "member"

    # A NEW signup for the same domain, on the flag-OFF second app (S1 default), is
    # rejected — the revoked claim no longer influences routing.
    _second_app, off_client = second_app_client_signup_disabled
    bob_signup = await off_client.post(
        SIGNUP,
        json={"tenant_name": "X", "email": f"bob@{domain}", "password": DEFAULT_PASSWORD},
    )
    _assert_problem(bob_signup, 403, "ERR_SIGNUP_INVITE_ONLY")


# ===========================================================================
# 16. A verified-domain signup joins the EXISTING tenant, even while invite-only
#     (M8, M9, M10)
# ===========================================================================


async def test_verified_domain_signup_joins_existing_tenant_while_invite_only(
    client: httpx.AsyncClient,
    second_app_client_signup_disabled: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
) -> None:
    domain = "invite-only-join.io"
    tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@invite-only-join.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(owner_token))
    assert verify.status_code == 200, verify.text

    _second_app, off_client = second_app_client_signup_disabled
    before_users = await _user_count(db_session, tenant_id=tenant_id)
    before_tenants = int(
        (await db_session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    )

    resp = await off_client.post(
        SIGNUP,
        json={
            "tenant_name": "ignored-value",
            "email": f"newhire@{domain}",
            "password": DEFAULT_PASSWORD,
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["joined_existing_tenant"] is True

    after_users = await _user_count(db_session, tenant_id=tenant_id)
    after_tenants = int(
        (await db_session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    )
    assert after_users == before_users + 1
    assert after_tenants == before_tenants, "ZERO new tenants rows"

    new_user = (
        await db_session.execute(
            text("SELECT role, auth_method FROM users WHERE email = :e"),
            {"e": f"newhire@{domain}"},
        )
    ).one()
    assert new_user.role == "member"
    assert new_user.auth_method == "password"


# ===========================================================================
# 17. An unverified (pending) domain changes nothing (M8, M15, R11)
# ===========================================================================


async def test_unverified_pending_domain_changes_nothing(
    client: httpx.AsyncClient,
    second_app_client_signup_disabled: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    domain = "still-pending.io"
    _tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@still-pending.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    assert create.status_code == 201, create.text
    assert create.json()["status"] == "pending"

    _second_app, off_client = second_app_client_signup_disabled
    before_tenants = int(
        (await db_session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    )
    before_users = await _user_count(db_session)

    resp = await off_client.post(
        SIGNUP, json={"tenant_name": "X", "email": f"x@{domain}", "password": DEFAULT_PASSWORD}
    )

    _assert_problem(resp, 403, "ERR_SIGNUP_INVITE_ONLY")
    after_tenants = int(
        (await db_session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    )
    after_users = await _user_count(db_session)
    assert (after_tenants, after_users) == (before_tenants, before_users)
    assert await _claim_row_count(db_session, domain=domain) == 1
    assert await _claim_status(db_session, create.json()["claim_id"]) == "pending", (
        "the pending claim is completely unchanged"
    )


# ===========================================================================
# 18. A domain with no claim at all still obeys S1 exactly, both flag values
#     (M8, regression)
# ===========================================================================


async def test_domain_with_no_claim_obeys_s1_both_flag_values(
    client: httpx.AsyncClient,
    second_app_client_signup_disabled: tuple[Any, httpx.AsyncClient],
) -> None:
    domain = "unclaimed-domaincapture.example"
    _second_app, off_client = second_app_client_signup_disabled

    off_resp = await off_client.post(
        SIGNUP,
        json={"tenant_name": "New Co", "email": f"x@{domain}", "password": DEFAULT_PASSWORD},
    )
    _assert_problem(off_resp, 403, "ERR_SIGNUP_INVITE_ONLY")

    on_resp = await client.post(
        SIGNUP,
        json={"tenant_name": "New Co", "email": f"y@{domain}", "password": DEFAULT_PASSWORD},
    )
    assert on_resp.status_code == 201, on_resp.text
    body = on_resp.json()
    assert body.get("joined_existing_tenant") in (False, None)


# ===========================================================================
# 19. A verified-domain signup still rejects an already-registered email (M9, R12)
# ===========================================================================


async def test_verified_domain_signup_rejects_already_registered_email(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    domain = "taken-email.io"
    _tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email=f"taken@{domain}"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(owner_token))
    assert verify.status_code == 200, verify.text

    before = await _user_count(db_session)
    resp = await client.post(
        SIGNUP,
        json={"tenant_name": "X", "email": f"taken@{domain}", "password": DEFAULT_PASSWORD},
    )

    _assert_problem(resp, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await _user_count(db_session) == before


# ===========================================================================
# 20. A verified-domain signup still rejects a weak password (M9, M10, R13)
# ===========================================================================


async def test_verified_domain_signup_rejects_weak_password(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    domain = "weakpass.io"
    _tenant_id, owner_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@weakpass.io"
    )
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(owner_token))
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(owner_token))
    assert verify.status_code == 200, verify.text

    before = await _user_count(db_session)
    resp = await client.post(
        SIGNUP, json={"tenant_name": "X", "email": f"new@{domain}", "password": "short"}
    )

    _assert_problem(resp, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert await _user_count(db_session) == before
