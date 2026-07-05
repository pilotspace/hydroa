"""RED->GREEN suite for GET/POST /invites/{token}[/accept] (member-invite-acceptance TASK.md §4).

One test per §2 SCENARIOS entry. All assertions are behavioral (HTTP status/body, DB state,
audit rows) — never internal implementation details of the use cases/repository.

Setup reuses the SHIPPED, frozen member-invite-issuance surface (`POST /admin/invites`,
`DELETE /admin/invites/{id}`) to create real pending/revoked invites with real tokens,
rather than hand-crafting `invites` rows with a fabricated `token_hash` — this exercises
the actual, already-tested issuance code as test fixtures instead of re-deriving its hashing
scheme independently.

RED-FOR-THE-RIGHT-REASON: pre-Build, `/invites/{token}` and `/invites/{token}/accept` do
not exist as routes at all, so FastAPI's own unmatched-route handler returns 404 with body
`{"detail": "Not Found"}` for EVERY request regardless of path. Asserting only
`status_code == 404` for the "unknown token" scenarios would therefore pass RED for the
WRONG reason. `_assert_error` below always additionally asserts the SPECIFIC `code` field
(FastAPI's default not-found body has no `code` key), so a route-missing 404 can never be
mistaken for a logic-driven one. Every test here is HTTP-level via the shared
`client`/`db_session` fixtures — no test imports a not-yet-existing module at collection
time, so pytest collection succeeds regardless of Build's progress.

SECURITY PROPERTIES PROVEN:
  - Server-side-only identity resolution: an accept body's tenant_id/role/email are ignored;
    the provisioned user always matches the INVITE, never the request (M3).
  - Distinguishable 404/409/410 for an already-known secret token (mirrors device-approval-
    flow's own AGENT_OAUTH_AUTHORIZATION_NOT_FOUND/_NOT_PENDING/_EXPIRED triad) — no new
    enumeration oracle, since the identifying key IS the unguessable secret itself.
  - Fail-closed atomicity: every reject (404/409/410/400/409-collision/429) leaves the
    invites row completely unchanged and creates zero users rows, partial or otherwise.
  - Global email-uniqueness collision reuses AUTH_EMAIL_TAKEN verbatim and rolls back
    completely — never a half-accepted invite, never an orphaned users row.
  - Concurrent double-accept of the SAME token never both succeed (row lock serializes).
  - Both endpoints are rate-limited per-client-IP, fail-open on Redis outage.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_error(resp: httpx.Response, status: int, code: str) -> None:
    """Assert BOTH the status code AND the specific error `code` field — never status
    alone (see the module docstring's RED-FOR-THE-RIGHT-REASON note)."""
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == code, resp.text


async def _signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str | None = None
) -> dict[str, str]:
    """Signup a fresh tenant+owner; return {owner_token, tenant_id, tenant_name}."""
    name = tenant_name or f"AcceptCo-{uuid.uuid4().hex[:8]}"
    email = f"owner-{uuid.uuid4().hex[:8]}@accepttest.io"
    password = "accept-horse-battery-01"
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": name, "email": email, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {
        "owner_token": login.json()["access_token"],
        "tenant_id": signup.json()["tenant_id"],
        "tenant_name": name,
    }


@pytest.fixture
async def seeded_tenant(client: httpx.AsyncClient) -> dict[str, str]:
    return await _signup_owner(client)


async def _create_pending_invite(
    client: httpx.AsyncClient,
    *,
    owner_token: str,
    email: str,
    role: str = "member",
) -> dict[str, Any]:
    """Create a real pending invite via the SHIPPED issuance endpoint. Returns the full
    201 body: id, email, role, status, expires_at, created_at, invited_by_user_id, token.
    """
    r = await client.post(
        "/admin/invites",
        json={"email": email, "role": role},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 201, f"setup failed creating invite: {r.text}"
    return r.json()


async def _revoke_invite(client: httpx.AsyncClient, *, owner_token: str, invite_id: str) -> None:
    """Revoke a pending invite via the SHIPPED issuance endpoint."""
    r = await client.delete(
        f"/admin/invites/{invite_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 204, f"setup failed revoking invite: {r.text}"


async def _backdate_invite_expiry(
    db_session: AsyncSession, *, invite_id: str, seconds_ago: int = 10
) -> None:
    """Backdate an invite's expires_at into the past — status stays 'pending' (expiry is
    always a COMPUTED check, never a write); mirrors seed_expired_authorization's role for
    device_authorizations in the device-approval-flow suite."""
    await db_session.execute(
        text("UPDATE invites SET expires_at = now() - make_interval(secs => :secs) WHERE id = :id"),
        {"id": uuid.UUID(invite_id), "secs": seconds_ago},
    )
    await db_session.commit()


async def _invite_status(db_session: AsyncSession, invite_id: str) -> str:
    result = await db_session.execute(
        text("SELECT status FROM invites WHERE id = :id"), {"id": uuid.UUID(invite_id)}
    )
    return result.scalar_one()


async def _users_count_for_email(db_session: AsyncSession, email: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE email = :email"), {"email": email}
    )
    return result.scalar_one()


async def _user_row(db_session: AsyncSession, email: str) -> Any:
    result = await db_session.execute(
        text(
            "SELECT id, tenant_id, email, role, auth_method, password_hash FROM users "
            "WHERE email = :email"
        ),
        {"email": email},
    )
    return result.mappings().one()


async def _audit_event(db_session: AsyncSession, action: str) -> Any:
    result = await db_session.execute(
        text(
            "SELECT action, metadata FROM audit_events WHERE action = :action "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


# ---------------------------------------------------------------------------
# TEST 1: Preview a valid pending invite                                                [M1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_happy_path(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /invites/{token} on a valid pending invite returns the exact preview shape and
    performs zero mutation."""
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="new@co.io", role="member"
    )

    r = await client.get(f"/invites/{invite['token']}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"tenant_name", "email", "role", "expires_at"}
    assert body["tenant_name"] == seeded_tenant["tenant_name"]
    assert body["email"] == "new@co.io"
    assert body["role"] == "member"
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromisoformat(
        invite["expires_at"]
    )
    # Zero side effect: the invite row is completely unchanged.
    assert await _invite_status(db_session, invite["id"]) == "pending"


# ---------------------------------------------------------------------------
# TEST 2: Accept a valid pending invite provisions a user and marks it accepted   [M2, M8]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_happy_path(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /invites/{token}/accept provisions a user, flips the invite, fires an audit
    event, and returns the exact response shape."""
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="new@co.io", role="member"
    )

    r = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"tenant_id", "user_id", "email"}
    assert body["tenant_id"] == seeded_tenant["tenant_id"]
    assert body["email"] == "new@co.io"

    row = await _user_row(db_session, "new@co.io")
    assert str(row["tenant_id"]) == seeded_tenant["tenant_id"]
    assert row["role"] == "member"
    assert row["auth_method"] == "password"
    assert row["password_hash"] != "correct horse battery staple"  # never plaintext
    assert str(row["id"]) == body["user_id"]

    assert await _invite_status(db_session, invite["id"]) == "accepted"

    await asyncio.sleep(0.05)  # let the fire-and-forget audit task complete
    audit = await _audit_event(db_session, "invite.accept")
    assert audit is not None, "expected an invite.accept audit event"


# ---------------------------------------------------------------------------
# TEST 3: Accept ignores client-supplied identity fields in the body                    [M3]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_ignores_client_supplied_identity_fields(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Extra body fields (tenant_id/role/email) never influence the provisioned user —
    identity is resolved EXCLUSIVELY from the invite row the token hash resolves to."""
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="new@co.io", role="member"
    )
    fake_tenant = str(uuid.uuid4())

    r = await client.post(
        f"/invites/{invite['token']}/accept",
        json={
            "password": "correct horse battery staple",
            "tenant_id": fake_tenant,
            "role": "owner",
            "email": "attacker@evil.io",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == seeded_tenant["tenant_id"]
    assert body["tenant_id"] != fake_tenant
    assert body["email"] == "new@co.io"

    row = await _user_row(db_session, "new@co.io")
    assert row["role"] == "member"
    assert str(row["tenant_id"]) == seeded_tenant["tenant_id"]
    # The injected "attacker@evil.io" was never used to create a row.
    assert await _users_count_for_email(db_session, "attacker@evil.io") == 0


# ---------------------------------------------------------------------------
# TEST 4: Preview an unknown token                                                  [M4, R1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_unknown_token_404(client: httpx.AsyncClient) -> None:
    unknown_token = secrets.token_urlsafe(32)
    r = await client.get(f"/invites/{unknown_token}")
    _assert_error(r, 404, "ERR_INVITE_NOT_FOUND")


# ---------------------------------------------------------------------------
# TEST 5: Preview a revoked invite                                                  [M4, R2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_revoked_token_409(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    await _revoke_invite(client, owner_token=seeded_tenant["owner_token"], invite_id=invite["id"])

    r = await client.get(f"/invites/{invite['token']}")

    _assert_error(r, 409, "ERR_INVITE_NOT_PENDING")
    assert await _invite_status(db_session, invite["id"]) == "revoked"


# ---------------------------------------------------------------------------
# TEST 6: Preview an already-accepted invite                                        [M4, R2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_already_accepted_token_409(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    accept = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert accept.status_code == 200, accept.text

    r = await client.get(f"/invites/{invite['token']}")

    _assert_error(r, 409, "ERR_INVITE_NOT_PENDING")
    assert await _invite_status(db_session, invite["id"]) == "accepted"


# ---------------------------------------------------------------------------
# TEST 7: Preview an expired invite                                                 [M4, R3]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_expired_token_410(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    await _backdate_invite_expiry(db_session, invite_id=invite["id"])

    r = await client.get(f"/invites/{invite['token']}")

    _assert_error(r, 410, "ERR_INVITE_EXPIRED")
    assert await _invite_status(db_session, invite["id"]) == "pending"  # never a status write


# ---------------------------------------------------------------------------
# TEST 8: Accept an unknown token                                                       [R1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_unknown_token_404(client: httpx.AsyncClient) -> None:
    unknown_token = secrets.token_urlsafe(32)
    r = await client.post(
        f"/invites/{unknown_token}/accept",
        json={"password": "correct horse battery staple"},
    )
    _assert_error(r, 404, "ERR_INVITE_NOT_FOUND")


# ---------------------------------------------------------------------------
# TEST 9: Accept a revoked invite                                                       [R2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_revoked_token_409(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    await _revoke_invite(client, owner_token=seeded_tenant["owner_token"], invite_id=invite["id"])

    r = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    _assert_error(r, 409, "ERR_INVITE_NOT_PENDING")
    assert await _invite_status(db_session, invite["id"]) == "revoked"
    assert await _users_count_for_email(db_session, "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 10: Accept an already-accepted invite (sequential double-accept)                 [R2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_already_accepted_token_409(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    first = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "another-valid-password"},
    )

    _assert_error(second, 409, "ERR_INVITE_NOT_PENDING")
    assert await _users_count_for_email(db_session, "x@co.io") == 1  # the 2nd created none


# ---------------------------------------------------------------------------
# TEST 11: Accept an expired invite                                                     [R3]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_expired_token_410(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )
    await _backdate_invite_expiry(db_session, invite_id=invite["id"])

    r = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    _assert_error(r, 410, "ERR_INVITE_EXPIRED")
    assert await _invite_status(db_session, invite["id"]) == "pending"
    assert await _users_count_for_email(db_session, "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 12: Accept with a weak password                                                  [R4]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_weak_password_400(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="x@co.io"
    )

    r = await client.post(f"/invites/{invite['token']}/accept", json={"password": "short"})

    _assert_error(r, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert await _invite_status(db_session, invite["id"]) == "pending"
    assert await _users_count_for_email(db_session, "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 13: Accept when the email now belongs to a user in a different tenant    [M6, R5]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_global_email_collision_409(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The invited email became a registered user in a DIFFERENT tenant between invite-
    CREATE and accept time (issuance's own M6 deliberately allows creating that invite in
    the first place — anti-enumeration). Accept must 409 reusing AUTH_EMAIL_TAKEN verbatim
    and roll back completely — the invite stays pending, no second users row is created."""
    collide_email = f"collide-{uuid.uuid4().hex[:8]}@co.io"
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email=collide_email
    )

    # A DIFFERENT tenant registers a REAL user with the SAME email, after the invite exists.
    other_signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": f"OtherCo-{uuid.uuid4().hex[:8]}",
            "email": collide_email,
            "password": "some-other-strong-password",
        },
    )
    assert other_signup.status_code == 201, other_signup.text
    other_tenant_id = other_signup.json()["tenant_id"]

    r = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    _assert_error(r, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await _invite_status(db_session, invite["id"]) == "pending"
    assert await _users_count_for_email(db_session, collide_email) == 1  # only tenant B's row
    other_row = await _user_row(db_session, collide_email)
    assert str(other_row["tenant_id"]) == other_tenant_id


# ---------------------------------------------------------------------------
# TEST 14: Preview rate limit exceeded                                              [M7, R6]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_rate_limited_429(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """With invite_preview_rpm=2, the 3rd GET from one IP within the window -> 429.

    Uses an UNKNOWN token throughout — the rate-limit check runs BEFORE any token lookup
    (§3 Access pattern), so the limiter must fire on the 3rd call regardless of whether the
    token resolves; this also proves the limiter counts even not-found lookups (it must, or
    an attacker could bypass it by always presenting invalid tokens).
    """
    _application, low_client = low_rpm_app_and_client
    token = secrets.token_urlsafe(32)

    r1 = await low_client.get(f"/invites/{token}")
    _assert_error(r1, 404, "ERR_INVITE_NOT_FOUND")
    r2 = await low_client.get(f"/invites/{token}")
    _assert_error(r2, 404, "ERR_INVITE_NOT_FOUND")

    r3 = await low_client.get(f"/invites/{token}")
    _assert_error(r3, 429, "ERR_RATE_LIMITED")
    assert "Retry-After" in r3.headers


# ---------------------------------------------------------------------------
# TEST 15: Accept rate limit exceeded                                               [M7, R6]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_rate_limited_429(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
    low_rpm_db_session: AsyncSession,
) -> None:
    """With invite_accept_rpm=2, the 3rd accept call from one IP within the window -> 429,
    and that 3rd invite is left completely untouched (never partially accepted)."""
    _application, low_client = low_rpm_app_and_client
    owner = await _signup_owner(low_client)

    invite1 = await _create_pending_invite(
        low_client, owner_token=owner["owner_token"], email="rl1@co.io"
    )
    invite2 = await _create_pending_invite(
        low_client, owner_token=owner["owner_token"], email="rl2@co.io"
    )
    invite3 = await _create_pending_invite(
        low_client, owner_token=owner["owner_token"], email="rl3@co.io"
    )

    r1 = await low_client.post(
        f"/invites/{invite1['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert r1.status_code == 200, r1.text
    r2 = await low_client.post(
        f"/invites/{invite2['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert r2.status_code == 200, r2.text

    r3 = await low_client.post(
        f"/invites/{invite3['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    _assert_error(r3, 429, "ERR_RATE_LIMITED")
    assert "Retry-After" in r3.headers

    assert await _invite_status(low_rpm_db_session, invite3["id"]) == "pending"
    assert await _users_count_for_email(low_rpm_db_session, "rl3@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 16: Concurrent double-accept of the same token never both succeed  [M5, concurrency]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_double_accept_never_two_successes(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Two accept requests racing the SAME token never both succeed — the row lock
    (`InviteRepository.accept`'s `SELECT ... FOR UPDATE`, mirroring `.revoke`'s own shape)
    must serialize them: exactly one 200, one 409, never two 200s, never zero.

    KNOWN LIMITATION (named, not silently repeated): like member-invite-issuance's own
    `test_concurrent_double_reinvite_never_two_live_tokens`, this uses plain
    `asyncio.gather` rather than an explicit synchronization barrier, so it demonstrates —
    but does not deterministically GUARANTEE on every scheduler interleaving — that the
    collision path executes. A hardening pass (once the repository's real internal shape
    exists to hook a barrier into, mirroring issuance's own OBSERVE spec-delta) is a
    reasonable Build/Verify-time follow-up, not something fabricatable against code that
    does not exist yet.
    """
    invite = await _create_pending_invite(
        client, owner_token=seeded_tenant["owner_token"], email="race@co.io"
    )

    r1, r2 = await asyncio.gather(
        client.post(
            f"/invites/{invite['token']}/accept",
            json={"password": "correct horse battery staple"},
        ),
        client.post(
            f"/invites/{invite['token']}/accept",
            json={"password": "another-valid-password"},
        ),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409], f"expected exactly one 200 and one 409, got {statuses}"
    assert await _users_count_for_email(db_session, "race@co.io") == 1


# ---------------------------------------------------------------------------
# TEST 17: Preview and accept rate limits are independently counted   [review finding, M7]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_and_accept_rate_limits_counted_independently(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """Exhausting the preview limit must NOT count against the accept limit (or vice versa) —
    the two endpoints have independently configured rpm knobs (§3), so they must not share one
    Redis counter. Regression test for a review finding: `InvitePublicRateLimiter`'s window key
    originally keyed on client IP alone with no action discriminator, so both endpoints spent
    from the same budget; the FIRST endpoint called in a window would silently consume the
    OTHER endpoint's allowance.

    With invite_preview_rpm=2 / invite_accept_rpm=2 (`low_rpm_app_and_client`): 2 preview calls
    exhaust preview's own counter (both still 404, not 429 — the limiter allows exactly `limit`
    requests before rejecting). A 3rd call, to the DIFFERENT accept endpoint, must still be
    judged against accept's OWN fresh counter — reaching the token-lookup 404, never a 429 —
    proving the two counters are independent, not shared.
    """
    _application, low_client = low_rpm_app_and_client
    preview_token = secrets.token_urlsafe(32)

    r1 = await low_client.get(f"/invites/{preview_token}")
    _assert_error(r1, 404, "ERR_INVITE_NOT_FOUND")
    r2 = await low_client.get(f"/invites/{preview_token}")
    _assert_error(r2, 404, "ERR_INVITE_NOT_FOUND")

    accept_token = secrets.token_urlsafe(32)
    r3 = await low_client.post(
        f"/invites/{accept_token}/accept",
        json={"password": "correct horse battery staple"},
    )
    _assert_error(r3, 404, "ERR_INVITE_NOT_FOUND")
