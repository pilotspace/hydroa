"""RED suite for agent-token-endpoint (TASK.md §4 — 12 scenarios).

One test per scenario. Asserts OBSERVABLE behavior only: status codes, JSON body,
response headers, DB row counts, and stored hash invariants.

All tests fail at COLLECTION with ImportError (router / knobs absent) until Build —
the correct red reason.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.api.token_router import agent_oauth_token_router  # noqa: F401 — triggers ImportError before Build
from gateway.core.config import Settings
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

from tests.agent_token_endpoint.conftest import _seed_authorization

pytestmark = pytest.mark.asyncio

TOKEN_URL = "/oauth/token"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _token_count(db_session: AsyncSession) -> int:
    """Count rows in agent_tokens."""
    res = await db_session.execute(text("SELECT COUNT(*) FROM agent_tokens"))
    row = res.fetchone()
    return int(row[0]) if row else 0


async def _auth_status(db_session: AsyncSession, auth_id: Any) -> str:
    """Return the status of a device_authorizations row."""
    res = await db_session.execute(
        text("SELECT status FROM device_authorizations WHERE id = :i"),
        {"i": auth_id},
    )
    row = res.fetchone()
    assert row is not None
    return str(row[0])


async def _last_token_row(db_session: AsyncSession) -> dict[str, Any]:
    """Return the most-recently inserted agent_tokens row as a dict."""
    res = await db_session.execute(
        text(
            "SELECT id, authorization_id, access_token_hash, refresh_token_hash, scope "
            "FROM agent_tokens ORDER BY created_at DESC LIMIT 1"
        )
    )
    row = res.fetchone()
    assert row is not None, "No agent_tokens row found"
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# Scenario 1: poll before approval returns authorization_pending
# ---------------------------------------------------------------------------


async def test_poll_pending_returns_authorization_pending(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """POST /oauth/token with a pending device_code → 400 authorization_pending; no token."""
    device_code, auth = await _seed_authorization(app, status="pending")
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "authorization_pending"}

    # No token minted; status unchanged
    assert await _token_count(db_session) == before
    assert await _auth_status(db_session, auth.id) == "pending"


# ---------------------------------------------------------------------------
# Scenario 2: poll after approval mints the token (happy path)
# ---------------------------------------------------------------------------


async def test_poll_approved_mints_token(
    app: Any, client: Any, db_session: AsyncSession, tenant_user: dict[str, Any]
) -> None:
    """POST /oauth/token with an approved device_code → 200 with token fields; 1 token row."""
    device_code, auth = await _seed_authorization(
        app,
        status="approved",
        scope="proxy",
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
    )
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # §3 CONTRACT fields
    assert "access_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600  # agent_oauth_access_token_ttl_seconds default
    assert body["scope"] == "proxy"
    assert "refresh_token" in body  # refresh_ttl > 0 → present

    # Exactly one new token row; authorization now consumed
    assert await _token_count(db_session) == before + 1
    assert await _auth_status(db_session, auth.id) == "consumed"


# ---------------------------------------------------------------------------
# Scenario 3: refresh_token issued only when enabled
# Split into two independent tests to avoid dual-app DB conflicts.
# ---------------------------------------------------------------------------


async def test_refresh_token_present_when_enabled(
    app: Any,
    client: Any,
    tenant_user: dict[str, Any],
) -> None:
    """refresh_ttl>0 (default 2592000) → refresh_token present in the 200 body."""
    device_code, _auth = await _seed_authorization(
        app,
        status="approved",
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
    )
    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )
    assert resp.status_code == 200, resp.text
    assert "refresh_token" in resp.json()


async def test_refresh_token_absent_when_disabled(
    no_refresh_app_and_client: tuple[Any, Any],
) -> None:
    """refresh_ttl=0 → refresh_token absent from the 200 body."""
    no_refresh_app, no_refresh_client = no_refresh_app_and_client

    # Seed a tenant for the no_refresh_app
    async with no_refresh_app.state.sessionmaker() as session:
        from sqlalchemy import text

        # Create minimal tenant+user via the admin signup endpoint of the no_refresh_app
        pass  # seeding done below via HTTP

    # Use the no_refresh_client to sign up a tenant
    signup_resp = await no_refresh_client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "NoRefreshCo",
            "email": "test@norefresh.io",
            "password": "correct horse battery",
        },
    )
    assert signup_resp.status_code == 201, signup_resp.text
    import uuid as _uuid

    no_refresh_tenant_id = _uuid.UUID(signup_resp.json()["tenant_id"])

    async with no_refresh_app.state.sessionmaker() as session:
        from sqlalchemy import text

        res = await session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t"), {"t": no_refresh_tenant_id}
        )
        no_refresh_user_id = res.scalar_one()

    device_code, _auth = await _seed_authorization(
        no_refresh_app,
        status="approved",
        tenant_id=no_refresh_tenant_id,
        user_id=no_refresh_user_id,
    )
    resp = await no_refresh_client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )
    assert resp.status_code == 200, resp.text
    assert "refresh_token" not in resp.json()


# ---------------------------------------------------------------------------
# Scenario 4: single-use — second poll after mint is rejected
# ---------------------------------------------------------------------------


async def test_single_use_second_poll_invalid_grant(
    app: Any, client: Any, db_session: AsyncSession, tenant_user: dict[str, Any]
) -> None:
    """After a successful mint the same device_code → 400 invalid_grant; no 2nd token row."""
    device_code, _auth = await _seed_authorization(
        app,
        status="approved",
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
    )

    # First poll → 200
    r1 = await client.post(TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code})
    assert r1.status_code == 200, r1.text

    count_after_first = await _token_count(db_session)

    # Second poll (same device_code, now consumed) → 400 invalid_grant
    r2 = await client.post(TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code})
    assert r2.status_code == 400, r2.text
    assert r2.json() == {"error": "invalid_grant"}

    # No second token row
    assert await _token_count(db_session) == count_after_first


# ---------------------------------------------------------------------------
# Scenario 5: denied authorization returns access_denied
# ---------------------------------------------------------------------------


async def test_denied_returns_access_denied(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """A denied authorization → 400 access_denied; no token minted."""
    device_code, auth = await _seed_authorization(app, status="denied")
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "access_denied"}
    assert await _token_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 6: expired pending authorization returns expired_token
# ---------------------------------------------------------------------------


async def test_expired_pending_returns_expired_token(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """A pending authorization past its expiry → 400 expired_token; status stays pending."""
    now = datetime.now(UTC)
    past = now - timedelta(seconds=1)  # already expired
    device_code, auth = await _seed_authorization(
        app,
        status="pending",
        expires_at=past,
    )
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "expired_token"}
    assert await _token_count(db_session) == before
    # Status must remain pending (no state mutation on expiry)
    assert await _auth_status(db_session, auth.id) == "pending"


# ---------------------------------------------------------------------------
# Scenario 7: unknown device_code returns invalid_grant
# ---------------------------------------------------------------------------


async def test_unknown_device_code_invalid_grant(client: Any, db_session: AsyncSession) -> None:
    """An unrecognized device_code → 400 invalid_grant; nothing created."""
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": "nope-does-not-exist"}
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "invalid_grant"}
    assert await _token_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 8: wrong grant_type returns unsupported_grant_type
# ---------------------------------------------------------------------------


async def test_wrong_grant_type_unsupported(client: Any, db_session: AsyncSession) -> None:
    """grant_type != device_code grant → 400 unsupported_grant_type; no token."""
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL,
        json={"grant_type": "authorization_code", "device_code": "anything"},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "unsupported_grant_type"}
    assert await _token_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 9: polling faster than interval returns slow_down
# ---------------------------------------------------------------------------


async def test_poll_too_fast_slow_down(app: Any, client: Any, db_session: AsyncSession) -> None:
    """Two polls in rapid succession for the same pending device_code → 2nd gets slow_down."""
    device_code, _auth = await _seed_authorization(app, status="pending")

    # First poll → authorization_pending (sets the Redis NX key)
    r1 = await client.post(TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code})
    assert r1.status_code == 400, r1.text
    assert r1.json() == {"error": "authorization_pending"}

    # Second immediate poll → the NX key still exists → slow_down
    r2 = await client.post(TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code})
    assert r2.status_code == 400, r2.text
    assert r2.json() == {"error": "slow_down"}

    # No token minted in either case
    assert await _token_count(db_session) == 0


# ---------------------------------------------------------------------------
# Scenario 10: malformed / missing device_code returns 422 / 400
# ---------------------------------------------------------------------------


async def test_malformed_body_422(client: Any, db_session: AsyncSession) -> None:
    """Non-JSON body → 422 invalid_request; no token."""
    before = await _token_count(db_session)

    resp = await client.post(
        TOKEN_URL,
        content=b"not-json!!!",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request"
    assert await _token_count(db_session) == before


async def test_oversized_body_422(client: Any, db_session: AsyncSession) -> None:
    """Body > 4096 bytes → 422 invalid_request; no token."""
    before = await _token_count(db_session)

    huge = '{"grant_type":"' + _GRANT_TYPE + '","device_code":"' + ("a" * 8192) + '"}'
    resp = await client.post(
        TOKEN_URL,
        content=huge.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request"
    assert await _token_count(db_session) == before


async def test_missing_device_code_400(client: Any, db_session: AsyncSession) -> None:
    """Missing device_code field → 400 invalid_request; no token."""
    before = await _token_count(db_session)

    resp = await client.post(TOKEN_URL, json={"grant_type": _GRANT_TYPE})
    assert resp.status_code == 400, resp.text
    assert resp.json().get("error") == "invalid_request"
    assert await _token_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 11: per-IP rate limit returns 429
# ---------------------------------------------------------------------------


async def test_per_ip_rate_limit_429(
    low_rpm_app_and_client: tuple[Any, Any],
) -> None:
    """With token_rpm=2, the 3rd poll from the same IP gets 429 + Retry-After."""
    low_app, low_client = low_rpm_app_and_client

    # Two successful polls (both pending → authorization_pending, not a mint)
    r1 = await low_client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc1"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert r1.status_code == 400  # invalid_grant (unknown code) or authorization_pending

    r2 = await low_client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc2"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert r2.status_code == 400  # likewise

    # 3rd poll → rate limited
    r3 = await low_client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc3"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert r3.status_code == 429, r3.text
    body = r3.json()
    assert body.get("error") == "rate_limited"
    assert "Retry-After" in r3.headers

    # No token was minted by any of these requests
    async with low_app.state.sessionmaker() as session:
        res = await session.execute(text("SELECT COUNT(*) FROM agent_tokens"))
        count_row = res.fetchone()
        count = int(count_row[0]) if count_row else 0
    assert count == 0


# ---------------------------------------------------------------------------
# Scenario 12: token secrets hashed at rest
# ---------------------------------------------------------------------------


async def test_token_hashed_at_rest(
    app: Any, client: Any, db_session: AsyncSession, tenant_user: dict[str, Any]
) -> None:
    """access_token in body sha256-matches stored hash; plaintext not persisted."""
    device_code, _auth = await _seed_authorization(
        app,
        status="approved",
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
    )

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    access_token = body["access_token"]

    # Compute SHA-256 of the plaintext token
    expected_hash = hashlib.sha256(access_token.encode()).hexdigest()

    # Fetch the stored hash from DB
    token_row = await _last_token_row(db_session)
    assert token_row["access_token_hash"] == expected_hash

    # The plaintext must NOT be stored verbatim
    assert token_row["access_token_hash"] != access_token

    # If refresh_token is present, verify its hash too
    if "refresh_token" in body:
        refresh_token = body["refresh_token"]
        expected_refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        assert token_row["refresh_token_hash"] == expected_refresh_hash
        assert token_row["refresh_token_hash"] != refresh_token


# ---------------------------------------------------------------------------
# Scenario 13: config knob validation
# ---------------------------------------------------------------------------


def test_access_ttl_zero_fails_at_boot() -> None:
    """agent_oauth_access_token_ttl_seconds=0 must raise ValidationError at construction."""
    import pydantic

    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_access_token_ttl_seconds=0,
        )


def test_token_rpm_zero_fails_at_boot() -> None:
    """agent_oauth_token_rpm=0 must raise ValidationError at construction."""
    import pydantic

    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_token_rpm=0,
        )


def test_refresh_ttl_zero_is_valid() -> None:
    """agent_oauth_refresh_token_ttl_seconds=0 is valid (means disabled)."""
    s = Settings(
        database_url="postgresql+asyncpg://x:x@localhost:5433/x",
        agent_oauth_refresh_token_ttl_seconds=0,
    )
    assert s.agent_oauth_refresh_token_ttl_seconds == 0


def test_refresh_ttl_negative_fails_at_boot() -> None:
    """agent_oauth_refresh_token_ttl_seconds=-1 must raise ValidationError."""
    import pydantic

    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_refresh_token_ttl_seconds=-1,
        )


# ---------------------------------------------------------------------------
# Designed-for-failure: slow_down fails OPEN when Redis is down (CLAUDE.md IO rule)
# ---------------------------------------------------------------------------


async def test_slow_down_fails_open_when_redis_down(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """A Redis outage during the slow_down probe must NOT 500 and must NOT slow_down.

    Fail-OPEN: a pending poll still returns authorization_pending so a Redis blip can
    never block a legitimate agent from completing the device flow.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    device_code, _auth = await _seed_authorization(app, status="pending")

    class _BrokenRedis:
        async def set(self, *_a: Any, **_k: Any) -> Any:
            raise RedisConnectionError("redis down")

    app.state.redis_client = _BrokenRedis()

    resp = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": device_code}
    )

    # Fail-open → authorization_pending (NOT 500, NOT slow_down)
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"error": "authorization_pending"}
    assert await _token_count(db_session) == 0
