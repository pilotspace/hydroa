"""RED->GREEN suite for the domain-restricted shareable invite link — gateway security
core (invite-by-domain TASK.md §3/§4 — FROZEN @ v1, SECURITY task 6a). One test per §4
test-plan row (27 total). Every assertion is behavioral — HTTP status + `code` field, DB
row effects, captured emails, provisioned identity — never an internal implementation
detail.

RED-FOR-THE-RIGHT-REASON: pre-Build the four routes do not exist, so FastAPI's own
unmatched-route handler returns a 404 with body `{"detail": "Not Found"}` (no `code`
field) for EVERY request. `_assert_problem` therefore ALWAYS asserts the SPECIFIC `code`
string too, so a route-missing 404 can never be mistaken for a logic-driven one. Tests
that arrange a redemption row via `_seed_redemption` (direct INSERT into
`domain_invite_redemptions`) fail pre-migration on a ProgrammingError (relation does not
exist) — the same true-red convention the sibling member-verified suite uses. No test
imports a not-yet-existing gateway module at collection time, so pytest collection
succeeds regardless of Build progress.

CONTRACT-PROSE GAP (implemented + flagged, SHAPE untouched): §3/R1 writes the create/list/
revoke permission-gate rejection as `ERR_FORBIDDEN`, but that gate is the REUSED
`require_permission(MEMBERS_MANAGE)` dependency, whose catalog spec is `AUTH_FORBIDDEN`
(code `ERR_AUTH_FORBIDDEN`) — the SAME code the sibling member-verified suite asserts for
its own owner-gate (`test_non_owner_forbidden`). There is no `ERR_FORBIDDEN` spec in
error_catalog.py. The frozen SHAPE (403 + a forbidden code) is honored; the reused gate's
real code is `ERR_AUTH_FORBIDDEN`, asserted below.

The `_code_hash` helper reimplements ONLY the frozen Option-A formula
(HMAC-SHA256(code, key=HMAC(jwt_secret, "member-verify-code"))) as an INDEPENDENT oracle
for seeding a known code + for `test_code_never_in_response_or_plaintext`; it never
substitutes for the production hash path (exercised for real via a live step-1 issue in
`test_redeem_issues_code_to_domain_mailbox`).
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

from gateway.tenants.domain.entities import Role
from gateway.tenants.infrastructure.invite_public_rate_limiter import (
    InvitePublicRateLimiter,
    InviteRateLimitedError,
)
from tests.conftest import TEST_JWT_SECRET

pytestmark = pytest.mark.asyncio

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
DOMAIN_CLAIMS = "/admin/domain-claims"
ADMIN_LINKS = "/admin/domain-invite-links"

STRONG_PASSWORD = "correct horse battery staple"
_HMAC_LABEL = b"member-verify-code"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert BOTH the HTTP status AND the specific error `code` — never status alone (a
    route-missing 404 has no `code` field, so this can't pass RED for the wrong reason)."""
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    return body


def _code_hash(code: str, jwt_secret: str = TEST_JWT_SECRET) -> str:
    """Independent oracle for the FROZEN Option-A hash-at-rest formula: HMAC-SHA256(code,
    key=HMAC(jwt_secret, "member-verify-code")), hex-encoded (§3, reused member_verify_code.py)."""
    inner = hmac.new(jwt_secret.encode(), _HMAC_LABEL, hashlib.sha256).digest()
    return hmac.new(inner, code.encode(), hashlib.sha256).hexdigest()


def _redeem_url(token: str) -> str:
    return f"/domain-invite-links/{token}/redeem"


def _verify_url(token: str) -> str:
    return f"/domain-invite-links/{token}/redeem/verify"


async def _signup_login(
    client: httpx.AsyncClient, *, tenant_name: str, email: str, password: str = STRONG_PASSWORD
) -> tuple[str, str]:
    """Bootstrap a REAL tenant+owner and log in; return (tenant_id, owner_bearer_token)."""
    s = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password}
    )
    assert s.status_code == 201, s.text
    tenant_id = s.json()["tenant_id"]
    lg = await client.post(LOGIN, json={"email": email, "password": password})
    assert lg.status_code == 200, lg.text
    return tenant_id, str(lg.json()["access_token"])


async def _member_verify_claim(
    client: httpx.AsyncClient, db_session: AsyncSession, *, token: str, domain: str
) -> str:
    """Create a real pending domain claim for the caller's tenant, then mark it
    member-verified (member_verified_at) — the create-eligibility precondition (M1). Returns
    the claim_id. Uses the shipped, frozen domain-claims + member-verify surfaces."""
    r = await client.post(DOMAIN_CLAIMS, json={"domain": domain}, headers=bearer(token))
    assert r.status_code == 201, r.text
    claim_id = str(r.json()["claim_id"])
    await db_session.execute(
        text("UPDATE tenant_domain_claims SET member_verified_at = now() WHERE id = :id"),
        {"id": claim_id},
    )
    await db_session.commit()
    return claim_id


async def _eligible_admin_with_link(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    domain: str = "acme.com",
    tenant_name: str | None = None,
    owner_email: str | None = None,
) -> dict[str, Any]:
    """Full arrange: an eligible admin (member-verified on `domain`) who mints a link.

    Returns {tenant_id, owner_token, link_id, token, domain}.
    """
    suffix = uuid.uuid4().hex[:8]
    tenant_id, owner_token = await _signup_login(
        client,
        tenant_name=tenant_name or f"Acme-{suffix}",
        email=owner_email or f"owner-{suffix}@{domain}",
    )
    await _member_verify_claim(client, db_session, token=owner_token, domain=domain)
    created = await client.post(ADMIN_LINKS, json={"domain": domain}, headers=bearer(owner_token))
    assert created.status_code == 201, created.text
    body = created.json()
    return {
        "tenant_id": tenant_id,
        "owner_token": owner_token,
        "link_id": body["id"],
        "token": body["token"],
        "domain": domain,
    }


async def _seed_redemption(
    db_session: AsyncSession,
    *,
    link_id: str,
    email: str,
    code: str,
    expires_at: datetime,
    attempt_count: int = 0,
) -> None:
    """Directly seed a (link, email) redemption row with a known keyed code hash. Pre-
    migration this raises ProgrammingError (relation domain_invite_redemptions does not
    exist) — the correct RED reason for every verify-path test arranged via this helper."""
    await db_session.execute(
        text(
            "INSERT INTO domain_invite_redemptions "
            "(id, link_id, email, code_hash, code_expires_at, attempt_count, created_at, updated_at) "
            "VALUES (:id, :link_id, :email, :h, :e, :c, now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "link_id": link_id,
            "email": email,
            "h": _code_hash(code),
            "e": expires_at,
            "c": attempt_count,
        },
    )
    await db_session.commit()


async def _links_for_tenant(db_session: AsyncSession, tenant_id: str, *, status: str | None = None) -> list[Any]:
    q = "SELECT id, domain, status, token_hash FROM domain_invite_links WHERE tenant_id = :tid"
    params: dict[str, Any] = {"tid": tenant_id}
    if status is not None:
        q += " AND status = :st"
        params["st"] = status
    return list((await db_session.execute(text(q), params)).mappings().all())


async def _redemption_row(db_session: AsyncSession, link_id: str, email: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT code_hash, code_expires_at, attempt_count FROM domain_invite_redemptions "
                "WHERE link_id = :lid AND email = :email"
            ),
            {"lid": link_id, "email": email},
        )
    ).mappings().one_or_none()


async def _users_count(db_session: AsyncSession, email: str) -> int:
    return (
        await db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :e"), {"e": email}
        )
    ).scalar_one()


async def _user_row(db_session: AsyncSession, email: str) -> Any:
    return (
        await db_session.execute(
            text("SELECT id, tenant_id, email, role, auth_method FROM users WHERE email = :e"),
            {"e": email},
        )
    ).mappings().one()


async def _seat_joined_count(db_session: AsyncSession, tenant_id: str) -> int:
    return (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM seat_membership_events "
                "WHERE tenant_id = :tid AND event_type = 'joined'"
            ),
            {"tid": tenant_id},
        )
    ).scalar_one()


class _AlwaysRateLimited:
    """A limiter double whose `.check()` always trips — proves the 429 path (M11)."""

    async def check(self, *, action: str, key: str, limit: int) -> None:
        raise InviteRateLimitedError(retry_after=7)


class _RaisingRedis:
    """A redis double whose ops raise RedisError, so the REAL InvitePublicRateLimiter
    fails OPEN (M11) — the request proceeds past the limiter."""

    async def incr(self, *_a: Any, **_k: Any) -> int:
        from redis.exceptions import RedisError

        raise RedisError("simulated redis outage")

    async def expire(self, *_a: Any, **_k: Any) -> None:
        from redis.exceptions import RedisError

        raise RedisError("simulated redis outage")


# ===========================================================================
# 1. Eligible admin mints a link                                          (M1,M2,M3)
# ===========================================================================


async def test_eligible_admin_mints_link(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tenant_id, owner_token = await _signup_login(
        client, tenant_name="Acme", email="owner@acme.com"
    )
    await _member_verify_claim(client, db_session, token=owner_token, domain="acme.com")

    # Mixed-case domain in — server lowercases it (M3).
    resp = await client.post(ADMIN_LINKS, json={"domain": "Acme.com"}, headers=bearer(owner_token))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body.keys()) == {"id", "domain", "token", "status", "expires_at", "created_at"}
    assert body["domain"] == "acme.com"
    assert body["status"] == "active"
    assert isinstance(body["token"], str) and len(body["token"]) >= 20
    ttl = datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29) < ttl <= timedelta(days=31), f"expected ~30-day TTL, got {ttl}"

    rows = await _links_for_tenant(db_session, tenant_id, status="active")
    assert len(rows) == 1, "exactly one active link for (tenant, acme.com)"
    assert rows[0]["domain"] == "acme.com"
    # Only the SHA256 hash is at rest — never the plaintext token (M2).
    assert rows[0]["token_hash"] != body["token"]
    assert rows[0]["token_hash"] == hashlib.sha256(body["token"].encode()).hexdigest()


# ===========================================================================
# 2. Re-create supersedes the active link                                 (M2 [contract])
# ===========================================================================


async def test_recreate_supersedes_active_link(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    token1 = arr["token"]

    again = await client.post(
        ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(arr["owner_token"])
    )
    assert again.status_code == 201, again.text
    token2 = again.json()["token"]
    assert token2 != token1, "re-create must rotate the token"

    active = await _links_for_tenant(db_session, arr["tenant_id"], status="active")
    assert len(active) == 1, "at most ONE active link per (tenant, domain)"
    revoked = await _links_for_tenant(db_session, arr["tenant_id"], status="revoked")
    assert len(revoked) == 1, "the old link is superseded (revoked)"

    # Redeeming with the OLD token now fails as inactive.
    old = await client.post(_redeem_url(token1), json={"email": "sam@acme.com"})
    _assert_problem(old, 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE")


# ===========================================================================
# 3. Create for an unverified domain is refused                           (R2)
# ===========================================================================


async def test_create_unverified_domain_forbidden(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tenant_id, owner_token = await _signup_login(
        client, tenant_name="Acme", email="owner@acme.com"
    )
    # A PENDING (not member/owner-verified) claim only — never eligible.
    r = await client.post(DOMAIN_CLAIMS, json={"domain": "acme.com"}, headers=bearer(owner_token))
    assert r.status_code == 201, r.text

    resp = await client.post(ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(owner_token))

    _assert_problem(resp, 403, "ERR_DOMAIN_INVITE_NOT_ELIGIBLE")
    assert await _links_for_tenant(db_session, tenant_id) == [], "no link is created"


# ===========================================================================
# 4. Create requires MEMBERS_MANAGE                                       (R1)
# ===========================================================================


async def test_create_requires_members_manage(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    tenant_id, owner_token = await _signup_login(
        client, tenant_name="Acme", email="owner@acme.com"
    )
    await _member_verify_claim(client, db_session, token=owner_token, domain="acme.com")

    # A MEMBER role lacks MEMBERS_MANAGE. NOTE (contract-prose gap): §3/R1 writes this as
    # "ERR_FORBIDDEN"; the REUSED require_permission gate's catalog code is
    # "ERR_AUTH_FORBIDDEN" (no ERR_FORBIDDEN spec exists) — the frozen 403 SHAPE holds.
    member_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member@acme.com",
    )

    resp = await client.post(
        ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(str(member_token))
    )

    _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await _links_for_tenant(db_session, tenant_id) == [], "no link is created"


# ===========================================================================
# 5. List is active + tenant-scoped, token-free                           (M4)
# ===========================================================================


async def test_list_active_tenant_scoped_no_token(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    a = await _eligible_admin_with_link(client, db_session, domain="acme.com", tenant_name="A")
    # Tenant A also has a revoked link.
    again = await client.post(ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(a["owner_token"]))
    assert again.status_code == 201
    active_token = again.json()["token"]
    # Tenant B has its own active link on a different domain.
    b = await _eligible_admin_with_link(client, db_session, domain="globex.io", tenant_name="B")

    resp = await client.get(ADMIN_LINKS, headers=bearer(a["owner_token"]))

    assert resp.status_code == 200, resp.text
    links = resp.json()["links"]
    assert len(links) == 1, f"only A's single ACTIVE link: {links}"
    item = links[0]
    assert set(item.keys()) == {"id", "domain", "status", "expires_at", "created_at"}
    assert "token" not in item and "token_hash" not in item
    assert item["status"] == "active"
    assert item["domain"] == "acme.com"
    # B's link never appears in A's list.
    assert b["link_id"] not in {lk["id"] for lk in links}
    # sanity: A's returned link is the still-active one.
    assert item["id"] != a["link_id"], "the superseded link is not listed"
    assert active_token  # the active link's plaintext is not asserted here (never in list)


# ===========================================================================
# 6. Revoke stops redemption                                              (M5)
# ===========================================================================


async def test_revoke_stops_redemption(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")

    resp = await client.delete(f"{ADMIN_LINKS}/{arr['link_id']}", headers=bearer(arr["owner_token"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"id": arr["link_id"], "status": "revoked"}

    active = await _links_for_tenant(db_session, arr["tenant_id"], status="active")
    assert active == [], "no active link remains"

    after = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    _assert_problem(after, 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE")


# ===========================================================================
# 7. Revoke of another tenant's link is not found                         (R13)
# ===========================================================================


async def test_revoke_other_tenant_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    a = await _eligible_admin_with_link(client, db_session, domain="acme.com", tenant_name="A")
    _b_tenant, b_token = await _signup_login(client, tenant_name="B", email="owner@globex.io")

    resp = await client.delete(f"{ADMIN_LINKS}/{a['link_id']}", headers=bearer(b_token))

    _assert_problem(resp, 404, "ERR_INVITE_NOT_FOUND")
    active = await _links_for_tenant(db_session, a["tenant_id"], status="active")
    assert len(active) == 1, "the other tenant's link stays active (no cross-tenant mutation)"


# ===========================================================================
# 8. Redeem step-1 issues a code to an @domain mailbox                    (M6)
# ===========================================================================


async def test_redeem_issues_code_to_domain_mailbox(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    sender = FakeEmailSender()
    app.state.email_sender = sender
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    sender.sent.clear()  # discard any signup-time member-verify email

    resp = await client.post(_redeem_url(arr["token"]), json={"email": "Sam@Acme.com"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"email": "sam@acme.com"}

    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row is not None, "a (link, sam@acme.com) redemption row exists"
    assert row["code_hash"] is not None
    assert row["attempt_count"] == 0
    ttl = row["code_expires_at"] - datetime.now(UTC)
    assert timedelta(minutes=10) < ttl <= timedelta(minutes=16), f"expected ~15-min TTL, got {ttl}"

    assert len(sender.sent) == 1, "exactly one code email"
    assert sender.sent[0].to == "sam@acme.com"
    # The code appears in the email body but the stored hash is NOT the plaintext.
    m = re.search(r"\b(\d{6})\b", sender.sent[0].text_body or "")
    assert m is not None, sender.sent[0].text_body
    assert row["code_hash"] == _code_hash(m.group(1)), "stored keyed hash matches the emailed code"


# ===========================================================================
# 9. Redeem step-1 rejects a non-@domain email with no email sent         (M6,R3)
# ===========================================================================


# eve@evil.com (other domain) · sam@sub.acme.com (subdomain != apex) · root@127.0.0.1 (IP
# literal, normalize fail-closed) · sam@акме.com (raw non-ASCII host,
# normalize_domain raises -> treated as mismatch, fail-closed) — every non-@acme.com email.
@pytest.mark.parametrize(
    "bad_email",
    ["eve@evil.com", "sam@sub.acme.com", "root@127.0.0.1", "sam@акме.com"],
)
async def test_redeem_rejects_non_domain_email_no_email(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession, bad_email: str
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    sender = FakeEmailSender()
    app.state.email_sender = sender
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    sender.sent.clear()

    resp = await client.post(_redeem_url(arr["token"]), json={"email": bad_email})

    _assert_problem(resp, 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH")
    assert await _redemption_row(db_session, arr["link_id"], bad_email.lower()) is None
    assert sender.sent == [], "no code email is sent on a domain mismatch"


# ===========================================================================
# 10. Re-issue supersedes and resets the code                             (M6)
# ===========================================================================


async def test_reissue_supersedes_and_resets(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    app.state.email_sender = FakeEmailSender()
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="111222",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        attempt_count=3,
    )
    old = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")

    resp = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})

    assert resp.status_code == 202, resp.text
    new = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert new is not None
    assert new["code_hash"] != old["code_hash"], "re-issue replaces the code"
    assert new["attempt_count"] == 0, "attempt counter reset"
    assert new["code_expires_at"] > old["code_expires_at"], "expiry refreshed"


# ===========================================================================
# 11. Step-1 does not reveal an existing account                          (M8)
# ===========================================================================


async def test_step1_no_account_existence_oracle(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    app.state.email_sender = FakeEmailSender()
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    # sam@acme.com is ALREADY a registered global user (a different tenant).
    await _signup_login(client, tenant_name="Existing", email="sam@acme.com")

    existing = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    fresh = await client.post(_redeem_url(arr["token"]), json={"email": "new@acme.com"})

    # Indistinguishable: BOTH are a plain 202 with the same body shape.
    assert existing.status_code == 202, existing.text
    assert fresh.status_code == 202, fresh.text
    assert existing.json() == {"email": "sam@acme.com"}
    assert fresh.json() == {"email": "new@acme.com"}


# ===========================================================================
# 12. Verify with the correct code provisions a MEMBER               (M7,M9,M10)
# ===========================================================================


async def test_verify_correct_code_provisions_member(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="482913",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "482913", "password": STRONG_PASSWORD},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body.keys()) == {"tenant_id", "user_id", "email"}
    assert body["tenant_id"] == arr["tenant_id"]
    assert body["email"] == "sam@acme.com"

    row = await _user_row(db_session, "sam@acme.com")
    assert str(row["tenant_id"]) == arr["tenant_id"]
    assert row["role"] == "member", "role is FIXED MEMBER (M10)"
    assert row["auth_method"] == "password"
    assert str(row["id"]) == body["user_id"]
    assert await _seat_joined_count(db_session, arr["tenant_id"]) >= 1

    # The redemption row is consumed (single-use, M9).
    assert await _redemption_row(db_session, arr["link_id"], "sam@acme.com") is None

    # The domain claim's status/member_verified path is UNCHANGED (M10 — never auto-join).
    claim = (
        await db_session.execute(
            text("SELECT status, verified_at FROM tenant_domain_claims WHERE domain = 'acme.com'")
        )
    ).mappings().first()
    assert claim["status"] == "pending"
    assert claim["verified_at"] is None

    # Replay of the same correct code after success no longer verifies.
    replay = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "482913", "password": STRONG_PASSWORD},
    )
    _assert_problem(replay, 400, "ERR_MEMBER_VERIFY_CODE_INVALID")


# ===========================================================================
# 13. Wrong code increments and does not provision                        (R4)
# ===========================================================================


async def test_verify_wrong_code_increments(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="111222",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "000000", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 400, "ERR_MEMBER_VERIFY_CODE_INVALID")
    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row["attempt_count"] == 1
    assert await _users_count(db_session, "sam@acme.com") == 0


# ===========================================================================
# 14. Attempt cap invalidates the code                                    (R5)
# ===========================================================================


async def test_verify_cap_invalidates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="333444",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        attempt_count=4,  # one below the cap of 5
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "000000", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 429, "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS")
    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row is None or row["code_hash"] is None, "the code is invalidated at the cap"
    assert await _users_count(db_session, "sam@acme.com") == 0

    # Even the ORIGINAL correct code no longer verifies.
    retry = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "333444", "password": STRONG_PASSWORD},
    )
    assert retry.status_code in (400, 410, 429), retry.text


# ===========================================================================
# 15. Expired code is refused                                             (R6)
# ===========================================================================


async def test_verify_expired_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="556677",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "556677", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 410, "ERR_MEMBER_VERIFY_CODE_EXPIRED")
    assert await _users_count(db_session, "sam@acme.com") == 0


# ===========================================================================
# 16. Weak password is refused before consuming the code                  (R7)
# ===========================================================================


async def test_verify_weak_password_keeps_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="778899",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "778899", "password": "short"},
    )

    _assert_problem(resp, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert await _users_count(db_session, "sam@acme.com") == 0
    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row is not None and row["code_hash"] is not None, "the code is NOT consumed"
    assert row["attempt_count"] == 0, "a weak password is not a wrong-code attempt"


# ===========================================================================
# 17. An already-registered email cannot double-provision                 (R8)
# ===========================================================================


async def test_verify_email_taken_no_double_provision(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    # sam@acme.com already a GLOBAL user in a different tenant.
    await _signup_login(client, tenant_name="Existing", email="sam@acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="223344",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "223344", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await _users_count(db_session, "sam@acme.com") == 1, "no second users row"
    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row is not None and row["code_hash"] is not None, "link + code remain intact"


# ===========================================================================
# 18. Seat cap blocks the join                                            (R12)
# ===========================================================================


async def test_verify_seat_cap_blocks(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from tests.plan_seat_cap.conftest import (
        assign_plan,
        seed_extra_active_users,
        seed_plan,
    )

    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    plan_id = await seed_plan(db_session, name="starter-domlink-cap", seat_cap=1)
    await assign_plan(db_session, tenant_id=uuid.UUID(arr["tenant_id"]), plan_id=plan_id)
    # The owner already occupies the single seat -> the tenant is AT its cap.
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="909090",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "909090", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    assert await _users_count(db_session, "sam@acme.com") == 0


# ===========================================================================
# 19. Redeem against a revoked link                                       (R9)
# ===========================================================================


async def test_redeem_revoked_link(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="121212",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    revoke = await client.delete(f"{ADMIN_LINKS}/{arr['link_id']}", headers=bearer(arr["owner_token"]))
    assert revoke.status_code == 200, revoke.text

    step1 = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    _assert_problem(step1, 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE")

    verify = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "121212", "password": STRONG_PASSWORD},
    )
    _assert_problem(verify, 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE")
    assert await _users_count(db_session, "sam@acme.com") == 0


# ===========================================================================
# 20. Redeem against an expired link                                      (R10)
# ===========================================================================


async def test_redeem_expired_link(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await db_session.execute(
        text("UPDATE domain_invite_links SET expires_at = now() - interval '1 day' WHERE id = :id"),
        {"id": arr["link_id"]},
    )
    await db_session.commit()

    resp = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    _assert_problem(resp, 410, "ERR_INVITE_EXPIRED")


# ===========================================================================
# 21. Redeem against an unknown token                                     (R11)
# ===========================================================================


async def test_redeem_unknown_token(client: httpx.AsyncClient) -> None:
    import secrets

    resp = await client.post(_redeem_url(secrets.token_urlsafe(32)), json={"email": "sam@acme.com"})
    _assert_problem(resp, 404, "ERR_INVITE_NOT_FOUND")


# ===========================================================================
# 22. Concurrent verify with the same correct code provisions once   (R14,M7)
# ===========================================================================


async def test_concurrent_verify_provisions_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="424242",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    payload = {"email": "sam@acme.com", "code": "424242", "password": STRONG_PASSWORD}
    r1, r2 = await asyncio.gather(
        client.post(_verify_url(arr["token"]), json=payload),
        client.post(_verify_url(arr["token"]), json=payload),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [201, 400], f"exactly one 201 and one 400 (consumed), got {statuses}"
    loser = r1 if r1.status_code == 400 else r2
    _assert_problem(loser, 400, "ERR_MEMBER_VERIFY_CODE_INVALID")
    assert await _users_count(db_session, "sam@acme.com") == 1, "exactly one users row"
    assert await _redemption_row(db_session, arr["link_id"], "sam@acme.com") is None


# ===========================================================================
# 23. Injected identity fields are ignored                                (R15)
# ===========================================================================


async def test_verify_ignores_injected_identity(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    other_tenant = str(uuid.uuid4())
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="sam@acme.com",
        code="565656",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={
            "email": "sam@acme.com",
            "code": "565656",
            "password": STRONG_PASSWORD,
            "tenant_id": other_tenant,
            "role": "superadmin",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] == arr["tenant_id"], "tenant is the link's, not the injected one"
    assert body["tenant_id"] != other_tenant
    row = await _user_row(db_session, "sam@acme.com")
    assert row["role"] == "member", "role is MEMBER, never the injected SUPERADMIN"
    assert str(row["tenant_id"]) == arr["tenant_id"]


# ===========================================================================
# 24. Rate limit both public steps, fail-open on Redis outage             (M11)
# ===========================================================================


async def test_rate_limit_both_steps_fail_open(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")

    # (a) Exhausted limiter -> 429 with Retry-After, on BOTH public steps.
    app.state.invite_public_limiter = _AlwaysRateLimited()
    r_redeem = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    _assert_problem(r_redeem, 429, "ERR_RATE_LIMITED")
    assert r_redeem.headers.get("Retry-After") is not None
    r_verify = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": "000000", "password": STRONG_PASSWORD},
    )
    _assert_problem(r_verify, 429, "ERR_RATE_LIMITED")
    assert r_verify.headers.get("Retry-After") is not None

    # (b) Redis outage -> the REAL limiter fails OPEN; the request proceeds past it.
    app.state.invite_public_limiter = InvitePublicRateLimiter(redis=_RaisingRedis())
    proceed = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    assert proceed.status_code == 202, f"fail-open: request must proceed, got {proceed.text}"


# ===========================================================================
# 25. Create/revoke/join are audited fail-open                            (M12)
# ===========================================================================


async def test_create_revoke_join_audited_fail_open(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gateway.tenants.api.domain_invite_links_router as admin_mod
    import gateway.tenants.api.domain_invite_redeem_router as public_mod

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("simulated audit outage")

    # Even when the audit writer raises, the fire-and-forget dispatch must never block the
    # success response (create, revoke, verify each stay green).
    monkeypatch.setattr(admin_mod, "record_audit", _boom)
    monkeypatch.setattr(public_mod, "record_audit", _boom)

    tenant_id, owner_token = await _signup_login(client, tenant_name="Acme", email="owner@acme.com")
    await _member_verify_claim(client, db_session, token=owner_token, domain="acme.com")

    created = await client.post(ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(owner_token))
    assert created.status_code == 201, created.text
    link = created.json()

    await _seed_redemption(
        db_session,
        link_id=link["id"],
        email="sam@acme.com",
        code="606060",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    verified = await client.post(
        _verify_url(link["token"]),
        json={"email": "sam@acme.com", "code": "606060", "password": STRONG_PASSWORD},
    )
    assert verified.status_code == 201, verified.text

    revoked = await client.delete(f"{ADMIN_LINKS}/{link['id']}", headers=bearer(owner_token))
    assert revoked.status_code == 200, revoked.text


# ===========================================================================
# 26. The code never appears in a response body or at rest in plaintext   (M9)
# ===========================================================================


async def test_code_never_in_response_or_plaintext(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    sender = FakeEmailSender()
    app.state.email_sender = sender
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    sender.sent.clear()

    step1 = await client.post(_redeem_url(arr["token"]), json={"email": "sam@acme.com"})
    assert step1.status_code == 202, step1.text
    m = re.search(r"\b(\d{6})\b", sender.sent[0].text_body or "")
    assert m is not None
    code = m.group(1)

    # The code is NOT echoed in the step-1 response body.
    assert code not in step1.text

    # At rest: only the keyed hash, never the plaintext code.
    row = await _redemption_row(db_session, arr["link_id"], "sam@acme.com")
    assert row["code_hash"] != code
    assert row["code_hash"] == _code_hash(code)

    # The verify response also never contains the code.
    step2 = await client.post(
        _verify_url(arr["token"]),
        json={"email": "sam@acme.com", "code": code, "password": STRONG_PASSWORD},
    )
    assert step2.status_code == 201, step2.text
    assert code not in step2.text


# ===========================================================================
# 27. Step-1 fails CLOSED on a malformed addr-spec redeemer email           (M6,R3)
# ===========================================================================
# SECURITY (verify-lens-B finding): the domain gate must not accept an email whose
# last '@'-segment is the link domain while the mailbox actually addresses a FOREIGN
# domain. `x@evil.com@acme.com` (two '@') and control/whitespace-laden local parts must
# be rejected as a domain MISMATCH BEFORE any code is armed or emailed — the feature's OWN
# control, never a reliance on MTA routing or incidental stdlib CRLF hardening.
@pytest.mark.parametrize(
    "malformed_email",
    [
        "x@evil.com@acme.com",       # embedded '@' — rsplit domain is acme.com, mailbox foreign
        "attacker@evil.com@acme.com",
        "sam foo@acme.com",          # whitespace in the local part
        "sam\tfoo@acme.com",         # tab in the local part
        "sam\r\nbcc:x@evil.com@acme.com",  # CRLF header-injection attempt
        "@acme.com",                 # empty local part
    ],
)
async def test_redeem_rejects_malformed_addrspec_email(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession, malformed_email: str
) -> None:
    from tests.domain_capture.conftest import FakeEmailSender

    sender = FakeEmailSender()
    app.state.email_sender = sender
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    sender.sent.clear()

    resp = await client.post(_redeem_url(arr["token"]), json={"email": malformed_email})

    _assert_problem(resp, 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH")
    assert sender.sent == [], "no code email is armed/sent for a malformed addr-spec"
    # No redemption row was written under ANY casing of the malformed string.
    rows = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM domain_invite_redemptions WHERE link_id = :lid"),
            {"lid": arr["link_id"]},
        )
    ).scalar_one()
    assert rows == 0, "no redemption row is written on a malformed addr-spec"


# ===========================================================================
# 28. Step-2 re-checks the addr-spec (defense-in-depth)                      (M7,R3)
# ===========================================================================
async def test_verify_rejects_malformed_addrspec_email(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    arr = await _eligible_admin_with_link(client, db_session, domain="acme.com")
    # A redemption row directly seeded under the malformed key (simulating a bypass of
    # step-1): step-2 must STILL fail closed on the domain gate, provisioning no user.
    await _seed_redemption(
        db_session,
        link_id=arr["link_id"],
        email="x@evil.com@acme.com",
        code="707070",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    resp = await client.post(
        _verify_url(arr["token"]),
        json={"email": "x@evil.com@acme.com", "code": "707070", "password": STRONG_PASSWORD},
    )

    _assert_problem(resp, 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH")
    assert await _users_count(db_session, "x@evil.com@acme.com") == 0


# ===========================================================================
# 29. CREATE eligibility is tenant-scoped (anti-confused-deputy)             (M1,R2)
# ===========================================================================
async def test_create_eligibility_is_tenant_scoped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Tenant B is member-verified on acme.com; tenant A is NOT. A must be refused a link
    # for acme.com — is_domain_eligible must never read B's claim for A.
    await _eligible_admin_with_link(client, db_session, domain="acme.com", tenant_name="B")
    a_tenant, a_token = await _signup_login(client, tenant_name="A", email="admin@other-co.io")

    resp = await client.post(ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(a_token))

    _assert_problem(resp, 403, "ERR_DOMAIN_INVITE_NOT_ELIGIBLE")
    assert await _links_for_tenant(db_session, a_tenant) == [], "A gets no acme.com link"


# ===========================================================================
# 30. CREATE is unlocked by the owner-verified (DNS) branch too              (M1)
# ===========================================================================
async def test_create_owner_verified_branch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # The M1 OR-branch: status='verified' (DNS owner-verified) unlocks create even with
    # NO member_verified_at.
    tenant_id, owner_token = await _signup_login(
        client, tenant_name="Owner", email="owner@acme.com"
    )
    r = await client.post(DOMAIN_CLAIMS, json={"domain": "acme.com"}, headers=bearer(owner_token))
    assert r.status_code == 201, r.text
    claim_id = str(r.json()["claim_id"])
    await db_session.execute(
        text(
            "UPDATE tenant_domain_claims SET status = 'verified', verified_at = now(), "
            "member_verified_at = NULL WHERE id = :id"
        ),
        {"id": claim_id},
    )
    await db_session.commit()

    resp = await client.post(ADMIN_LINKS, json={"domain": "acme.com"}, headers=bearer(owner_token))

    assert resp.status_code == 201, resp.text
    assert resp.json()["domain"] == "acme.com"
    active = await _links_for_tenant(db_session, tenant_id, status="active")
    assert len(active) == 1, "owner-verified domain mints a link"
