"""RED (pre-BUILD) suite for impersonation-live-session-guard (TASK.md §3 CONTRACT — DRAFT).

Closes the bounded window impersonation-session-lifecycle TASK.md §7 OBSERVE named as N1: an
explicitly-Ended impersonation session's JWT remains technically valid against the self-service
`/admin/*` surface until its own (<=900s) `exp` claim naturally elapses. This suite proves an
End'd session's JWT is rejected on its VERY NEXT request, at EACH of the five independent
identity-resolution call sites confirmed at Ground (not just the one file the originating brief
named):
  1. `tenants/domain/authz.py::_resolve_identity`        -> GET /admin/audit  (require_permission)
  2. `keys/api/deps.py::get_identity`                     -> GET /admin/cache (bare, any role)
  3. `catalog/api/deps.py::get_current_identity`          -> GET /admin/catalog/models
  4. `usage/api/router.py::_extract_identity`             -> GET /admin/usage (bare, any role)
  5. `tenants/application/use_cases.py::GetIdentityUseCase` -> GET /admin/auth/me

Design constraint (deliberate): NOT ONE test in this file imports a not-yet-existing symbol — no
`ImpersonationSessionGuard`, `DbImpersonationSessionGuard`, or `ensure_impersonation_session_live`
is referenced anywhere here. Every test drives the ALREADY-EXISTING real HTTP surface (the real
Mint/End endpoints, the real self-service GET routes) plus the ALREADY-SHIPPED
`app.state.token_service.issue(impersonation=..., ttl_seconds=...)` kwargs. This keeps collection
green regardless of Build's internal naming/placement choices: every failure below is an assertion
mismatch (expected 401, got 200), never an ImportError/collection error.

RED before BUILD: none of the 5 call sites consult session liveness today — every
`test_end_then_replay_rejected_*` test, `test_forged_session_id_with_no_matching_row_rejected`,
`test_live_check_infra_failure_fails_closed`, and `test_concurrent_end_vs_replay_no_torn_verdict`
currently observe the replayed/forged/faulted token STILL WORKING (200), so each fails on its own
`assert ... == 401` — the right kind of red (missing implementation), never a fixture/harness bug.
`test_ordinary_identity_unaffected` and `test_naturally_expired_session_already_rejected_today` are
DELIBERATELY confirmatory/already-green (see their own docstrings) — included for regression
coverage and to pin the exact boundary of what this task does NOT need to (re)implement (natural
TTL expiry is already closed today by the pre-existing, unmodified `jwt.decode()` exp check), not
counted as red evidence.

Fixture/helper provenance (this repo's convention is suite-local fixtures, not cross-suite
imports): `platform_tenant_id`/`_seed_customer_tenant`/`_seed_user`/`_issue_token`/`_bearer`/
`superadmin_actor`/`superadmin_token`/`_fetch_session_row` mirror
`tests/impersonation_session_lifecycle`'s own fixtures of the same name/shape verbatim (duplicated,
not imported, per that suite's own documented convention).

Coverage target: >=90% of new/modified lines once Build lands (the new Protocol, domain helper,
infrastructure adapter, config validator, and the small diff at each of the 5 wiring sites).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import ImpersonationContext, Role

PLATFORM_TENANTS = "/admin/platform/tenants"


# ---------------------------------------------------------------------------
# URL + bearer helpers (mirror impersonation_session_lifecycle's own style)
# ---------------------------------------------------------------------------


def _mint_url(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate"


def _end_url(session_id: object) -> str:
    return f"/admin/platform/impersonation/sessions/{session_id}"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures + helpers (app/client/db_session/settings from the root conftest.py;
# the rest are suite-local duplicates of impersonation_session_lifecycle's own helpers)
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id; seed one directly when the fast create_all test schema
    has not run the seed migration (mirrors every sibling platform-admin suite's own fixture)."""
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
    """Insert a users row directly (mirrors impersonation_session_lifecycle's own helper)."""
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


def _issue_token(
    app: Any, *, role: Any, tenant_id: uuid.UUID, email: str, user_id: uuid.UUID
) -> str:
    """Mint an ORDINARY (non-impersonation) Bearer token directly via the live token service."""
    token, _ = app.state.token_service.issue(
        user_id=user_id, tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_actor(
    db_session: AsyncSession, platform_tenant_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """A REAL superadmin UserRow — Mint's own INSERT has an enforced FK to users.id, so a
    token-only (no backing row) superadmin does not survive a real Mint call."""
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


async def _fetch_session_row(db_session: AsyncSession, session_id: object) -> Row[Any] | None:
    return (
        await db_session.execute(
            text(
                "SELECT revoked_at, revoked_reason, expires_at FROM impersonation_sessions "
                "WHERE id = :id"
            ),
            {"id": session_id},
        )
    ).fetchone()


async def _mint_impersonation(
    client: httpx.AsyncClient,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    superadmin_token: str,
) -> tuple[str, str]:
    """Mint via the REAL, already-shipped Mint endpoint (not a hand-rolled token) — returns
    (impersonation_token, session_id)."""
    resp = await client.post(
        _mint_url(tenant_id, user_id), headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["token"], body["session_id"]


# ===========================================================================
# Core rule, exercised at all 5 independent identity-resolution call sites —
# End then immediate reuse is rejected on the VERY NEXT request (M4, R1)
# ===========================================================================


async def _assert_end_then_replay_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    self_service_path: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    superadmin_token: str,
) -> None:
    """Shared body: mint -> confirm the target route accepts the fresh token -> End (real
    endpoint) -> replay the SAME token -> assert 401 ERR_AUTH_INVALID_TOKEN, and that this
    check performed no write of its own (revoked_at/revoked_reason are End's own write,
    unchanged by the replay)."""
    token, session_id = await _mint_impersonation(
        client, tenant_id=tenant_id, user_id=user_id, superadmin_token=superadmin_token
    )

    pre = await client.get(self_service_path, headers=_bearer(token))
    assert pre.status_code != 401, (self_service_path, pre.status_code, pre.text)

    end_resp = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
    assert end_resp.status_code == 204, end_resp.text

    row_after_end = await _fetch_session_row(db_session, session_id)
    assert row_after_end is not None
    assert row_after_end.revoked_at is not None
    assert row_after_end.revoked_reason == "explicit_end"

    replay = await client.get(self_service_path, headers=_bearer(token))
    assert replay.status_code == 401, (self_service_path, replay.status_code, replay.text)
    assert replay.json().get("code") == "ERR_AUTH_INVALID_TOKEN"

    # This check performs no write of its own — End's own write is unchanged by the replay.
    row_after_replay = await _fetch_session_row(db_session, session_id)
    assert row_after_replay is not None
    assert row_after_replay.revoked_at == row_after_end.revoked_at
    assert row_after_replay.revoked_reason == "explicit_end"


async def test_end_then_replay_rejected_admin_cache(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Entry point 2/5: keys/api/deps.py::get_identity (bare, any authenticated role) —
    GET /admin/cache. Covers M4b, R1."""
    tid = await _seed_customer_tenant(db_session, name="CacheEntryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="cache-target@x.test", role="owner")
    await _assert_end_then_replay_rejected(
        client,
        db_session,
        self_service_path="/admin/cache",
        tenant_id=tid,
        user_id=uid,
        superadmin_token=superadmin_token,
    )


async def test_end_then_replay_rejected_admin_me(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Entry point 5/5: tenants/application/use_cases.py::GetIdentityUseCase (via
    tenants/api/router.py's `me`, mounted under that router's OWN "/admin/auth" prefix) —
    GET /admin/auth/me. Covers M4e, R1."""
    tid = await _seed_customer_tenant(db_session, name="MeEntryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="me-target@x.test", role="viewer")
    await _assert_end_then_replay_rejected(
        client,
        db_session,
        self_service_path="/admin/auth/me",
        tenant_id=tid,
        user_id=uid,
        superadmin_token=superadmin_token,
    )


async def _seed_catalog_model(db_session: AsyncSession) -> str:
    """Seed one minimal active model + pricing snapshot (mirrors reasoning_passthrough/
    conftest.py's own minimal seed shape) so GET /admin/catalog/models returns 200 rather than
    409 ERR_CATALOG_EMPTY (that router's own, unrelated, pre-existing "no catalog synced yet"
    rule) — needed only so this test's pre-End check exercises a clean success, not a signal
    this task's own guard needs to care about."""
    model_id = f"test-catalog-model-{uuid.uuid4().hex[:8]}"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "Test Catalog Model"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


async def test_end_then_replay_rejected_admin_catalog_models(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Entry point 3/5: catalog/api/deps.py::get_current_identity — GET /admin/catalog/models.
    Covers M4c, R1."""
    await _seed_catalog_model(db_session)
    tid = await _seed_customer_tenant(db_session, name="CatalogEntryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="catalog-target@x.test", role="member")
    await _assert_end_then_replay_rejected(
        client,
        db_session,
        self_service_path="/admin/catalog/models",
        tenant_id=tid,
        user_id=uid,
        superadmin_token=superadmin_token,
    )


async def test_end_then_replay_rejected_admin_usage(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Entry point 4/5: usage/api/router.py::_extract_identity (bare, any authenticated role,
    no permission wrapper) — GET /admin/usage. Covers M4d, R1."""
    tid = await _seed_customer_tenant(db_session, name="UsageEntryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="usage-target@x.test", role="operator")
    await _assert_end_then_replay_rejected(
        client,
        db_session,
        self_service_path="/admin/usage",
        tenant_id=tid,
        user_id=uid,
        superadmin_token=superadmin_token,
    )


async def test_end_then_replay_rejected_admin_audit(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Entry point 1/5: tenants/domain/authz.py::_resolve_identity via
    require_permission(Permission.AUDIT_READ) — GET /admin/audit. The impersonated target role
    is ADMIN (confirmed to hold AUDIT_READ, authz.py:74-75). This is the ONE call site the
    originating dispatch brief named — proving it alone is NOT sufficient (the other 4 tests in
    this file are the completeness check). Covers M4a, R1."""
    tid = await _seed_customer_tenant(db_session, name="AuditEntryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="audit-target@x.test", role="admin")
    await _assert_end_then_replay_rejected(
        client,
        db_session,
        self_service_path="/admin/audit",
        tenant_id=tid,
        user_id=uid,
        superadmin_token=superadmin_token,
    )


# ===========================================================================
# No overhead for an ordinary (non-impersonation) identity (M7)
# ===========================================================================


async def test_ordinary_identity_unaffected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """CONFIRMATORY — expected to ALREADY PASS today and stay passing after Build. An ordinary
    (non-impersonation) identity's request is byte-identical in observable behavior; this task's
    own M7 additionally requires ZERO added DB query for this path, which this repo's own
    "assert observable behavior, never internals" convention (CONVENTIONS.md) means a black-box
    test cannot itself prove without inspecting internals — that half of M7 is a code-review-time
    WIRING check at Verify (confirm `guard.ensure_live` is never called unconditionally), not
    fabricated here as a pytest assertion."""
    tid = await _seed_customer_tenant(db_session, name="OrdinaryCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="ordinary@x.test", role="owner")
    token = _issue_token(app, role=Role.OWNER, tenant_id=tid, email="ordinary@x.test", user_id=uid)

    resp = await client.get("/admin/cache", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    assert "enabled" in resp.json()


# ===========================================================================
# Natural TTL expiry is ALREADY closed today — confirms M8, no regression
# ===========================================================================


async def test_naturally_expired_session_already_rejected_today(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_actor: tuple[uuid.UUID, str],
    platform_tenant_id: uuid.UUID,
) -> None:
    """CONFIRMATORY — expected to ALREADY PASS today. An impersonation-shaped JWT whose own
    `exp` claim has already elapsed is rejected by the PRE-EXISTING, unmodified `jwt.decode()`
    exp check, before this task's own guard could ever be reached (decode() raises first, so
    `identity` — and therefore `identity.impersonation` — is never even produced). Included to
    pin/document the exact boundary of what this task does NOT need to, and does not,
    (re)implement: only the End-before-natural-expiry window is new territory (M8)."""
    superadmin_uid, superadmin_email = superadmin_actor
    tid = await _seed_customer_tenant(db_session, name="ExpiredCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="expired-target@x.test", role="owner")

    expired_token, _ = app.state.token_service.issue(
        user_id=uid,
        tenant_id=tid,
        role=Role.OWNER,
        email="expired-target@x.test",
        impersonation=ImpersonationContext(
            session_id=uuid.uuid4(),
            actor_user_id=superadmin_uid,
            actor_tenant_id=platform_tenant_id,
            actor_email=superadmin_email,
        ),
        ttl_seconds=-10,  # already elapsed at the moment of issuance
    )

    resp = await client.get("/admin/cache", headers=_bearer(expired_token))
    assert resp.status_code == 401, resp.text
    assert resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"


# ===========================================================================
# Defensive: a forged claim naming a session_id with no matching row (M3b, R3)
# ===========================================================================


async def test_forged_session_id_with_no_matching_row_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_actor: tuple[uuid.UUID, str],
    platform_tenant_id: uuid.UUID,
) -> None:
    """A directly-constructed (test-only, bypassing Mint) impersonation JWT whose
    `impersonation.session_id` matches NO row in impersonation_sessions at all — structurally
    should never happen via the real Mint path, exercised here as defense-in-depth. Absence must
    be treated identically to explicit revocation (fail-closed), never as "not my problem.\""""
    superadmin_uid, superadmin_email = superadmin_actor
    tid = await _seed_customer_tenant(db_session, name="ForgedCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="forged-target@x.test", role="owner")

    forged_token, _ = app.state.token_service.issue(
        user_id=uid,
        tenant_id=tid,
        role=Role.OWNER,
        email="forged-target@x.test",
        impersonation=ImpersonationContext(
            session_id=uuid.uuid4(),  # never minted — no matching row
            actor_user_id=superadmin_uid,
            actor_tenant_id=platform_tenant_id,
            actor_email=superadmin_email,
        ),
        ttl_seconds=900,
    )

    resp = await client.get("/admin/cache", headers=_bearer(forged_token))
    assert resp.status_code == 401, resp.text
    assert resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"


# ===========================================================================
# Fail-CLOSED: the live-check itself failing must reject, never silently pass (M3c, R2)
# ===========================================================================


async def test_live_check_infra_failure_fails_closed(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LIVE (not-yet-ended, not-yet-expired) impersonation session, whose live-check DB read
    is forced to fail via a surgical monkeypatch scoped to any SELECT touching
    `impersonation_sessions` (a stable, library-level patch target — `AsyncSession.execute` —
    independent of Build's own internal function/class names). Confirms fail-CLOSED: "cannot
    verify" must reject, exactly like "verified revoked" (R1/R2 share one code, deliberately no
    oracle on why)."""
    tid = await _seed_customer_tenant(db_session, name="FaultInjectCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="fault-target@x.test", role="owner")
    token, _session_id = await _mint_impersonation(
        client, tenant_id=tid, user_id=uid, superadmin_token=superadmin_token
    )

    # Confirm the session is genuinely live before fault injection.
    pre = await client.get("/admin/cache", headers=_bearer(token))
    assert pre.status_code != 401, pre.text

    orig_execute = AsyncSession.execute

    async def _flaky_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if "impersonation_sessions" in str(statement):
            raise TimeoutError("simulated live-check DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _flaky_execute)

    resp = await client.get("/admin/cache", headers=_bearer(token))
    assert resp.status_code == 401, resp.text
    assert resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"


# ===========================================================================
# Concurrency: End racing replay never produces a torn/inconsistent verdict
# ===========================================================================


async def test_concurrent_end_vs_replay_no_torn_verdict(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Mirrors this repo's own `asyncio.gather` dual-dispatch race-test idiom (per
    impersonation_session_lifecycle's own precedent). Many concurrent replays of a live
    impersonation token race one concurrent End call against the SAME session_id. Every replay
    that lands must be either a clean pre-commit success or a clean post-commit 401 — never a
    500/partial result — and once everything settles, a fresh replay MUST be 401 (End has
    committed and durably invalidated the store row, regardless of the JWT's own remaining
    `exp` window)."""
    tid = await _seed_customer_tenant(db_session, name="RaceCo")
    uid = await _seed_user(db_session, tenant_id=tid, email="race-target@x.test", role="owner")
    token, session_id = await _mint_impersonation(
        client, tenant_id=tid, user_id=uid, superadmin_token=superadmin_token
    )

    async def _replay() -> int:
        r = await client.get("/admin/cache", headers=_bearer(token))
        return r.status_code

    async def _end() -> int:
        r = await client.delete(_end_url(session_id), headers=_bearer(superadmin_token))
        return r.status_code

    results = await asyncio.gather(_end(), *[_replay() for _ in range(10)])
    end_status, *replay_statuses = results
    assert end_status == 204, results
    assert all(s in (200, 401) for s in replay_statuses), replay_statuses

    final = await client.get("/admin/cache", headers=_bearer(token))
    assert final.status_code == 401, final.text
    assert final.json().get("code") == "ERR_AUTH_INVALID_TOKEN"

    row = await _fetch_session_row(db_session, session_id)
    assert row is not None
    assert row.revoked_at is not None
    assert row.revoked_reason == "explicit_end"
