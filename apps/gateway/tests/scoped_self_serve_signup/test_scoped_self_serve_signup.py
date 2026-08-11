"""Red->green suite for scoped-self-serve-signup (scoped-self-serve-signup TASK.md §3/§4 —
SECURITY, FROZEN @ v1).

One test per §2 SCENARIOS row, plus one extra structural test for the ⚠ M6 timing-mask
flag the orchestrator surfaced at freeze (see test_email_dispatch_never_blocks_the_response
below). Asserts observable behavior only: HTTP status, `code` field, response body
shape/headers, DB row effects (pending_personal_signups / tenants / users counts and
content), captured-email call counts/content — never internal implementation details.

RED at Ground SHA 8daf22c: neither the new `account_type=='personal' AND
public_signup_personal_enabled` branch nor `POST /admin/auth/signup/confirm` exist yet.
Every test below either hits the untouched S1 gate (expected, M2/R1) or a plain FastAPI 404
(wrong body shape) on the new route — DO NOT weaken these tests to pass; that is Build's job.

M14 / "the frozen S1 suite is unaffected" is deliberately NOT re-implemented as a test here
(shelling out to pytest from inside a pytest test has no precedent in this codebase and
would just be a fragile subprocess wrapper around the same file). Instead §4 records the
frozen suite run directly (`pytest tests/signup_routing_authz/`) as evidence, plus a
`git status`/`git diff` check proving the file itself was never touched.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._polling import poll_until

from .conftest import (
    CONFIRM,
    FRESH_EMAIL,
    PASSWORD,
    SIGNUP,
    TAKEN_EMAIL,
    WEAK_PASSWORD,
    FakeEmailSender,
    _pending_count,
    _pending_row,
    _row_counts,
    _seed_free_plan,
    _tenant_row_by_email,
    assert_problem,
    business_body,
    extract_confirm_token,
    personal_body,
)

pytestmark = pytest.mark.asyncio


async def _users_count_for_email(db_session: AsyncSession, email: str) -> int:
    return int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM users WHERE email = :email"), {"email": email}
            )
        ).scalar_one()
    )


# ===========================================================================
# 1. Flag OFF keeps a personal signup byte-identical to the frozen S1 gate   [M2, R1]
# ===========================================================================


async def test_flag_off_personal_signup_byte_identical_to_s1(
    client: httpx.AsyncClient,
    make_second_app_client: Any,
    db_session: AsyncSession,
) -> None:
    """The registered email is seeded via the PRIMARY `client` fixture, whose top-level
    `settings` fixture sets the EXISTING business/global `public_signup_enabled=True`
    (tests/conftest.py) — that flag alone already lets ANY account_type, including
    personal, through the S1 gate today (it predates this task's account_type
    discriminator work), so it is the wrong baseline for THIS scenario. The actual
    assertion runs against a SEPARATE app where BOTH flags are at their Settings default
    False (`public_signup_personal_enabled=False` here; `public_signup_enabled` is never
    passed to `make_second_app_client`, so it falls back to Settings' own False) — an
    already-registered email AND a weak password on the personal path must still hit 403
    ERR_SIGNUP_INVITE_ONLY, never 400/409 — the gate is checked BEFORE any body validation
    or DB IO, byte-identical to test_signup_invite_only_checked_before_validation in the
    frozen S1 suite."""
    registered = await client.post(
        SIGNUP,
        json={"tenant_name": "Existing Co", "email": TAKEN_EMAIL, "password": PASSWORD},
    )
    assert registered.status_code == 201, registered.text
    before = await _row_counts(db_session)

    application, off_client, fake = make_second_app_client(public_signup_personal_enabled=False)
    async with off_client:
        resp = await off_client.post(
            SIGNUP, json=personal_body(email=TAKEN_EMAIL, password=WEAK_PASSWORD)
        )

        assert_problem(resp, 403, "ERR_SIGNUP_INVITE_ONLY")
        after = await _row_counts(db_session)
        assert after == before, (
            f"the gate must create ZERO rows when both flags are OFF: {before} -> {after}"
        )
        assert fake.sent == []
    await application.state.engine.dispose()


# ===========================================================================
# 2. The new flag has zero effect on business signups   [M1, R1]
# ===========================================================================


async def test_new_flag_zero_effect_on_business_signups(
    make_second_app_client: Any, db_session: AsyncSession
) -> None:
    """public_signup_personal_enabled=True, public_signup_enabled left at its Settings
    default (False) — a BUSINESS signup with no domain claim still hits 403
    ERR_SIGNUP_INVITE_ONLY, unchanged from before this task existed. Proves the new flag is
    never read anywhere near the business path."""
    application, scoped_client, fake = make_second_app_client(public_signup_personal_enabled=True)
    async with scoped_client:
        before = await _row_counts(db_session)

        resp = await scoped_client.post(SIGNUP, json=business_body(email="biz-target@acme.io"))

        assert_problem(resp, 403, "ERR_SIGNUP_INVITE_ONLY")
        after = await _row_counts(db_session)
        assert after == before
        assert fake.sent == []
    await application.state.engine.dispose()


# ===========================================================================
# 3. [ATTACKER] Probing a known-registered vs an unknown personal email is
#    indistinguishable   [M7, M9, R-sec-1, R-sec-2]
# ===========================================================================


async def test_probe_registered_vs_unknown_indistinguishable(
    client: httpx.AsyncClient,
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    """taken@acme.io is a real, already-registered user (seeded via a plain business signup
    on the PRIMARY app/client, same DB); fresh@acme.io has never been used. Both personal
    signups, same password, same length email — the two responses must be 202 with the
    IDENTICAL JSON shape/keys, the SAME status/header-key-set/Content-Length, and the
    password hasher's hash() invoked EXACTLY ONCE per request (call-count, not wall-clock —
    the dominant cost is paid identically on both branches, masking the DB-write delta)."""
    seed = await client.post(
        SIGNUP,
        json={"tenant_name": "Taken Co", "email": TAKEN_EMAIL, "password": PASSWORD},
    )
    assert seed.status_code == 201, seed.text

    application, scoped_client, _fake = scoped_app_client
    await _seed_free_plan(db_session)

    real_hasher = application.state.password_hasher
    original_hash = real_hasher.hash
    calls: list[str] = []

    def _counting_hash(password: str) -> str:
        calls.append(password)
        return original_hash(password)

    real_hasher.hash = _counting_hash  # type: ignore[method-assign]
    try:
        resp_taken = await scoped_client.post(SIGNUP, json=personal_body(email=TAKEN_EMAIL))
        resp_fresh = await scoped_client.post(SIGNUP, json=personal_body(email=FRESH_EMAIL))
    finally:
        real_hasher.hash = original_hash  # type: ignore[method-assign]

    assert resp_taken.status_code == 202, resp_taken.text
    assert resp_fresh.status_code == 202, resp_fresh.text

    body_taken, body_fresh = resp_taken.json(), resp_fresh.json()
    assert set(body_taken.keys()) == set(body_fresh.keys()) == {"status", "email"}
    assert body_taken["status"] == body_fresh["status"] == "pending_verification"
    assert body_taken["email"] == TAKEN_EMAIL
    assert body_fresh["email"] == FRESH_EMAIL

    headers_taken = {k.lower() for k in resp_taken.headers.keys()}
    headers_fresh = {k.lower() for k in resp_fresh.headers.keys()}
    assert headers_taken == headers_fresh, (
        f"no header may differ between the two branches: {headers_taken} vs {headers_fresh}"
    )
    assert resp_taken.headers.get("content-length") == resp_fresh.headers.get("content-length"), (
        "same-length emails must yield a byte-identical Content-Length across both branches"
    )

    assert len(calls) == 2, (
        f"Argon2 hash() must be invoked EXACTLY ONCE per request on both branches (M6 "
        f"timing mask), got {len(calls)} call(s): {calls}"
    )


# ===========================================================================
# 3b. STRUCTURAL: neither branch's email dispatch may contribute response
#     latency — closes the ⚠ M6 timing-mask flag surfaced at freeze
# ===========================================================================


class _GatedEmailSender:
    """An EmailSender that blocks inside `send` until the test releases it.

    Replaces this test's original wall-clock budget with a CAUSAL one. The invariant is
    "dispatch is not on the response path", and the original proved it by injecting a 1.5s
    send delay and asserting the response returned within 0.5s. That inequality holds only
    while the request's own cost stays well under the budget — and signup's cost is
    dominated by the deliberate M6 Argon2 timing mask, which is expensive BY DESIGN. Under
    `-n 12` it measured 0.586s against the 0.5s budget: the structural property held (0.586s
    is nowhere near the 1.5s an inline send would have cost) and the test failed anyway.

    Blocking on an Event instead removes wall-clock from the passing path entirely. If
    dispatch is off-path the response returns while `send` is still parked, whatever the
    host is doing; if Build ever inlines `await send_email(...)`, the request cannot return
    at all and the bounded wait below fails it. Strictly stronger than a threshold, and it
    additionally proves the send was actually SCHEDULED — the 0.5s version passed whether or
    not any email was dispatched.
    """

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.started: list[Any] = []
        self._release = asyncio.Event()

    async def send(self, message: Any) -> None:
        self.started.append(message)
        await self._release.wait()
        self.sent.append(message)

    def release(self) -> None:
        self._release.set()


async def test_email_dispatch_never_blocks_the_response(
    client: httpx.AsyncClient,
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    """§3's freeze note flags that the M6 Argon2 timing mask only equalizes the DB-branch
    cost, never reasoning about the inline `await send_email(...)` the closest sibling
    (IssueMemberVerifyCodeUseCase) uses today — a measurable send-LATENCY delta between the
    confirm-email (M8) and conflict-notice (M9) templates would reopen the very oracle M7
    exists to close.

    Comparing branch-A-vs-branch-B wall-clock deltas directly would be flaky (send times
    are naturally close and CI jitter dwarfs the signal). Instead this asserts the
    STRUCTURAL property that makes any such delta irrelevant: email dispatch never sits on
    the response path AT ALL, on EITHER branch. The send is parked on an Event, and the
    response must come back with the send still parked — for both the fresh-email and
    already-registered-email branches. If Build inlines `await send_email(...)` on the
    request path (the member-verify precedent's own shape), the request cannot return until
    the Event is set and this test fails; only scheduling BOTH sends off the request path
    (e.g. asyncio.ensure_future — the shape LoginUseCase.execute already uses for its own
    fire-and-forget superadmin-login audit write) passes.
    """
    seed = await client.post(
        SIGNUP,
        json={"tenant_name": "Slow Mail Co", "email": TAKEN_EMAIL, "password": PASSWORD},
    )
    assert seed.status_code == 201, seed.text

    application, scoped_client, _fake = scoped_app_client
    await _seed_free_plan(db_session)
    gated = _GatedEmailSender()
    application.state.email_sender = gated

    # Only ever reached by an INLINE await, which parks forever on the Event. The passing
    # path never waits, so this bounds a BROKEN implementation rather than a slow host.
    INLINE_DISPATCH_TIMEOUT_SECONDS = 30.0

    try:
        for branch, email in (("fresh-email", FRESH_EMAIL), ("already-registered", TAKEN_EMAIL)):
            try:
                resp = await asyncio.wait_for(
                    scoped_client.post(SIGNUP, json=personal_body(email=email)),
                    timeout=INLINE_DISPATCH_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                pytest.fail(
                    f"{branch} branch never returned while the email send was parked — "
                    "dispatch sits on the request path (M6/M7 timing mask)"
                )
            assert resp.status_code == 202, resp.text
            assert not gated.sent, (
                f"{branch} branch response returned only AFTER the send completed, so "
                "dispatch sat on the request path (M6/M7 timing mask)"
            )

        # Non-vacuity: the responses above prove nothing unless a send was actually
        # scheduled. Both branches must have entered `send` and be parked in it. Polled
        # rather than read directly because an off-path send is scheduled, not awaited —
        # it reaches its first line on a later loop turn.
        async def _started() -> list[Any]:
            return list(gated.started)

        started = await poll_until(_started, lambda s: len(s) >= 2, timeout=10.0)
        assert len(started) == 2, (
            f"expected both branches to schedule exactly one send, saw {len(started)} — "
            "with no send scheduled the assertions above would pass vacuously"
        )
    finally:
        # Never leave a task parked on the Event: teardown would either hang or warn.
        gated.release()


# ===========================================================================
# 4. A fresh email issues a pending signup and exactly one confirm email   [M8]
# ===========================================================================


async def test_fresh_email_issues_pending_signup_and_one_confirm_email(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    before = await _row_counts(db_session)

    resp = await scoped_client.post(SIGNUP, json=personal_body(email=FRESH_EMAIL))

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "pending_verification", "email": FRESH_EMAIL}

    row = await _pending_row(db_session, email=FRESH_EMAIL)
    assert row is not None, "expected a pending_personal_signups row for the fresh email"
    assert row.confirm_token_hash, "the stored token must be hashed, never plaintext"
    now = datetime.now(UTC)
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    assert now + timedelta(hours=23) < expires_at < now + timedelta(hours=25), (
        f"expected a ~24h expiry, got {expires_at} (now={now})"
    )

    assert len(fake.sent) == 1, f"expected exactly ONE confirm email, got {len(fake.sent)}"
    assert fake.sent[0].to == FRESH_EMAIL
    extract_confirm_token(fake.sent[0])  # raises if no token-shaped substring is present

    after = await _row_counts(db_session)
    assert after == before, "zero tenants/users rows may be created by the initial POST"


# ===========================================================================
# 5. An already-registered email creates nothing and notifies out-of-band   [M9]
# ===========================================================================


async def test_already_registered_creates_nothing_and_notifies_out_of_band(
    client: httpx.AsyncClient,
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    seed = await client.post(
        SIGNUP,
        json={"tenant_name": "Owner Co", "email": TAKEN_EMAIL, "password": PASSWORD},
    )
    assert seed.status_code == 201, seed.text
    before_row = await _tenant_row_by_email(db_session, TAKEN_EMAIL)

    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    before = await _row_counts(db_session)

    resp = await scoped_client.post(SIGNUP, json=personal_body(email=TAKEN_EMAIL))

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "pending_verification", "email": TAKEN_EMAIL}

    assert await _pending_count(db_session, email=TAKEN_EMAIL) == 0, (
        "no pending_personal_signups row may be written on the conflict branch"
    )
    after = await _row_counts(db_session)
    assert after == before, "no tenant/user row may be created on the conflict branch"
    after_row = await _tenant_row_by_email(db_session, TAKEN_EMAIL)
    assert tuple(after_row) == tuple(before_row), "the existing tenant/user row must be untouched"

    assert len(fake.sent) == 1, f"expected exactly ONE conflict-notice email, got {len(fake.sent)}"
    notice = fake.sent[0]
    assert notice.to == TAKEN_EMAIL
    haystack = f"{notice.text_body or ''}\n{notice.html_body or ''}"
    assert "/login" in haystack, f"conflict notice must point at /login: {haystack!r}"
    assert not _looks_like_a_confirm_token(haystack), (
        f"conflict notice must carry NO token (no reset link exists to gate): {haystack!r}"
    )


def _looks_like_a_confirm_token(haystack: str) -> bool:
    return re.search(r"[A-Za-z0-9_-]{40,}", haystack) is not None


# ===========================================================================
# 6. Weak password rejected identically regardless of target email   [M4, R2]
# ===========================================================================


async def test_weak_password_rejected_identically_regardless_of_target_email(
    client: httpx.AsyncClient,
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    seed = await client.post(
        SIGNUP,
        json={"tenant_name": "Weak Co", "email": TAKEN_EMAIL, "password": PASSWORD},
    )
    assert seed.status_code == 201, seed.text

    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    before = await _row_counts(db_session)

    resp_fresh = await scoped_client.post(
        SIGNUP, json=personal_body(email=FRESH_EMAIL, password=WEAK_PASSWORD)
    )
    resp_taken = await scoped_client.post(
        SIGNUP, json=personal_body(email=TAKEN_EMAIL, password=WEAK_PASSWORD)
    )

    body_fresh = assert_problem(resp_fresh, 400, "ERR_AUTH_PASSWORD_WEAK")
    body_taken = assert_problem(resp_taken, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert body_fresh == body_taken, "the 400 body must be byte-identical regardless of target email"

    assert await _row_counts(db_session) == before
    assert await _pending_count(db_session) == 0
    assert fake.sent == [], "neither rejected call may dispatch an email"


# ===========================================================================
# 7. Free plan unprovisioned fails loud identically regardless of target email   [M5, R3]
# ===========================================================================


async def test_plan_unprovisioned_fails_loud(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    """Deliberately do NOT seed the free plan (mirrors
    tests/account_type_discriminator/test_account_type_discriminator.py's own
    `test_personal_signup_without_free_plan_fails_loud`)."""
    _application, scoped_client, fake = scoped_app_client

    resp = await scoped_client.post(SIGNUP, json=personal_body(email=FRESH_EMAIL))

    assert_problem(resp, 500, "ERR_SIGNUP_PLAN_UNPROVISIONED")
    assert await _pending_count(db_session) == 0
    assert fake.sent == []


# ===========================================================================
# 8. Rate limit exceeded identically regardless of target email   [M3, R4]
# ===========================================================================


async def test_rate_limit_exceeded_identically_regardless_of_target_email(
    client: httpx.AsyncClient,
    low_rpm_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    registered = await client.post(
        SIGNUP,
        json={"tenant_name": "RL Co", "email": "registered@rl.acme.io", "password": PASSWORD},
    )
    assert registered.status_code == 201, registered.text

    _application, low_client, fake = low_rpm_app_client
    await _seed_free_plan(db_session)

    r1 = await low_client.post(SIGNUP, json=personal_body(email="rl-fresh-1@acme.io"))
    assert r1.status_code == 202, r1.text
    r2 = await low_client.post(SIGNUP, json=personal_body(email="rl-fresh-2@acme.io"))
    assert r2.status_code == 202, r2.text

    # 3rd call, a brand-new target email — trips the per-IP counter (rpm=2); this specific
    # address has never been submitted, so its OWN per-email counter is still fresh.
    r3 = await low_client.post(SIGNUP, json=personal_body(email="rl-fresh-3@acme.io"))
    assert_problem(r3, 429, "ERR_RATE_LIMITED")
    assert "Retry-After" in r3.headers

    # 4th call, the ALREADY-REGISTERED target — same IP, still over budget: an IDENTICAL
    # status/code/header shape, proving the threshold/response never correlates with the
    # target email's registration state.
    r4 = await low_client.post(SIGNUP, json=personal_body(email="registered@rl.acme.io"))
    assert_problem(r4, 429, "ERR_RATE_LIMITED")
    assert "Retry-After" in r4.headers

    assert len(fake.sent) == 2, "only the 2 admitted calls may have dispatched an email"


# ===========================================================================
# 9. Re-submitting for a still-pending email reissues a fresh token   [M8 reissue]
# ===========================================================================


async def test_resubmission_reissues_a_fresh_token(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    email = "waiting@acme.io"

    first = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert first.status_code == 202, first.text
    second = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert second.status_code == 202, second.text

    assert len(fake.sent) == 2, "a re-submission must send a SECOND confirmation email"
    old_token = extract_confirm_token(fake.sent[0])
    new_token = extract_confirm_token(fake.sent[1])
    assert old_token != new_token, "the reissue must mint a genuinely fresh token"

    assert await _pending_count(db_session, email=email) == 1, (
        "reissue must UPSERT — exactly one pending row per email, never two"
    )

    stale = await scoped_client.post(CONFIRM, json={"token": old_token})
    assert_problem(stale, 400, "ERR_SIGNUP_CONFIRM_INVALID")

    fresh = await scoped_client.post(CONFIRM, json={"token": new_token})
    assert fresh.status_code == 201, fresh.text


# ===========================================================================
# 10. Confirming with the correct token creates the tenant   [M10]
# ===========================================================================


async def test_confirm_with_correct_token_creates_the_tenant(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    free_plan_id = await _seed_free_plan(db_session)
    email = "new2@acme.io"
    before = await _row_counts(db_session)

    issue = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert issue.status_code == 202, issue.text
    token = extract_confirm_token(fake.sent[0])

    resp = await scoped_client.post(CONFIRM, json={"token": token})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] and body["user_id"]
    assert body["joined_existing_tenant"] is False

    row = await _tenant_row_by_email(db_session, email)
    assert row.account_type == "personal"
    assert row.plan_id == free_plan_id

    assert await _pending_count(db_session, email=email) == 0, "the pending row must be consumed"
    after = await _row_counts(db_session)
    assert after == (before[0] + 1, before[1] + 1)


# ===========================================================================
# 11. Confirming with an unknown or already-consumed token is rejected   [M11, R5]
# ===========================================================================


async def test_confirm_with_unknown_token_rejected(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    """A token that was NEVER issued."""
    _application, scoped_client, _fake = scoped_app_client
    before = await _row_counts(db_session)

    resp = await scoped_client.post(CONFIRM, json={"token": "never-issued-" + "z" * 43})

    assert_problem(resp, 400, "ERR_SIGNUP_CONFIRM_INVALID")
    assert await _row_counts(db_session) == before


# ===========================================================================
# 12. Replaying an already-consumed token fails closed   [M10 single-use]
# ===========================================================================


async def test_confirm_replay_of_consumed_token_fails_closed(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    email = "onceonly@acme.io"

    issue = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert issue.status_code == 202, issue.text
    token = extract_confirm_token(fake.sent[0])

    first = await scoped_client.post(CONFIRM, json={"token": token})
    assert first.status_code == 201, first.text

    replay = await scoped_client.post(CONFIRM, json={"token": token})

    assert_problem(replay, 400, "ERR_SIGNUP_CONFIRM_INVALID")
    row = await _tenant_row_by_email(db_session, email)
    assert row is not None, "the original tenant/user pair must still exist"
    # exactly one tenant/user pair for this email — no second creation from the replay.
    assert await _users_count_for_email(db_session, email) == 1


# ===========================================================================
# 13. Confirming with an expired token is rejected and cleaned up   [M11, R6]
# ===========================================================================


async def test_confirm_with_expired_token_rejected_and_cleaned_up(
    make_second_app_client: Any,
    db_session: AsyncSession,
) -> None:
    application, short_ttl_client, fake = make_second_app_client(
        public_signup_personal_enabled=True,
        personal_signup_confirm_ttl_seconds=1,
    )
    async with short_ttl_client:
        await _seed_free_plan(db_session)
        email = "expiring@acme.io"

        issue = await short_ttl_client.post(SIGNUP, json=personal_body(email=email))
        assert issue.status_code == 202, issue.text
        token = extract_confirm_token(fake.sent[0])

        # NEGATIVE WAIT: the 1s confirm-TTL must actually LAPSE. Elapsed time is the trigger
        # under test — there is no row or counter to poll for, and shortening the wait would
        # turn the 410 EXPIRED assertion into a race.
        await asyncio.sleep(1.5)

        expired = await short_ttl_client.post(CONFIRM, json={"token": token})
        assert_problem(expired, 410, "ERR_SIGNUP_CONFIRM_EXPIRED")
        assert await _pending_count(db_session, email=email) == 0, "the expired row must be cleaned up"

        # Re-submitting the SAME (now-purged) token afterward -> 400 INVALID, not 410 again —
        # proves the expired row was actually deleted (cleanup), not merely skipped over.
        replay = await short_ttl_client.post(CONFIRM, json={"token": token})
        assert_problem(replay, 400, "ERR_SIGNUP_CONFIRM_INVALID")

        assert await _users_count_for_email(db_session, email) == 0, (
            "an expired-then-replayed token may never create a tenant/user row"
        )
    await application.state.engine.dispose()


# ===========================================================================
# 14. Concurrent confirms with the same token — only one succeeds   [M10 atomicity]
# ===========================================================================


async def test_confirm_concurrent_same_token_only_one_succeeds(
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    email = "racey@acme.io"

    issue = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert issue.status_code == 202, issue.text
    token = extract_confirm_token(fake.sent[0])

    r1, r2 = await asyncio.gather(
        scoped_client.post(CONFIRM, json={"token": token}),
        scoped_client.post(CONFIRM, json={"token": token}),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [201, 400], f"expected exactly one 201 and one 400, got {statuses}"
    invalid_resp = r1 if r1.status_code == 400 else r2
    assert invalid_resp.json().get("code") == "ERR_SIGNUP_CONFIRM_INVALID"

    assert await _users_count_for_email(db_session, email) == 1, (
        "exactly one tenant/user pair may exist, never two"
    )


# ===========================================================================
# 15. Confirm-time race against an independently-created account   [M12, R7]
# ===========================================================================


async def test_confirm_race_against_independently_created_account(
    client: httpx.AsyncClient,
    scoped_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, scoped_client, fake = scoped_app_client
    await _seed_free_plan(db_session)
    email = "sam@acme.io"

    issue = await scoped_client.post(SIGNUP, json=personal_body(email=email))
    assert issue.status_code == 202, issue.text
    token = extract_confirm_token(fake.sent[0])

    # An UNRELATED path (a plain business signup on the primary app) registers the same
    # email between issuance and confirm.
    unrelated = await client.post(
        SIGNUP,
        json={"tenant_name": "Unrelated Biz", "email": email, "password": PASSWORD},
    )
    assert unrelated.status_code == 201, unrelated.text

    resp = await scoped_client.post(CONFIRM, json={"token": token})

    assert_problem(resp, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await _users_count_for_email(db_session, email) == 1, (
        "no SECOND tenant/user row for sam@acme.io may be created"
    )
    assert await _pending_count(db_session, email=email) == 0, (
        "the pending row must be consumed by the atomic DELETE, never left dangling"
    )


# ===========================================================================
# 16. Confirm endpoint rate-limited   [M13, R8]
# ===========================================================================


async def test_confirm_endpoint_rate_limited(
    low_rpm_app_client: tuple[Any, httpx.AsyncClient, FakeEmailSender],
    db_session: AsyncSession,
) -> None:
    _application, low_client, _fake = low_rpm_app_client
    before = await _row_counts(db_session)

    r1 = await low_client.post(CONFIRM, json={"token": "garbage-token-" + "a" * 40})
    assert_problem(r1, 400, "ERR_SIGNUP_CONFIRM_INVALID")
    r2 = await low_client.post(CONFIRM, json={"token": "garbage-token-" + "b" * 40})
    assert_problem(r2, 400, "ERR_SIGNUP_CONFIRM_INVALID")

    r3 = await low_client.post(CONFIRM, json={"token": "garbage-token-" + "c" * 40})
    assert_problem(r3, 429, "ERR_RATE_LIMITED")
    assert "Retry-After" in r3.headers

    assert await _row_counts(db_session) == before, (
        "no tenant/user row may be created regardless of whether the throttled token was valid"
    )
