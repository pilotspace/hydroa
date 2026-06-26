"""RED suite for device-approval-flow (TASK.md §2 SCENARIOS, §4 TESTS).

One test per scenario — asserts OBSERVABLE behavior only (status codes, JSON body, DB row
state). Never asserts internals.

All tests fail at COLLECTION with ImportError until the Build phase — the right red reason
(router / knob absent).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.api.device_approval_router import (  # noqa: F401
    agent_oauth_approval_router,
)
from gateway.core.config import Settings  # noqa: F401 — knob existence check

pytestmark = pytest.mark.asyncio

APPROVE = "/oauth/device/approve"
DENY = "/oauth/device/deny"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Scenario 1: approve happy path
# ---------------------------------------------------------------------------


async def test_approve_happy_path(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """POST /approve with valid JWT + pending user_code → 200; row status='approved' bound to JWT."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-GHJK"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    resp = await client.post(APPROVE, json={"user_code": user_code}, headers=auth(user_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"status": "approved"}

    # DB row: status + binding
    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "approved"
    assert str(row["tenant_id"]) == tenant_id
    assert str(row["user_id"]) == user_id


# ---------------------------------------------------------------------------
# Scenario 2: user_code normalized before matching
# ---------------------------------------------------------------------------


async def test_user_code_normalized(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Lowercase/spaced/no-dash user_code normalizes to XXXX-XXXX and still matches."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)
    # Stored hash is for "BCDF-GHJK"
    user_code = "BCDF-GHJK"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    # Send the same code: lowercase, with spaces, no dash — must normalize to "BCDF-GHJK"
    resp = await client.post(
        APPROVE,
        json={"user_code": "  bcdf ghjk "},
        headers=auth(user_token),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "approved"}

    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "approved"
    assert str(row["tenant_id"]) == tenant_id
    assert str(row["user_id"]) == user_id


# ---------------------------------------------------------------------------
# Scenario 3: binding ignores extra body fields (tenant_id / user_id)
# ---------------------------------------------------------------------------


async def test_binding_ignores_body_tenant_user(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Body tenant_id/user_id are ignored; row is always bound to the JWT Identity."""
    import uuid

    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-LMNP"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    # Attempt to inject a different tenant_id/user_id via the body
    fake_tenant = str(uuid.uuid4())
    fake_user = str(uuid.uuid4())
    resp = await client.post(
        APPROVE,
        json={
            "user_code": user_code,
            "tenant_id": fake_tenant,
            "user_id": fake_user,
        },
        headers=auth(user_token),
    )

    assert resp.status_code == 200, resp.text
    row = await get_authorization_row(app, auth_id)
    # MUST be bound to the JWT identity, NOT the body values
    assert str(row["tenant_id"]) == tenant_id
    assert str(row["user_id"]) == user_id
    assert str(row["tenant_id"]) != fake_tenant
    assert str(row["user_id"]) != fake_user


# ---------------------------------------------------------------------------
# Scenario 4: deny marks row as denied
# ---------------------------------------------------------------------------


async def test_deny_marks_denied(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """POST /deny with valid JWT + pending user_code → 200; row status='denied'."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-QRST"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    resp = await client.post(DENY, json={"user_code": user_code}, headers=auth(user_token))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "denied"}

    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "denied"


# ---------------------------------------------------------------------------
# Scenario 5: approval requires authentication
# ---------------------------------------------------------------------------


async def test_approve_requires_auth(
    app: Any,
    client: Any,
) -> None:
    """No (or invalid) Authorization header → 401; row unchanged."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        seed_pending_authorization,
    )

    user_code = "BCDF-WXYZ"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    # No auth header
    resp_no_auth = await client.post(APPROVE, json={"user_code": user_code})
    assert resp_no_auth.status_code == 401, resp_no_auth.text

    # Invalid token
    resp_bad_token = await client.post(
        APPROVE,
        json={"user_code": user_code},
        headers=auth("not-a-valid-jwt"),
    )
    assert resp_bad_token.status_code == 401, resp_bad_token.text

    # Row is unchanged (pending, no binding)
    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "pending"
    assert row["tenant_id"] is None
    assert row["user_id"] is None


# ---------------------------------------------------------------------------
# Scenario 6: unknown user_code → 404
# ---------------------------------------------------------------------------


async def test_unknown_user_code_404(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """user_code that matches no row → 404 authorization_not_found."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    resp = await client.post(
        APPROVE,
        json={"user_code": "ZZZZ-ZZZZ"},
        headers=auth(user_token),
    )

    assert resp.status_code == 404, resp.text
    assert resp.json().get("error") == "authorization_not_found"


# ---------------------------------------------------------------------------
# Scenario 7: approving a non-pending authorization → 409
# ---------------------------------------------------------------------------


async def test_approve_non_pending_409(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Already approved/denied authorization → 409 authorization_not_pending."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-KLMN"
    auth_id = await seed_pending_authorization(app, user_code=user_code)

    # First approval — succeeds
    r1 = await client.post(APPROVE, json={"user_code": user_code}, headers=auth(user_token))
    assert r1.status_code == 200, r1.text

    # Second approval attempt on the same (now 'approved') row
    r2 = await client.post(APPROVE, json={"user_code": user_code}, headers=auth(user_token))
    assert r2.status_code == 409, r2.text
    assert r2.json().get("error") == "authorization_not_pending"

    # Row is still approved (binding unchanged)
    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "approved"


# ---------------------------------------------------------------------------
# Scenario 8: approving an expired authorization → 410
# ---------------------------------------------------------------------------


async def test_approve_expired_410(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Pending but expired authorization → 410 authorization_expired; stays pending/unbound."""
    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_expired_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-PQRS"
    auth_id = await seed_expired_authorization(app, user_code=user_code)

    resp = await client.post(APPROVE, json={"user_code": user_code}, headers=auth(user_token))

    assert resp.status_code == 410, resp.text
    assert resp.json().get("error") == "authorization_expired"

    # Row stays pending and unbound
    row = await get_authorization_row(app, auth_id)
    assert row["status"] == "pending"
    assert row["tenant_id"] is None
    assert row["user_id"] is None


# ---------------------------------------------------------------------------
# Scenario 9: malformed body → 422
# ---------------------------------------------------------------------------


async def test_malformed_body_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Non-JSON / missing user_code / oversized body → 422 invalid_request."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # Non-JSON body with JSON content-type
    r1 = await client.post(
        APPROVE,
        content=b"not-json-at-all!!",
        headers={**auth(user_token), "Content-Type": "application/json"},
    )
    assert r1.status_code == 422, r1.text
    assert r1.json().get("error") == "invalid_request"

    # Missing user_code field (empty JSON object)
    r2 = await client.post(APPROVE, json={}, headers=auth(user_token))
    assert r2.status_code == 422, r2.text
    assert r2.json().get("error") == "invalid_request"

    # Oversized body (> 4096 bytes)
    huge = '{"user_code":"' + ("A" * 8192) + '"}'
    r3 = await client.post(
        APPROVE,
        content=huge.encode(),
        headers={**auth(user_token), "Content-Type": "application/json"},
    )
    assert r3.status_code == 422, r3.text
    assert r3.json().get("error") == "invalid_request"


# ---------------------------------------------------------------------------
# Scenario 10: per-user rate limit → 429
# ---------------------------------------------------------------------------


async def test_per_user_rate_limit_429(
    low_rpm_app_and_client: tuple[Any, Any],
    db_session: AsyncSession,
) -> None:
    """With approve_rpm=2, the 3rd approve/deny call from one user → 429 + Retry-After."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from tests.device_approval_flow.conftest import (
        get_authorization_row,
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    low_app, low_client = low_rpm_app_and_client

    # Create identity via the low-rpm app
    token, tenant_id, _ = await signup_and_login(low_client)

    # Use the low_app's own session to mint the token
    async with low_app.state.sessionmaker() as session:
        user_token, user_id = await mint_token(low_app, session, tenant_id=tenant_id)

    # Seed three separate user_codes
    code1 = await seed_pending_authorization(low_app, user_code="BCDF-VWXZ")
    code2 = await seed_pending_authorization(low_app, user_code="BCDF-MNPQ")
    code3 = await seed_pending_authorization(low_app, user_code="BCDF-RSVW")

    # First two calls — within limit
    r1 = await low_client.post(APPROVE, json={"user_code": "BCDF-VWXZ"}, headers=auth(user_token))
    assert r1.status_code == 200, r1.text

    r2 = await low_client.post(APPROVE, json={"user_code": "BCDF-MNPQ"}, headers=auth(user_token))
    assert r2.status_code == 200, r2.text

    # Third call — must be rate-limited
    r3 = await low_client.post(APPROVE, json={"user_code": "BCDF-RSVW"}, headers=auth(user_token))
    assert r3.status_code == 429, r3.text
    assert r3.json().get("error") == "rate_limited"
    assert "Retry-After" in r3.headers

    # The 3rd authorization row is still pending (unchanged by the rejected call)
    row = await get_authorization_row(low_app, code3)
    assert row["status"] == "pending"
    assert row["tenant_id"] is None


# ---------------------------------------------------------------------------
# Additional coverage tests — /deny error paths and body parse branches
# ---------------------------------------------------------------------------


async def test_deny_unknown_user_code_404(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """POST /deny with unknown user_code → 404 authorization_not_found."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    resp = await client.post(DENY, json={"user_code": "ZZZZ-YYYY"}, headers=auth(user_token))

    assert resp.status_code == 404, resp.text
    assert resp.json().get("error") == "authorization_not_found"


async def test_deny_already_denied_409(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """POST /deny on an already-denied authorization → 409 authorization_not_pending."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-STXZ"
    await seed_pending_authorization(app, user_code=user_code)

    # First deny succeeds
    r1 = await client.post(DENY, json={"user_code": user_code}, headers=auth(user_token))
    assert r1.status_code == 200, r1.text

    # Second deny on the same (now 'denied') row
    r2 = await client.post(DENY, json={"user_code": user_code}, headers=auth(user_token))
    assert r2.status_code == 409, r2.text
    assert r2.json().get("error") == "authorization_not_pending"


async def test_deny_malformed_body_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """POST /deny with malformed body → 422 invalid_request."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # Missing user_code
    r1 = await client.post(DENY, json={}, headers=auth(user_token))
    assert r1.status_code == 422, r1.text
    assert r1.json().get("error") == "invalid_request"

    # Non-JSON body
    r2 = await client.post(
        DENY,
        content=b"garbage",
        headers={**auth(user_token), "Content-Type": "application/json"},
    )
    assert r2.status_code == 422, r2.text
    assert r2.json().get("error") == "invalid_request"


async def test_per_user_rate_limit_deny_429(
    low_rpm_app_and_client: tuple[Any, Any],
) -> None:
    """The per-user rate limit also applies on /deny; 3rd call → 429."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        seed_pending_authorization,
        signup_and_login,
    )

    low_app, low_client = low_rpm_app_and_client
    token, tenant_id, _ = await signup_and_login(low_client)
    async with low_app.state.sessionmaker() as session:
        user_token, _ = await mint_token(low_app, session, tenant_id=tenant_id)

    # Seed two pending authorizations
    await seed_pending_authorization(low_app, user_code="BCDF-DCFG")
    await seed_pending_authorization(low_app, user_code="BCDF-HHJK")

    # First two calls on /deny — within limit
    r1 = await low_client.post(DENY, json={"user_code": "BCDF-DCFG"}, headers=auth(user_token))
    assert r1.status_code == 200, r1.text
    r2 = await low_client.post(DENY, json={"user_code": "BCDF-HHJK"}, headers=auth(user_token))
    assert r2.status_code == 200, r2.text

    # Third call — rate limited
    r3 = await low_client.post(DENY, json={"user_code": "BCDF-LMNP"}, headers=auth(user_token))
    assert r3.status_code == 429, r3.text
    assert r3.json().get("error") == "rate_limited"
    assert "Retry-After" in r3.headers


async def test_oversized_body_with_content_length_header_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Content-Length header > 4096 → 422 before body is read (approve path)."""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # Send a fake Content-Length that claims the body is huge
    resp = await client.post(
        APPROVE,
        content=b'{"user_code":"BCDF-GHJK"}',
        headers={
            **auth(user_token),
            "Content-Type": "application/json",
            "Content-Length": "99999",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request"


async def test_oversized_raw_body_no_content_length_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Oversized body → 422. (httpx auto-sets Content-Length, so the declared-length guard fires.)"""
    from tests.device_approval_flow.conftest import (
        mint_token,
        signup_and_login,
    )

    token, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # No Content-Length header; body itself is oversized so the raw-read guard fires
    huge_body = b'{"user_code":"' + b"B" * 8192 + b'"}'
    resp = await client.post(
        APPROVE,
        content=huge_body,
        headers={**auth(user_token), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request"
