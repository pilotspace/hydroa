"""RED->GREEN suite for impersonation-session-lifecycle (TASK.md §3 CONTRACT — FROZEN @ v1).

Covers the dual-identity JWT shape + revocable session-store record:
  - `ImpersonationContext` + additive `Identity.impersonation` field (M1)
  - `JwtTokenService.issue()`/`.decode()` additive kwargs/claim, ordinary-token
    byte-identical guarantee (M2, M3) + N3 (TypeError added to decode()'s except tuple)
  - NEW `impersonation_sessions` table (M4)
  - POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate — Mint (M5-M7)
  - DELETE /admin/platform/impersonation/sessions/{session_id} — End (M8), including the
    concurrency-safe conditional-UPDATE race (dedicated test, contract §3 Part D IO note)
  - Mint's own INSERT-race backstop (dedicated test, same IO note, Mint half) — the partial
    unique index + IntegrityError->409 translation, not merely the ordinary precondition read
  - Containment: an impersonation identity can never reach /admin/platform/* (incl. nested Mint)
  - Actor-attribution invariant on both audit rows (platform.impersonation.start/.end)

RED before BUILD: `platform_impersonation_router.py` does not exist and is not registered in
main.py yet -> every HTTP-level Mint/End test fails with a bare 404 (route not found).
`Identity` has no `impersonation` attribute yet -> AttributeError in the dual-identity/ordinary-
token tests. `JwtTokenService.issue()`/`.decode()` do not accept/produce an `impersonation`
kwarg/claim yet -> TypeError (unexpected keyword argument) in the two direct JWT-layer unit
tests. The `impersonation_sessions` table does not exist yet -> raw-SQL seed/assert helpers
raise asyncpg.UndefinedTableError/ProgrammingError. All of these are missing-implementation
RED reasons, never a fixture/harness bug.

Fixture/helper provenance (this repo's convention is suite-local fixtures, not cross-suite
imports):
  - `platform_tenant_id`/`_issue_token` mirror tests/platform_tenant_directory and
    tests/admin_console_audit's own fixtures of the same name/shape.
  - `_seed_customer_tenant`/`_seed_user`/`_bearer` mirror tests/admin_console_audit's own
    helpers (in turn mirroring tests/cross_tenant_keys_members).
  - `fetch_one_audit_row`/`count_audit_rows`/`_drain_fire_and_forget` mirror
    tests/admin_console_audit/test_admin_console_audit.py verbatim (which mirrors
    tests/test_users_role.py's own `asyncio.sleep(0.05)` idiom).
  - The concurrent-double-End race test mirrors tests/member_invite_issuance's own
    `asyncio.gather(client.post(...), client.post(...))` partial-unique-index race idiom.

IMPORTANT — real FK, not the sibling "token-only" convenience: unlike `audit_events.
actor_user_id` (deliberately FK-less so system/audited actors need no backing row),
`impersonation_sessions.actor_user_id`/`actor_tenant_id`/`target_user_id`/`target_tenant_id`
are ALL real ForeignKeys (contract §3 Part D) — so, unlike several sibling platform-admin
suites, every superadmin CALLER fixture here seeds a REAL `users` row (role='superadmin'
under the platform tenant) and mints its token against that same user_id. Skipping this would
make every Mint-success-path test fail on an IntegrityError, not the target behavior.

DO NOT change these tests to make them pass — that is the Build phase's job.

Coverage target: >=90% of new/modified lines (jwt_service.py additions, the new router,
ImpersonationSessionRow, config validator) — one test per §2 scenario + two dedicated
concurrency tests (End's conditional-UPDATE race + Mint's INSERT race, both named by contract
§3 Part D's IO note) + two direct JWT-layer unit tests for the N3/defense-in-depth guards that
no HTTP scenario can reach (a forged malformed claim / a forged SUPERADMIN+impersonation combo).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.tenants.domain.entities import Role

PLATFORM_TENANTS = "/admin/platform/tenants"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _mint_url(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate"


def _end_url(session_id: object) -> str:
    return f"/admin/platform/impersonation/sessions/{session_id}"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures + helpers (app/client/db_session/settings from the root conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors every sibling
    platform-admin suite's own fixture of the same name)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


async def _seed_customer_tenant(db_session: AsyncSession, *, name: str) -> uuid.UUID:
    """Insert a kind='customer' tenant row (mirrors sibling suites' own helper)."""
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await db_session.commit()
    return tid


async def _seed_user(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str = "member",
) -> uuid.UUID:
    """Insert a users row directly (mirrors tests/admin_console_audit's own helper).

    The fast create_all test schema does NOT install the DB trigger that (in production,
    via migration 5b34ca5e1c4b) restricts role='superadmin' to the platform tenant — so this
    helper can be, and is deliberately, used to seed a role='superadmin' row under a
    NON-platform tenant for the M6b defense-in-depth scenario (Scenario 5).
    """
    uid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role)
            """
        ),
        {"id": uid, "tid": tenant_id, "email": email, "role": role},
    )
    await db_session.commit()
    return uid


async def _seed_impersonation_session(
    db_session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    actor_tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    target_tenant_id: uuid.UUID,
    target_role: str,
    target_email: str,
    expires_at: datetime,
    actor_email: str = "root@platform.internal",
    revoked_at: datetime | None = None,
    revoked_reason: str | None = None,
) -> uuid.UUID:
    """Directly seed an impersonation_sessions row — used ONLY for states Mint itself never
    produces fresh (an already-expired or already-ended row), mirroring the codebase-wide
    "seed the impossible-via-the-API state directly via SQL" convention."""
    sid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO impersonation_sessions
                (id, actor_user_id, actor_tenant_id, actor_email, target_user_id,
                 target_tenant_id, target_role, target_email, expires_at, revoked_at,
                 revoked_reason)
            VALUES
                (:id, :actor_user_id, :actor_tenant_id, :actor_email, :target_user_id,
                 :target_tenant_id, :target_role, :target_email, :expires_at, :revoked_at,
                 :revoked_reason)
            """
        ),
        {
            "id": sid,
            "actor_user_id": actor_user_id,
            "actor_tenant_id": actor_tenant_id,
            "actor_email": actor_email,
            "target_user_id": target_user_id,
            "target_tenant_id": target_tenant_id,
            "target_role": target_role,
            "target_email": target_email,
            "expires_at": expires_at,
            "revoked_at": revoked_at,
            "revoked_reason": revoked_reason,
        },
    )
    await db_session.commit()
    return sid


def _issue_token(
    app: Any, *, role: Any, tenant_id: uuid.UUID, email: str, user_id: uuid.UUID
) -> str:
    """Mint a Bearer token directly via the live token service."""
    token, _ = app.state.token_service.issue(
        user_id=user_id, tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_actor(
    db_session: AsyncSession, platform_tenant_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """A REAL superadmin UserRow — impersonation_sessions.actor_user_id has an enforced FK
    to users.id (contract §3 Part D), unlike audit_events' deliberately FK-less actor_user_id,
    so the "token-only, no DB row" convenience convention used by several sibling
    platform-admin suites does not carry over here for any test reaching Mint's INSERT."""
    email = "root@platform.internal"
    uid = await _seed_user(db_session, tenant_id=platform_tenant_id, email=email, role="superadmin")
    return uid, email


@pytest.fixture
async def superadmin_token(
    app: Any, platform_tenant_id: uuid.UUID, superadmin_actor: tuple[uuid.UUID, str]
) -> str:
    uid, email = superadmin_actor
    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email=email, user_id=uid
    )


@pytest.fixture
def superadmin_user_id(superadmin_actor: tuple[uuid.UUID, str]) -> uuid.UUID:
    return superadmin_actor[0]


async def _mint_second_superadmin(
    app: Any, db_session: AsyncSession, *, platform_tenant_id: uuid.UUID, email: str
) -> tuple[uuid.UUID, str]:
    """A second, independent, REAL superadmin (own UserRow + token) — for the "two
    superadmins" and "ended by a different superadmin" scenarios."""
    uid = await _seed_user(db_session, tenant_id=platform_tenant_id, email=email, role="superadmin")
    token = _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email=email, user_id=uid
    )
    return uid, token


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete before
    querying audit_events (mirrors tests/admin_console_audit's own idiom)."""
    await asyncio.sleep(0.05)


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action` (mirrors
    tests/admin_console_audit/test_admin_console_audit.py's own helper)."""
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "
            "target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def _await_audit_row(
    session: AsyncSession,
    *,
    action: str,
    timeout: float = 3.0,  # noqa: ASYNC109 -- bounded poll loop, not a cancel scope
    interval: float = 0.02,
) -> Row[Any] | None:
    """Poll until `fetch_one_audit_row(action)` returns a row, or `timeout` elapses.

    De-flakes the fire-and-forget audit write under `pytest -n 12` CPU saturation — a
    fixed `_drain_fire_and_forget()` sleep can read before the scheduled
    asyncio.ensure_future(record_audit(...)) task has run. Positive-assertion sites only
    (mirrors tests/superadmin_audit_foundation/conftest.py::await_audit_count).
    """
    row = await fetch_one_audit_row(session, action=action)
    deadline = time.monotonic() + timeout
    while row is None and time.monotonic() < deadline:
        await asyncio.sleep(interval)
        row = await fetch_one_audit_row(session, action=action)
    return row


async def _session_count(db_session: AsyncSession, *, active_only: bool = False) -> int:
    query = "SELECT COUNT(*) FROM impersonation_sessions"
    if active_only:
        query += " WHERE revoked_at IS NULL"
    return int((await db_session.execute(text(query))).scalar() or 0)


async def _fetch_session_row(db_session: AsyncSession, session_id: object) -> Row[Any] | None:
    return (
        await db_session.execute(
            text(
                "SELECT actor_user_id, target_user_id, target_role, revoked_at, "
                "revoked_reason, expires_at FROM impersonation_sessions WHERE id = :id"
            ),
            {"id": session_id},
        )
    ).fetchone()


# ===========================================================================
# Scenario 1 — mint for an eligible target (M1, M5)
# ===========================================================================


async def test_mint_impersonation_session_for_eligible_target(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="MintEligibleCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@x.test", role="admin")

    resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body["token"], str) and body["token"]
    assert body["expires_in"] == app.state.settings.impersonation_session_ttl_seconds
    assert uuid.UUID(body["session_id"])
    assert body["target"] == {
        "user_id": str(uid),
        "tenant_id": str(tid),
        "email": "u@x.test",
        "role": "admin",
    }

    row = await _fetch_session_row(db_session, body["session_id"])
    assert row is not None
    actor_user_id, target_user_id, target_role, revoked_at, _revoked_reason, _expires_at = row
    assert actor_user_id == superadmin_user_id
    assert target_user_id == uid
    assert target_role == "admin"
    assert revoked_at is None


# ===========================================================================
# Scenario 2 — minted JWT's primary claims are the target's, never the superadmin's (M1)
# ===========================================================================


async def test_minted_jwt_primary_claims_are_targets_never_superadmins(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
    platform_tenant_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="DualIdentityCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="target@dualidentity.io", role="admin")

    resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert resp.status_code == 201, resp.text
    body = resp.json()

    identity = app.state.token_service.decode(body["token"])
    assert identity.tenant_id == tid
    assert identity.role.value == "admin"
    assert identity.user_id == uid
    assert identity.email == "target@dualidentity.io"
    # NEVER the superadmin's own values.
    assert identity.tenant_id != platform_tenant_id
    assert identity.role.value != "superadmin"
    assert identity.user_id != superadmin_user_id

    assert identity.impersonation is not None
    assert identity.impersonation.actor_user_id == superadmin_user_id
    assert identity.impersonation.actor_tenant_id == platform_tenant_id
    assert str(identity.impersonation.session_id) == body["session_id"]


# ===========================================================================
# Scenario 3 — an ordinary token is issued/decoded byte-identically (M2, M3)
# ===========================================================================


async def test_ordinary_token_issued_and_decoded_byte_identically(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "OrdinaryTokenCo",
            "email": "owner@ordinarytoken.io",
            "password": "ordinary-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": "owner@ordinarytoken.io", "password": "ordinary-horse-battery-01"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    # decode() returns impersonation=None for an ordinary token.
    identity = app.state.token_service.decode(token)
    assert identity.impersonation is None
    assert identity.role.value == "owner"

    # Byte-identical claims dict: no "impersonation" key at all (not merely null).
    settings: Settings = app.state.settings
    raw_claims = pyjwt.decode(
        token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer
    )
    assert "impersonation" not in raw_claims
    # SANCTIONED EDIT (auth-hardening-login-sessions §3 M5, migration-free additive claim,
    # 2026-08-18): every newly issued session token now carries a revocable "jti". This
    # sibling check's intent — no "impersonation" key on an ordinary token — is unchanged
    # and still asserted above; only the exact-set enumeration gains the new claim.
    assert set(raw_claims.keys()) == {
        "sub", "tenant_id", "role", "email", "iat", "exp", "iss", "jti",
    }


# ===========================================================================
# Scenario 4 — Mint rejects the platform tenant as a target (M6, R4)
# ===========================================================================


async def test_mint_rejects_platform_tenant_target(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    platform_tenant_id: uuid.UUID,
) -> None:
    resp = await client.post(
        _mint_url(platform_tenant_id, uuid.uuid4()), headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_IMPERSONATION_TARGET_INVALID"
    assert await _session_count(db_session) == 0


# ===========================================================================
# Scenario 5 — Mint rejects a fixture-provisioned superadmin-role target (M6, R6)
# ===========================================================================


async def test_mint_rejects_target_user_role_superadmin_defense_in_depth(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="DefenseInDepthCo")
    # Bypasses the (fast-schema-absent) DB trigger purely to exercise the APPLICATION-level
    # guard (M6b) in isolation, per the contract's own scenario text.
    uid = await _seed_user(
        db_session, tenant_id=tid, email="fake-superadmin@ddco.io", role="superadmin"
    )

    resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_IMPERSONATION_TARGET_INVALID"
    assert await _session_count(db_session) == 0


# ===========================================================================
# Scenario 6 — Mint rejects an unknown target tenant_id (R3)
# ===========================================================================


async def test_mint_rejects_unknown_target_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    resp = await client.post(
        _mint_url(uuid.uuid4(), uuid.uuid4()), headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"
    assert await _session_count(db_session) == 0


# ===========================================================================
# Scenario 7 — Mint rejects an unknown target user_id within a real tenant (R5)
# ===========================================================================


async def test_mint_rejects_unknown_target_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="UnknownUserCo")
    resp = await client.post(_mint_url(tid, uuid.uuid4()), headers=_bearer(superadmin_token))
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_USER_NOT_FOUND"
    assert await _session_count(db_session) == 0


# ===========================================================================
# Scenario 8 — a non-superadmin caller is rejected regardless of their own permissions (R2)
# ===========================================================================


async def test_mint_rejects_non_superadmin_caller(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    owner_tid = await _seed_customer_tenant(db_session, name="OwnerCallerCo")
    owner_uid = await _seed_user(
        db_session, tenant_id=owner_tid, email="owner@ownercaller.io", role="owner"
    )
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tid, email="owner@ownercaller.io", user_id=owner_uid
    )

    other_tid = await _seed_customer_tenant(db_session, name="OtherTargetCo")
    other_uid = await _seed_user(db_session, tenant_id=other_tid, email="u@othertarget.io")

    resp = await client.post(_mint_url(other_tid, other_uid), headers=_bearer(owner_token))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"
    assert await _session_count(db_session) == 0


# ===========================================================================
# Scenario 9 — Mint rejects a second session while one is active (M7, R7)
# ===========================================================================


async def test_mint_rejects_second_session_while_one_active(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    t1 = await _seed_customer_tenant(db_session, name="FirstTargetCo")
    u1 = await _seed_user(db_session, tenant_id=t1, email="u1@firsttarget.io", role="admin")
    first = await client.post(_mint_url(t1, u1), headers=_bearer(superadmin_token))
    assert first.status_code == 201, first.text
    first_session_id = first.json()["session_id"]

    t2 = await _seed_customer_tenant(db_session, name="SecondTargetCo")
    u2 = await _seed_user(db_session, tenant_id=t2, email="u2@secondtarget.io")
    second = await client.post(_mint_url(t2, u2), headers=_bearer(superadmin_token))
    assert second.status_code == 409, second.text
    assert second.json().get("code") == "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE"

    row = await _fetch_session_row(db_session, first_session_id)
    assert row is not None
    assert row.revoked_at is None
    assert await _session_count(db_session) == 1


# ===========================================================================
# Scenario 10 — a naturally expired prior session does not block a new Mint (M7 lazy-expire)
# ===========================================================================


async def test_mint_allows_new_session_after_prior_naturally_expired(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
    platform_tenant_id: uuid.UUID,
) -> None:
    old_tid = await _seed_customer_tenant(db_session, name="StaleTargetCo")
    old_uid = await _seed_user(db_session, tenant_id=old_tid, email="stale@staletarget.io")
    stale_session_id = await _seed_impersonation_session(
        db_session,
        actor_user_id=superadmin_user_id,
        actor_tenant_id=platform_tenant_id,
        target_user_id=old_uid,
        target_tenant_id=old_tid,
        target_role="member",
        target_email="stale@staletarget.io",
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    new_tid = await _seed_customer_tenant(db_session, name="FreshTargetCo")
    new_uid = await _seed_user(
        db_session, tenant_id=new_tid, email="fresh@freshtarget.io", role="admin"
    )
    resp = await client.post(_mint_url(new_tid, new_uid), headers=_bearer(superadmin_token))
    assert resp.status_code == 201, resp.text
    assert resp.json()["session_id"] != str(stale_session_id)

    stale_row = await _fetch_session_row(db_session, stale_session_id)
    assert stale_row is not None
    assert stale_row.revoked_at is not None
    assert stale_row.revoked_reason == "expired_lazy_cleanup"
    assert await _session_count(db_session) == 2
    assert await _session_count(db_session, active_only=True) == 1


# ===========================================================================
# Scenario 11 — two different superadmins each hold independent active sessions (M7 boundary)
# ===========================================================================


async def test_two_superadmins_each_hold_independent_active_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    platform_tenant_id: uuid.UUID,
) -> None:
    t1 = await _seed_customer_tenant(db_session, name="S1TargetCo")
    u1 = await _seed_user(db_session, tenant_id=t1, email="u1@s1target.io", role="admin")
    first = await client.post(_mint_url(t1, u1), headers=_bearer(superadmin_token))
    assert first.status_code == 201, first.text

    _second_uid, second_token = await _mint_second_superadmin(
        app, db_session, platform_tenant_id=platform_tenant_id, email="root2@platform.internal"
    )

    t2 = await _seed_customer_tenant(db_session, name="S2TargetCo")
    u2 = await _seed_user(db_session, tenant_id=t2, email="u2@s2target.io")
    second = await client.post(_mint_url(t2, u2), headers=_bearer(second_token))
    assert second.status_code == 201, second.text

    assert await _session_count(db_session, active_only=True) == 2


# ===========================================================================
# Scenario 12 — a superadmin ends their own active session (M8)
# ===========================================================================


async def test_end_own_active_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="EndOwnCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@endown.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]

    end_resp = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert end_resp.status_code == 204, end_resp.text
    assert end_resp.content == b""

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is not None
    assert row.revoked_reason == "explicit_end"


# ===========================================================================
# Scenario 13 — a different superadmin may end another superadmin's session (M8 decision)
# ===========================================================================


async def test_end_by_different_superadmin(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    platform_tenant_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="EndByOtherCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@endbyother.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]

    _second_uid, second_token = await _mint_second_superadmin(
        app, db_session, platform_tenant_id=platform_tenant_id, email="root3@platform.internal"
    )

    end_resp = await client.delete(_end_url(session_id), headers=_bearer(second_token))
    assert end_resp.status_code == 204, end_resp.text

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is not None
    assert row.revoked_reason == "explicit_end"

    end_row = await _await_audit_row(db_session, action="platform.impersonation.end")
    assert end_row is not None
    metadata = end_row[7]
    assert metadata["ended_by_original_actor"] is False


# ===========================================================================
# Scenario 14 — End rejects an unknown session_id (R8)
# ===========================================================================


async def test_end_rejects_unknown_session_id(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    resp = await client.delete(_end_url(uuid.uuid4()), headers=_bearer(superadmin_token))
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_IMPERSONATION_SESSION_NOT_FOUND"


# ===========================================================================
# Scenario 15 — End rejects an already-ended session_id (double-End) (R9)
# ===========================================================================


async def test_end_rejects_already_ended_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="DoubleEndCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@doubleend.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]

    first_end = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert first_end.status_code == 204, first_end.text
    row_after_first = await _fetch_session_row(db_session, session_id)

    second_end = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert second_end.status_code == 409, second_end.text
    assert second_end.json().get("code") == "ERR_IMPERSONATION_SESSION_ALREADY_ENDED"

    row_after_second = await _fetch_session_row(db_session, session_id)
    assert row_after_second == row_after_first, "the row must be unchanged by the rejected 2nd End"


# ===========================================================================
# Scenario 16 — End rejects a naturally expired, never-ended session_id (R9 TTL variant)
# ===========================================================================


async def test_end_rejects_naturally_expired_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
    platform_tenant_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="ExpiredEndCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@expiredend.io", role="admin")
    session_id = await _seed_impersonation_session(
        db_session,
        actor_user_id=superadmin_user_id,
        actor_tenant_id=platform_tenant_id,
        target_user_id=uid,
        target_tenant_id=tid,
        target_role="admin",
        target_email="u@expiredend.io",
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    resp = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "ERR_IMPERSONATION_SESSION_ALREADY_ENDED"

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is None, (
        "End must NOT itself perform the lazy-expire write (M7 is Mint-only)"
    )


# ===========================================================================
# Scenario 17 — an impersonation identity can never reach /admin/platform/* (Containment)
# ===========================================================================


async def test_impersonation_identity_cannot_reach_platform_routes(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="ContainmentCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@containment.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    impersonation_token = mint_resp.json()["token"]

    resp = await client.get(PLATFORM_TENANTS, headers=_bearer(impersonation_token))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"


# ===========================================================================
# Scenario 18 — an impersonation identity can never mint a nested session (Containment, nested)
# ===========================================================================


async def test_impersonation_identity_cannot_mint_nested_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="NestedContainmentCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@nestedcontainment.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    impersonation_token = mint_resp.json()["token"]

    other_tid = await _seed_customer_tenant(db_session, name="NestedTargetCo")
    other_uid = await _seed_user(db_session, tenant_id=other_tid, email="u2@nestedtarget.io")

    resp = await client.post(_mint_url(other_tid, other_uid), headers=_bearer(impersonation_token))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"
    assert await _session_count(db_session, active_only=True) == 1


# ===========================================================================
# Scenario 19 — audit rows attribute the REAL superadmin, never the target (actor-attribution)
# ===========================================================================


async def test_audit_rows_attribute_real_superadmin_never_target(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="AuditAttributionCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@auditattribution.io", role="admin")

    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]

    start_row = await _await_audit_row(db_session, action="platform.impersonation.start")
    assert start_row is not None
    (
        start_tenant_id,
        start_actor_user_id,
        start_actor_email,
        _start_action,
        start_target_type,
        start_target_id,
        _start_result,
        start_metadata,
    ) = start_row
    assert str(start_tenant_id) == str(tid)
    assert start_actor_user_id == superadmin_user_id
    assert start_actor_email == "root@platform.internal"
    assert start_target_type == "user"
    assert start_target_id == str(uid)
    assert start_metadata["session_id"] == str(session_id)
    assert start_metadata["target_role"] == "admin"

    end_resp = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert end_resp.status_code == 204, end_resp.text

    end_row = await _await_audit_row(db_session, action="platform.impersonation.end")
    assert end_row is not None
    (
        end_tenant_id,
        end_actor_user_id,
        end_actor_email,
        _end_action,
        end_target_type,
        end_target_id,
        _end_result,
        end_metadata,
    ) = end_row
    assert str(end_tenant_id) == str(tid)
    assert end_actor_user_id == superadmin_user_id
    assert end_actor_email == "root@platform.internal"
    assert end_target_type == "user"
    assert end_target_id == str(uid)
    assert end_metadata["ended_by_original_actor"] is True


# ===========================================================================
# Scenario 20 — End durably invalidates the store regardless of the JWT's own exp (replay boundary)
# ===========================================================================


async def test_end_durably_invalidates_session_store_regardless_of_jwt_exp(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="ReplayBoundaryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@replayboundary.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]
    assert mint_resp.json()["expires_in"] > 60, (
        "hard TTL should be materially longer than this test's own runtime"
    )

    end_resp = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert end_resp.status_code == 204, end_resp.text

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is not None
    # The JWT's own exp window is still far in the future — the store is durably invalid
    # regardless (a consumer honoring revoked_at can reject a replay instantly; the live
    # per-request consultation itself is impersonation-live-session-guard's job, out of this
    # task's own build scope).
    assert row.expires_at > datetime.now(UTC)


# ===========================================================================
# Dedicated concurrency test — End's conditional-UPDATE race (contract §3 Part D IO note)
# ===========================================================================


async def test_concurrent_end_calls_exactly_one_204_one_409(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Two simulated concurrent Ends on ONE session_id -> exactly one 204 and one 409, NEVER
    two 204s (mirrors tests/member_invite_issuance's own asyncio.gather race idiom). A plain
    read-then-write implementation would let both requests observe revoked_at IS NULL before
    either commits; the frozen conditional UPDATE...RETURNING (mirrors RevokeKeyUseCase's own
    precedent, keys/infrastructure/repository.py:111-124) closes that race at the DB level."""
    tid = await _seed_customer_tenant(db_session, name="ConcurrentEndCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="u@concurrentend.io", role="admin")
    mint_resp = await client.post(_mint_url(tid, uid), headers=_bearer(superadmin_token))
    assert mint_resp.status_code == 201, mint_resp.text
    session_id = mint_resp.json()["session_id"]

    r1, r2 = await asyncio.gather(
        client.delete(_end_url(session_id), headers=_bearer(superadmin_token)),
        client.delete(_end_url(session_id), headers=_bearer(superadmin_token)),
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [204, 409], f"expected exactly one 204 and one 409, got {statuses}"

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is not None
    assert row.revoked_reason == "explicit_end"


# ===========================================================================
# Dedicated concurrency test — Mint's INSERT race (contract §3 Part D IO note, Mint half)
# ===========================================================================


async def test_concurrent_mint_calls_by_same_superadmin_exactly_one_201_one_409(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Two simulated concurrent Mint calls by the SAME superadmin -> exactly one 201 and one
    409, NEVER two 201s. Mirrors the End concurrency test above, but exercises the OTHER half
    of the same contract §3 Part D IO note: "the lazy-expire-then-check-then-insert sequence in
    Mint (M7) is NOT assumed atomic across the read and the write on its own — the partial
    unique index (uq_impersonation_sessions_actor_active) is the actual race-safety backstop; a
    resulting IntegrityError on insert is caught and translated to the same 409
    ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE the ordinary precondition check returns, never
    surfaced as a raw 500." Without this test, the router's `except IntegrityError` branch
    (the actual race-safety backstop, not merely the ordinary precondition-read 409 already
    covered by Scenario 9) would have zero coverage."""
    t1 = await _seed_customer_tenant(db_session, name="ConcurrentMintCoA")
    u1 = await _seed_user(db_session, tenant_id=t1, email="u1@concurrentmint.io", role="admin")
    t2 = await _seed_customer_tenant(db_session, name="ConcurrentMintCoB")
    u2 = await _seed_user(db_session, tenant_id=t2, email="u2@concurrentmint.io", role="admin")

    r1, r2 = await asyncio.gather(
        client.post(_mint_url(t1, u1), headers=_bearer(superadmin_token)),
        client.post(_mint_url(t2, u2), headers=_bearer(superadmin_token)),
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [201, 409], f"expected exactly one 201 and one 409, got {statuses}"
    for resp in (r1, r2):
        if resp.status_code == 409:
            assert resp.json().get("code") == "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE"

    assert await _session_count(db_session, active_only=True) == 1


# ===========================================================================
# Direct JWT-layer unit tests — N3 + defense-in-depth (unreachable via any HTTP scenario)
# ===========================================================================


def test_jwt_decode_rejects_malformed_impersonation_claim_type_error(settings: Settings) -> None:
    """N3 (security review Build-time note): decode()'s nested-claim parsing must add
    TypeError to its caught-exceptions tuple so a malformed non-dict `impersonation` claim
    401s cleanly (InvalidTokenError) instead of raw-500ing. Only reachable by forging a
    validly-HMAC-signed token with a malformed claim shape directly — no Mint bug can ever
    produce this (M2 always writes a dict), so this must be a direct unit test."""
    import time

    from gateway.tenants.domain.errors import InvalidTokenError
    from gateway.tenants.infrastructure.jwt_service import JwtTokenService

    service = JwtTokenService(settings)
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "role": "admin",
        "email": "target@co.io",
        "iat": now,
        "exp": now + 900,
        "iss": settings.jwt_issuer,
        "impersonation": "not-a-dict",  # malformed shape — a string, not an object
    }
    token = pyjwt.encode(claims, settings.jwt_secret, algorithm="HS256")

    with pytest.raises(InvalidTokenError):
        service.decode(token)


def test_jwt_decode_rejects_superadmin_role_with_impersonation_claim(settings: Settings) -> None:
    """Defense-in-depth (contract Part B): decode() must never construct an Identity with
    role=SUPERADMIN AND a non-None impersonation field, even if some future minting bug tried
    to produce one. Never produced by this task's own mint path (M6 always resolves a
    non-superadmin target role) — exercised here by directly forging that exact combination."""
    import time

    from gateway.tenants.domain.errors import InvalidTokenError
    from gateway.tenants.infrastructure.jwt_service import JwtTokenService

    service = JwtTokenService(settings)
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "role": "superadmin",
        "email": "root@platform.internal",
        "iat": now,
        "exp": now + 900,
        "iss": settings.jwt_issuer,
        "impersonation": {
            "session_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "actor_tenant_id": str(uuid.uuid4()),
            "actor_email": "root@platform.internal",
        },
    }
    token = pyjwt.encode(claims, settings.jwt_secret, algorithm="HS256")

    with pytest.raises(InvalidTokenError):
        service.decode(token)
