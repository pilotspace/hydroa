"""Red->green suite for member-verified-recognition (member-verified-recognition TASK.md
§3/§4 — FROZEN @ v1, SECURITY task). One test per §2 SCENARIOS row (§4 test_plan, 18 tests
total). Asserts observable behavior only: HTTP status, `code` field, DB row effects,
captured email messages, `resolve_verified_tenant`'s own return value — never internal
implementation details.

MUST run RED (missing implementation) before Build: `member_verify_code_hash` /
`member_verified_at` / the 2 new routes / the 5 new ErrorSpecs do not exist yet, so every
test that reaches them fails on a missing DB column (ProgrammingError: column does not
exist — the migration hasn't landed), a 404 from the router not knowing the path, or an
assertion on a response field that isn't there yet — the established true-red convention
this codebase already uses (see test_domain_verify_notify.py's own docstring).

The keyed-hash helper `_code_hash` reimplements ONLY the frozen Option A formula
(HMAC-SHA256(code, key=HMAC(jwt_secret, "member-verify-code"))) as an independent oracle
for `test_code_stored_only_as_keyed_hash` — it never substitutes for the production hash
path (which is exercised for real, through a live signup, in that same test).

Coverage target: 90% (member_verify_use_cases + the new repository methods + router routes,
per TASK.md §4).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.domain_capture.domain.errors import DomainClaimRateLimitedError
from gateway.tenants.domain.entities import Role
from tests.conftest import TEST_JWT_SECRET

from .conftest import (
    DEFAULT_PASSWORD,
    DOMAIN_CLAIMS,
    LOGIN,
    SIGNUP,
    FakeDnsResolver,
    FakeEmailSender,
    bearer,
    issue_token,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio

_HMAC_LABEL = b"member-verify-code"


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    return body


def _code_hash(code: str, jwt_secret: str) -> str:
    """Independent oracle for the FROZEN Option A hash-at-rest formula (TASK.md §3/§5):
    HMAC-SHA256(code, key=HMAC(jwt_secret, "member-verify-code")), hex-encoded."""
    inner_key = hmac.new(jwt_secret.encode(), _HMAC_LABEL, hashlib.sha256).digest()
    return hmac.new(inner_key, code.encode(), hashlib.sha256).hexdigest()


def _verify_url(claim_id: str) -> str:
    return f"{DOMAIN_CLAIMS}/{claim_id}/member-verify"


def _resend_url(claim_id: str) -> str:
    return f"{DOMAIN_CLAIMS}/{claim_id}/member-verify/resend"


async def _create_claim(client: httpx.AsyncClient, token: str, domain: str) -> str:
    resp = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["claim_id"])


async def _seed_code(
    db_session: AsyncSession,
    claim_id: str,
    *,
    code: str,
    expires_at: datetime,
    attempt_count: int = 0,
    jwt_secret: str = TEST_JWT_SECRET,
) -> None:
    """Directly seed the 3 in-flight code columns (bypasses the not-yet-existing issuance
    use case) — pre-migration this raises ProgrammingError (column does not exist), which
    IS the correct red reason for every test that arranges via this helper."""
    code_hash = _code_hash(code, jwt_secret)
    await db_session.execute(
        text(
            "UPDATE tenant_domain_claims SET member_verify_code_hash = :h, "
            "member_verify_code_expires_at = :e, member_verify_attempt_count = :c "
            "WHERE id = :id"
        ),
        {"h": code_hash, "e": expires_at, "c": attempt_count, "id": claim_id},
    )
    await db_session.commit()


async def _claim_row(db_session: AsyncSession, claim_id: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT status, verified_at, member_verified_at, member_verify_code_hash, "
                "member_verify_code_expires_at, member_verify_attempt_count "
                "FROM tenant_domain_claims WHERE id = :id"
            ),
            {"id": claim_id},
        )
    ).one()


class _RaisingEmailSender:
    """A sender whose `.send()` always raises — proves M2/R-fail-open end-to-end: signup
    must still return 201 no matter what breaks inside issuance."""

    async def send(self, message: Any) -> None:
        raise RuntimeError("simulated email outage")


class _AlwaysRateLimitedLimiter:
    """A DomainClaimRateLimiter double whose `.check()` always trips the limit — the
    inverse of the fail-open Redis-outage double the sibling suites use."""

    async def check(self, *, action: str, tenant_id: uuid.UUID, limit: int) -> None:
        raise DomainClaimRateLimitedError(retry_after=7)


# ===========================================================================
# 1. Business signup with a company domain issues a code to the signup address   (M1)
# ===========================================================================


async def test_business_signup_issues_code_to_signup_address(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_email_sender: FakeEmailSender,
) -> None:
    app.state.email_sender = fake_email_sender
    domain = "example-co.com"
    owner_email = f"owner@{domain}"

    resp = await client.post(
        SIGNUP,
        json={"tenant_name": "Example Co", "email": owner_email, "password": DEFAULT_PASSWORD},
    )

    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["tenant_id"]

    row = (
        await db_session.execute(
            text(
                "SELECT status, member_verify_code_hash, member_verify_code_expires_at, "
                "member_verify_attempt_count FROM tenant_domain_claims "
                "WHERE tenant_id = :tid AND domain = :domain"
            ),
            {"tid": tenant_id, "domain": domain},
        )
    ).one()
    assert row.status == "pending"
    assert row.member_verify_code_hash is not None
    assert row.member_verify_attempt_count == 0
    assert row.member_verify_code_expires_at is not None
    ttl = row.member_verify_code_expires_at - datetime.now(UTC)
    assert timedelta(minutes=10) < ttl <= timedelta(minutes=16), f"expected a ~15-min TTL, got {ttl}"

    assert len(fake_email_sender.sent) == 1, "exactly one member-verify code email"
    assert fake_email_sender.sent[0].to == owner_email, (
        "the recipient must be derived from the signup email, never a request field"
    )


# ===========================================================================
# 2. Signup still returns 201 when code issuance fails   (M2, R-fail-open)
# ===========================================================================


async def test_signup_returns_201_when_issuance_fails(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    app.state.email_sender = _RaisingEmailSender()
    domain = "example-issuance-fail.io"

    resp = await client.post(
        SIGNUP,
        json={
            "tenant_name": "Acme",
            "email": f"owner@{domain}",
            "password": DEFAULT_PASSWORD,
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "tenant_id" in body and "user_id" in body
    assert body.get("joined_existing_tenant") in (None, False)
    tenant_id = body["tenant_id"]

    # The email failure must NOT roll back the DB half of issuance (the claim + hashed
    # code are written before send_email is ever invoked) — proving the failure was
    # swallowed at the fail-open BOUNDARY, not by simply never attempting issuance at all.
    row = (
        await db_session.execute(
            text(
                "SELECT member_verify_code_hash FROM tenant_domain_claims "
                "WHERE tenant_id = :tid AND domain = :domain"
            ),
            {"tid": tenant_id, "domain": domain},
        )
    ).one()
    assert row.member_verify_code_hash is not None, (
        "issuance's DB write must complete even though the email send failed"
    )


# ===========================================================================
# 3. Owner enters the correct code and becomes member-verified   (M3,M6,M8)
# ===========================================================================


async def test_correct_code_sets_member_verified(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "member-verify-correct.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    code = "482913"
    await _seed_code(db_session, claim_id, code=code, expires_at=datetime.now(UTC) + timedelta(minutes=15))

    resp = await client.post(_verify_url(claim_id), json={"code": code}, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_id"] == claim_id
    assert body["member_verified_at"] is not None
    assert body["status"] == "pending"
    assert body["verified_at"] is None

    row = await _claim_row(db_session, claim_id)
    assert row.status == "pending"
    assert row.verified_at is None
    assert row.member_verified_at is not None
    assert row.member_verify_code_hash is None, "the code must be cleared (single-use)"
    assert row.member_verify_code_expires_at is None
    assert row.member_verify_attempt_count == 0

    replay = await client.post(_verify_url(claim_id), json={"code": code}, headers=bearer(token))
    assert replay.status_code in (400, 410), (
        f"a cleared/replayed code must never re-verify: got {replay.status_code}"
    )


# ===========================================================================
# 4. A wrong code is rejected and increments the attempt counter   (R3,M4)
# ===========================================================================


async def test_wrong_code_increments_attempt(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "member-verify-wrong.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    await _seed_code(
        db_session, claim_id, code="111222", expires_at=datetime.now(UTC) + timedelta(minutes=15)
    )

    resp = await client.post(_verify_url(claim_id), json={"code": "000000"}, headers=bearer(token))

    _assert_problem(resp, 400, "ERR_MEMBER_VERIFY_CODE_INVALID")
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_attempt_count == 1
    assert row.member_verified_at is None
    assert row.status == "pending"


# ===========================================================================
# 5. The 5th wrong code invalidates the code   (R5,M4)
# ===========================================================================


async def test_fifth_wrong_code_invalidates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "member-verify-fifth.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    await _seed_code(
        db_session,
        claim_id,
        code="333444",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        attempt_count=4,
    )

    resp = await client.post(_verify_url(claim_id), json={"code": "000000"}, headers=bearer(token))

    _assert_problem(resp, 429, "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS")
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_code_hash is None, "the code must be invalidated at the cap"
    assert row.member_verified_at is None
    assert row.status == "pending"

    retry = await client.post(_verify_url(claim_id), json={"code": "333444"}, headers=bearer(token))
    assert retry.status_code in (400, 410, 429), (
        f"an invalidated code must never verify, even with the ORIGINAL correct value: "
        f"got {retry.status_code}"
    )


# ===========================================================================
# 6. An expired code is rejected   (R4)
# ===========================================================================


async def test_expired_code_rejected(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    domain = "member-verify-expired.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    code = "556677"
    await _seed_code(db_session, claim_id, code=code, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    resp = await client.post(_verify_url(claim_id), json={"code": code}, headers=bearer(token))

    _assert_problem(resp, 410, "ERR_MEMBER_VERIFY_CODE_EXPIRED")
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_code_hash is None, "an expired code must be cleared"
    assert row.member_verified_at is None
    assert row.status == "pending"


# ===========================================================================
# 7. Concurrent wrong guesses cannot exceed the attempt cap   (M4,R-sec-6)
# ===========================================================================


async def test_concurrent_wrong_guesses_cap_holds(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "member-verify-concurrent.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    await _seed_code(
        db_session, claim_id, code="998877", expires_at=datetime.now(UTC) + timedelta(minutes=15)
    )

    responses = await asyncio.gather(
        *[
            client.post(_verify_url(claim_id), json={"code": "000000"}, headers=bearer(token))
            for _ in range(6)
        ],
        return_exceptions=True,
    )

    assert all(not isinstance(r, BaseException) for r in responses), (
        f"no concurrent request should raise a client-side exception: {responses}"
    )
    assert all(r.status_code != 200 for r in responses), "no wrong guess may ever succeed"  # type: ignore[union-attr]

    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_attempt_count == 5, (
        "6 concurrent wrong guesses serialized under SELECT...FOR UPDATE must record "
        f"EXACTLY 5 increments (the hard cap), got {row.member_verify_attempt_count}"
    )
    assert row.member_verify_code_hash is None, "the code must be invalidated exactly once at the cap"
    assert row.member_verified_at is None
    assert row.status == "pending"


# ===========================================================================
# 8. Resend issues a fresh code and resets the counter   (M5)
# ===========================================================================


async def test_resend_issues_fresh_code_resets_counter(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_email_sender: FakeEmailSender,
) -> None:
    app.state.email_sender = fake_email_sender
    domain = "member-verify-resend.io"
    owner_email = f"owner@{domain}"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=owner_email)
    claim_id = await _create_claim(client, token, domain)
    old_code = "121212"
    await _seed_code(
        db_session,
        claim_id,
        code=old_code,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        attempt_count=3,
    )
    old_hash = (await _claim_row(db_session, claim_id)).member_verify_code_hash

    # M1 signup-issuance already emitted one member-verify email to owner_email (see
    # test_business_signup_issues_code_to_signup_address); discard it so the count below
    # isolates the RESEND email specifically. The frozen intent — resend emits EXACTLY one
    # fresh code email to the caller's own address — is unchanged (this only removes the
    # unrelated signup-time capture from the shared spy; it does NOT weaken the assertion).
    fake_email_sender.sent.clear()

    resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_code_hash is not None
    assert row.member_verify_code_hash != old_hash, "resend must issue a genuinely FRESH code"
    assert row.member_verify_code_expires_at is not None
    assert row.member_verify_attempt_count == 0, "resend resets the attempt counter"

    assert len(fake_email_sender.sent) == 1
    assert fake_email_sender.sent[0].to == owner_email


# ===========================================================================
# 9. A member-verified row is never resolved for auto-join   (M7,R-sec-4)
# ===========================================================================


async def test_member_verified_row_never_auto_joins(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from gateway.domain_capture.infrastructure.repository import SqlAlchemyDomainClaimRepository

    domain = "member-verified-no-autojoin.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)

    await db_session.execute(
        text("UPDATE tenant_domain_claims SET member_verified_at = now() WHERE id = :id"),
        {"id": claim_id},
    )
    await db_session.commit()

    repo = SqlAlchemyDomainClaimRepository(db_session)
    resolved = await repo.resolve_verified_tenant(domain)

    assert resolved is None, "a member-verified-but-status-pending claim must NEVER auto-join"

    row = await _claim_row(db_session, claim_id)
    assert row.status == "pending"
    assert row.verified_at is None

    constraints = {
        r[0]
        for r in (
            await db_session.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = "
                    "'tenant_domain_claims'::regclass"
                )
            )
        ).all()
    }
    assert "ck_tenant_domain_claims_status" in constraints, "the frozen CheckConstraint is unchanged"

    indexes = {
        r[0]
        for r in (
            await db_session.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'tenant_domain_claims'")
            )
        ).all()
    }
    assert "uq_domain_claims_tenant_domain" in indexes
    assert "uq_domain_claims_domain_verified" in indexes


# ===========================================================================
# 10. The list response exposes member_verified_at only   (M9)
# ===========================================================================


async def test_list_exposes_member_verified_at_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "member-verified-list.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    await db_session.execute(
        text("UPDATE tenant_domain_claims SET member_verified_at = now() WHERE id = :id"),
        {"id": claim_id},
    )
    await db_session.commit()

    resp = await client.get(DOMAIN_CLAIMS, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = [c for c in body["claims"] if c["claim_id"] == claim_id]
    assert len(items) == 1, f"expected the seeded claim in the list: {body}"
    item = items[0]
    assert "member_verified_at" in item
    assert item["member_verified_at"] is not None

    raw_text = resp.text
    for forbidden in (
        "member_verify_code_hash",
        "member_verify_code_expires_at",
        "member_verify_attempt_count",
    ):
        assert forbidden not in raw_text, f"{forbidden} must never appear in an API response"


# ===========================================================================
# 11. member-verify on another tenant's claim is not found   (R1)
# ===========================================================================


async def test_member_verify_other_tenant_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _globex_id, globex_token = await signup_and_login(
        client, tenant_name="Globex", email="owner@member-verify-cross-globex.io"
    )
    globex_claim_id = await _create_claim(client, globex_token, "member-verify-cross.io")

    _acme_id, acme_token = await signup_and_login(
        client, tenant_name="Acme", email="owner@member-verify-cross-acme.io"
    )

    resp = await client.post(
        _verify_url(globex_claim_id), json={"code": "555555"}, headers=bearer(acme_token)
    )

    _assert_problem(resp, 404, "ERR_DOMAIN_CLAIM_NOT_FOUND")
    row = await _claim_row(db_session, globex_claim_id)
    assert row.member_verified_at is None
    assert row.status == "pending"


# ===========================================================================
# 12. A non-owner cannot member-verify or resend   (R2)
# ===========================================================================


async def test_non_owner_forbidden(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    domain = "non-owner-member.io"
    tenant_id, owner_token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, owner_token, domain)
    admin_token = issue_token(app, role=Role.ADMIN, tenant_id=tenant_id)

    verify_resp = await client.post(
        _verify_url(claim_id), json={"code": "555555"}, headers=bearer(admin_token)
    )
    _assert_problem(verify_resp, 403, "ERR_AUTH_FORBIDDEN")

    resend_resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(admin_token))
    _assert_problem(resend_resp, 403, "ERR_AUTH_FORBIDDEN")

    row = await _claim_row(db_session, claim_id)
    assert row.member_verified_at is None
    assert row.status == "pending"


# ===========================================================================
# 13. Resend on a generic email domain is refused   (R6,R-sec-5)
# ===========================================================================


async def test_resend_generic_domain_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email="owner@gmail.com")
    claim_id = await _create_claim(client, token, "gmail.com")

    resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(token))

    _assert_problem(resp, 422, "ERR_DOMAIN_GENERIC")
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_code_hash is None
    assert row.member_verified_at is None


# ===========================================================================
# 14. Resend on a personal account is refused   (R7,R-sec-5)
# ===========================================================================


async def test_resend_personal_account_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from gateway.tenants.infrastructure.orm import PlanRow

    # create_all doesn't replay the migration seed — seed the `free` plan via ORM
    # (mirrors test_account_type_discriminator.py's own `_seed_free_plan` idiom) so a
    # personal signup doesn't 500 on IndividualPlanMissingError before we ever reach the
    # member-verify surface under test.
    db_session.add(
        PlanRow(
            id=uuid.uuid4(),
            name="free",
            display_name="Free",
            seat_cap=1,
            budget_usd_monthly_default=None,
            rpm_limit_default=None,
            tpm_limit_default=None,
            model_allowlist=None,
            feature_flags=[],
        )
    )
    await db_session.commit()

    domain = "example-personal-refused.io"
    owner_email = f"owner@{domain}"
    signup_resp = await client.post(
        SIGNUP,
        json={
            "tenant_name": "Acme",
            "email": owner_email,
            "password": DEFAULT_PASSWORD,
            "account_type": "personal",
        },
    )
    assert signup_resp.status_code == 201, signup_resp.text

    login_resp = await client.post(LOGIN, json={"email": owner_email, "password": DEFAULT_PASSWORD})
    assert login_resp.status_code == 200, login_resp.text
    token = str(login_resp.json()["access_token"])

    claim_id = await _create_claim(client, token, domain)

    resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(token))

    _assert_problem(resp, 403, "ERR_MEMBER_VERIFY_NOT_ELIGIBLE")
    row = await _claim_row(db_session, claim_id)
    assert row.member_verify_code_hash is None


# ===========================================================================
# 15. member-verify on an already-DNS-verified claim is rejected   (R8)
# ===========================================================================


async def test_already_dns_verified_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    domain = "already-verified-member.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    create = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    assert create.status_code == 201, create.text
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(f"_ai-proxy-challenge.{domain}", claim_token)
    verify = await client.post(f"{DOMAIN_CLAIMS}/{claim_id}/verify", headers=bearer(token))
    assert verify.status_code == 200, verify.text

    verify_resp = await client.post(
        _verify_url(claim_id), json={"code": "555555"}, headers=bearer(token)
    )
    _assert_problem(verify_resp, 409, "ERR_DOMAIN_CLAIM_NOT_PENDING")

    resend_resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(token))
    _assert_problem(resend_resp, 409, "ERR_DOMAIN_CLAIM_NOT_PENDING")


# ===========================================================================
# 16. member-verify beyond the per-tenant rate limit is rejected   (R9)
# ===========================================================================


async def test_rate_limited(app: Any, client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    domain = "member-verify-ratelimit.io"
    _tenant_id, token = await signup_and_login(client, tenant_name="Acme", email=f"owner@{domain}")
    claim_id = await _create_claim(client, token, domain)
    app.state.domain_claim_rate_limiter = _AlwaysRateLimitedLimiter()

    resp = await client.post(_verify_url(claim_id), json={"code": "555555"}, headers=bearer(token))

    _assert_problem(resp, 429, "ERR_RATE_LIMITED")
    assert resp.headers.get("Retry-After") is not None
    row = await _claim_row(db_session, claim_id)
    assert row.member_verified_at is None


# ===========================================================================
# 17. member-verify on a claim whose domain != the caller's own email domain   (R10,M6b)
# ===========================================================================


async def test_domain_mismatch_refused(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _tenant_id, token = await signup_and_login(
        client, tenant_name="Acme", email="sam@acme-mismatch-caller.com"
    )
    claim_id = await _create_claim(client, token, "acme-mismatch-other.io")

    verify_resp = await client.post(
        _verify_url(claim_id), json={"code": "555555"}, headers=bearer(token)
    )
    _assert_problem(verify_resp, 403, "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH")

    resend_resp = await client.post(_resend_url(claim_id), json={}, headers=bearer(token))
    _assert_problem(resend_resp, 403, "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH")

    row = await _claim_row(db_session, claim_id)
    assert row.member_verified_at is None
    assert row.status == "pending"
    assert row.member_verify_code_hash is None


# ===========================================================================
# 18. The code is stored ONLY as a keyed hash, never plaintext   (M8,R-sec-2)
# ===========================================================================


async def test_code_stored_only_as_keyed_hash(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_email_sender: FakeEmailSender,
) -> None:
    app.state.email_sender = fake_email_sender
    domain = "example-co-hashcheck.io"
    owner_email = f"owner@{domain}"

    resp = await client.post(
        SIGNUP,
        json={"tenant_name": "Acme", "email": owner_email, "password": DEFAULT_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["tenant_id"]

    assert len(fake_email_sender.sent) == 1, "expected exactly one member-verify code email"
    message = fake_email_sender.sent[0]
    match = re.search(r"\b(\d{6})\b", message.text_body or "")
    assert match is not None, f"expected a 6-digit code in the email body: {message.text_body!r}"
    code = match.group(1)

    row = (
        await db_session.execute(
            text(
                "SELECT member_verify_code_hash FROM tenant_domain_claims "
                "WHERE tenant_id = :tid AND domain = :domain"
            ),
            {"tid": tenant_id, "domain": domain},
        )
    ).one()
    stored_hash = row.member_verify_code_hash
    assert stored_hash is not None
    assert stored_hash != code, "the plaintext code must never be stored at rest"
    assert stored_hash == _code_hash(code, TEST_JWT_SECRET), (
        "member_verify_code_hash must equal HMAC-SHA256(code, HMAC(jwt_secret, "
        "'member-verify-code')) — Option A, the frozen decision at TASK.md §3/§5"
    )
