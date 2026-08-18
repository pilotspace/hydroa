"""Red->green suite for auth-hardening-login-sessions (TASK.md §CHECKS — SECURITY).

One test per §CHECKS row. Asserts observable behavior only: HTTP status, problem `code`,
body/header byte-identity, DB row effects, captured-email call counts — never
implementation internals.

RED at the current tree: the login limiter, /admin/auth/password-reset(+/confirm),
/admin/auth/logout, the jti claim, and the revocation guard do not exist yet. DO NOT
weaken these tests to pass — that is Build's job.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import jwt as pyjwt
import pytest
from sqlalchemy import text

from tests.conftest import TEST_JWT_SECRET

from .conftest import (
    GHOST_EMAIL,
    KNOWN_EMAIL,
    LOGIN,
    LOGOUT,
    ME,
    NEW_PASSWORD,
    PASSWORD,
    RESET,
    RESET_CONFIRM,
    WEAK_PASSWORD,
    BrokenRedis,
    CountingHasher,
    assert_problem,
    bearer,
    extract_reset_token,
    seed_password_user,
)

pytestmark = pytest.mark.asyncio


def _login_body(email: str = KNOWN_EMAIL, password: str = PASSWORD) -> dict[str, str]:
    return {"email": email, "password": password}


async def _login_token(client, email: str = KNOWN_EMAIL, password: str = PASSWORD) -> str:
    resp = await client.post(LOGIN, json=_login_body(email, password))
    assert resp.status_code == 200, f"seed login failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


# ── S1 · login rate limit ──────────────────────────────────────────────────────


async def test_login_rate_limited_per_ip_before_hasher(low_rpm_app, db_session) -> None:
    """covers: M1, A8, E1, R:UNTHROTTLED_LOGIN — the N+1th same-IP attempt 429s with
    Retry-After, and the limited attempt performs ZERO hasher verify calls."""
    application, client, _ = low_rpm_app
    await seed_password_user(db_session, application)
    spy = CountingHasher(application.state.password_hasher)
    application.state.password_hasher = spy

    # Exhaust the per-IP budget (2) with distinct emails so the per-email bucket stays cold.
    r1 = await client.post(LOGIN, json=_login_body("a1@hardn.io", "wrong-password-xx"))
    r2 = await client.post(LOGIN, json=_login_body("a2@hardn.io", "wrong-password-xx"))
    assert r1.status_code != 429 and r2.status_code != 429
    calls_before = spy.verify_calls

    limited = await client.post(LOGIN, json=_login_body("a3@hardn.io", "wrong-password-xx"))
    assert_problem(limited, 429, "ERR_RATE_LIMITED")
    assert limited.headers.get("Retry-After") is not None
    assert spy.verify_calls == calls_before, "a limited attempt must never reach the hasher"


async def test_login_per_email_cap_is_uniform(low_rpm_app, db_session) -> None:
    """covers: M1, M2, A4, E2, R:ENUMERATION_ORACLE, R:LOCKOUT_DOS — at the per-email threshold the
    registered and unknown email get byte-identical 429s."""
    application, client, _ = low_rpm_app
    await seed_password_user(db_session, application)

    async def exhaust_then_capture(email: str):
        # Per-email budget is 2; use a fresh XFF-independent path: the per-IP budget (2)
        # would trip first, so spread attempts across the email only — the limiter must
        # key these on email. Two misses, then the third is the boundary probe.
        last = None
        for _ in range(3):
            last = await client.post(LOGIN, json=_login_body(email, "wrong-password-xx"))
        return last

    known = await exhaust_then_capture(KNOWN_EMAIL)
    ghost = await exhaust_then_capture(GHOST_EMAIL)
    assert known.status_code == 429 and ghost.status_code == 429
    assert known.content == ghost.content, "429 bodies must be byte-identical (no oracle)"


async def test_login_limiter_redis_down_fails_open(low_rpm_app, db_session) -> None:
    """covers: E3 — limiter backend raising RedisError → login proceeds to the credential
    check and succeeds with valid credentials (documented fail-open house rule).

    Two phases so this is RED pre-Build (not vacuously green from the limiter simply not
    existing): phase 1 proves the limiter LIMITS with a healthy backend; phase 2 breaks
    the backend and proves the same endpoint fails open."""
    application, client, _ = low_rpm_app
    await seed_password_user(db_session, application)

    for i in range(2):
        await client.post(LOGIN, json=_login_body(f"c{i}@hardn.io", "wrong-password-xx"))
    limited = await client.post(LOGIN, json=_login_body("c9@hardn.io", "wrong-password-xx"))
    assert_problem(limited, 429, "ERR_RATE_LIMITED")  # the limiter exists and limits

    application.state.invite_public_limiter._redis = BrokenRedis()  # the documented seam
    resp = await client.post(LOGIN, json=_login_body())
    assert resp.status_code == 200, f"fail-open violated: {resp.status_code} {resp.text}"


async def test_login_limiter_keys_use_trusted_ip(low_rpm_app, db_session) -> None:
    """covers: M7, R:RAW_CLIENT_IP — an attacker-PREPENDED X-Forwarded-For token must NOT
    escape the per-IP bucket: with trusted_proxy_hops at its real default (1 — a 0 is
    coerced to 1 by config, the resolver trusts ONLY the rightmost, proxy-appended hop),
    varying the left (client-controlled) token while the trusted right hop stays fixed
    must keep hitting the SAME bucket. A naive raw-XFF (leftmost-token) implementation
    reads three different IPs here and never 429s — this check stays red against it.

    [CHECK-REPAIR disclosed at gate: originally authored against a false premise
    ("default (0)" — config.py coerces <=0 to 1, frozen by the trusted-proxy-hops task);
    repaired to test the SAME R:RAW_CLIENT_IP rule under the repo's real resolver
    semantics. Assertion strength unchanged: 429 on the third same-bucket attempt.]"""
    application, client, _ = low_rpm_app
    await seed_password_user(db_session, application)

    for i in range(2):
        await client.post(
            LOGIN,
            json=_login_body(f"b{i}@hardn.io", "wrong-password-xx"),
            headers={"X-Forwarded-For": f"10.9.{i}.{i}, 10.0.0.7"},
        )
    spoofed = await client.post(
        LOGIN,
        json=_login_body("b9@hardn.io", "wrong-password-xx"),
        headers={"X-Forwarded-For": "203.0.113.99, 10.0.0.7"},
    )
    assert_problem(spoofed, 429, "ERR_RATE_LIMITED")


# ── S2 · password reset ────────────────────────────────────────────────────────


async def test_reset_request_uniform_202_no_oracle(hardening_app, db_session) -> None:
    """covers: M2, A6, E4, R:ENUMERATION_ORACLE — unknown vs registered email get
    byte-identical 202s; an email is dispatched ONLY for the registered one."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)

    ghost = await client.post(RESET, json={"email": GHOST_EMAIL})
    sent_after_ghost = len(fake.sent)
    known = await client.post(RESET, json={"email": KNOWN_EMAIL})

    assert ghost.status_code == 202 and known.status_code == 202
    assert ghost.content == known.content, "202 bodies must be byte-identical (no oracle)"
    assert sent_after_ghost == 0, "unknown email must dispatch nothing"
    await asyncio.sleep(0)  # allow fire-and-forget dispatch to land
    assert len(fake.sent) == 1, "registered email must dispatch exactly one reset email"


async def test_reset_token_hashed_and_single_use(hardening_app, db_session) -> None:
    """covers: M3, E5, R:PLAINTEXT_RESET_TOKEN — the stored row holds a hash (never the
    raw token); a second confirm with the same token is refused."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)
    assert (await client.post(RESET, json={"email": KNOWN_EMAIL})).status_code == 202
    await asyncio.sleep(0)
    token = extract_reset_token(fake.sent[0])

    stored = (
        await db_session.execute(text("SELECT token_hash FROM password_reset_tokens"))
    ).scalars().all()
    assert stored, "a reset request for a registered email must persist a token row"
    assert token not in stored, "the raw token must never be stored at rest"

    first = await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    assert first.status_code == 200, first.text
    second = await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    assert_problem(second, 400, "ERR_AUTH_RESET_INVALID")


async def test_reset_token_expiry_boundary_refused(hardening_app, db_session) -> None:
    """covers: A5, E6 — a token at/after expires_at is AUTH_RESET_EXPIRED (fail closed at
    the boundary instant)."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)
    assert (await client.post(RESET, json={"email": KNOWN_EMAIL})).status_code == 202
    await asyncio.sleep(0)
    token = extract_reset_token(fake.sent[0])

    await db_session.execute(text("UPDATE password_reset_tokens SET expires_at = now()"))
    await db_session.commit()
    resp = await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    assert_problem(resp, 400, "ERR_AUTH_RESET_EXPIRED")


async def test_reset_weak_password_keeps_token_live(hardening_app, db_session) -> None:
    """covers: A16, E7 — a weak new_password is refused WITHOUT consuming the token; the same
    token then succeeds with a strong password."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)
    assert (await client.post(RESET, json={"email": KNOWN_EMAIL})).status_code == 202
    await asyncio.sleep(0)
    token = extract_reset_token(fake.sent[0])

    weak = await client.post(RESET_CONFIRM, json={"token": token, "new_password": WEAK_PASSWORD})
    assert_problem(weak, 400, "ERR_AUTH_PASSWORD_WEAK")
    strong = await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    assert strong.status_code == 200, "the token must survive a weak-password attempt"


async def test_reset_confirm_revokes_prior_sessions(hardening_app, db_session) -> None:
    """covers: M4, E8 — a session minted BEFORE the reset is refused after it; the new
    password logs in (and the old one no longer does)."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)
    old_session = await _login_token(client)
    assert (await client.get(ME, headers=bearer(old_session))).status_code == 200

    assert (await client.post(RESET, json={"email": KNOWN_EMAIL})).status_code == 202
    await asyncio.sleep(0)
    token = extract_reset_token(fake.sent[0])
    assert (
        await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    ).status_code == 200

    stale = await client.get(ME, headers=bearer(old_session))
    assert stale.status_code == 401, "a pre-reset session must not survive the reset"
    assert (await client.post(LOGIN, json=_login_body(password=NEW_PASSWORD))).status_code == 200
    assert (await client.post(LOGIN, json=_login_body(password=PASSWORD))).status_code == 401


# ── S3 · session revocation ────────────────────────────────────────────────────


async def test_logout_is_server_side_revocation(hardening_app, db_session) -> None:
    """covers: M5, A2, A14, E9, R:STATELESS_REVOCATION_THEATER — after logout the SAME bearer
    is refused server-side; a second live session of the same user is untouched."""
    application, client, _ = hardening_app
    await seed_password_user(db_session, application)
    t1 = await _login_token(client)
    t2 = await _login_token(client)

    resp = await client.post(LOGOUT, headers=bearer(t1))
    assert resp.status_code == 204, f"logout must exist and revoke: {resp.status_code}"
    assert (await client.get(ME, headers=bearer(t1))).status_code == 401
    assert (await client.get(ME, headers=bearer(t2))).status_code == 200


async def test_legacy_token_without_jti_still_authenticates(hardening_app, db_session) -> None:
    """covers: A7, E10, R:WEAKENED_SIBLING — a hand-minted legacy-shape token (no jti)
    still authenticates, and new tokens DO carry a jti (decode required-set unchanged)."""
    application, client, _ = hardening_app
    _, user_id = await seed_password_user(db_session, application)

    now = int(time.time())
    tenant_id = (
        await db_session.execute(text("SELECT tenant_id FROM users WHERE id = :i"), {"i": user_id})
    ).scalar_one()
    legacy = pyjwt.encode(
        {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": "owner",
            "email": KNOWN_EMAIL,
            "iat": now,
            "exp": now + 600,
            "iss": application.state.settings.jwt_issuer,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    assert (await client.get(ME, headers=bearer(legacy))).status_code == 200

    fresh = await _login_token(client)
    claims = pyjwt.decode(fresh, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False}, issuer=application.state.settings.jwt_issuer)
    assert "jti" in claims, "newly issued session JWTs must carry a jti"


async def test_revoked_token_refused_on_data_route_too(hardening_app, db_session) -> None:
    """covers: A12 — revocation is enforced at the shared identity seam: the revoked jti
    is refused on a representative non-/me admin route as well."""
    application, client, _ = hardening_app
    await seed_password_user(db_session, application)
    t1 = await _login_token(client)
    assert (await client.post(LOGOUT, headers=bearer(t1))).status_code == 204

    resp = await client.get("/admin/keys", headers=bearer(t1))
    assert resp.status_code == 401, f"seam bypass: /admin/keys answered {resp.status_code}"


async def test_revocation_store_outage_fails_closed_bounded(
    hardening_app, db_session, monkeypatch
) -> None:
    """covers: M6, E11 — a revocation store that raises → a typed 503-class refusal
    (AUTH_UNAVAILABLE) within the configured bound; never a silent allow, never a hang."""
    application, client, _ = hardening_app
    await seed_password_user(db_session, application)
    t1 = await _login_token(client)

    # Contract seam (PLAN): the revocation guard module. ImportError pre-Build = red.
    from gateway.tenants.infrastructure import session_revocation

    async def _broken(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("revocation store down (test double)")

    monkeypatch.setattr(session_revocation.DbSessionRevocationGuard, "is_revoked", _broken)
    started = time.monotonic()
    resp = await client.get(ME, headers=bearer(t1))
    elapsed = time.monotonic() - started

    assert resp.status_code == 503, "store outage must fail CLOSED, never silently allow"
    assert resp.json().get("code") == "ERR_AUTH_UNAVAILABLE"
    assert elapsed < 10.0, "the outage refusal must be bounded, not a hang"


async def test_reset_and_logout_audited_without_secrets(hardening_app, db_session) -> None:
    """covers: M6, E12 — reset-confirm and logout each emit an audit event whose metadata
    contains neither the reset token nor any password substring."""
    application, client, fake = hardening_app
    await seed_password_user(db_session, application)
    t1 = await _login_token(client)
    assert (await client.post(RESET, json={"email": KNOWN_EMAIL})).status_code == 202
    await asyncio.sleep(0)
    token = extract_reset_token(fake.sent[0])
    assert (
        await client.post(RESET_CONFIRM, json={"token": token, "new_password": NEW_PASSWORD})
    ).status_code == 200
    assert (await client.post(LOGOUT, headers=bearer(t1))).status_code == 204

    rows = (
        await db_session.execute(
            text(
                "SELECT action, metadata::text FROM audit_events "
                "WHERE action IN ('auth.password_reset', 'auth.logout')"
            )
        )
    ).all()
    actions = {r[0] for r in rows}
    assert actions == {"auth.password_reset", "auth.logout"}, f"missing audit rows: {actions}"
    for _, meta in rows:
        assert token not in meta and NEW_PASSWORD not in meta and PASSWORD not in meta
