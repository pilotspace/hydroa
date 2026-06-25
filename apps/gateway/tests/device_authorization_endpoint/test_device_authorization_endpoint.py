"""RED suite for device-authorization-endpoint (TASK.md §2 SCENARIOS).

One test per scenario — asserts OBSERVABLE behavior only (status codes, JSON body,
response headers, DB row counts). Never asserts internals.

All tests fail at COLLECTION with ImportError until the Build phase — the right
red reason (router / limiter / knobs absent).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.api.device_authorize_router import agent_oauth_device_router  # noqa: F401
from gateway.agent_oauth.infrastructure.ip_rate_limiter import AgentOAuthIpRateLimiter  # noqa: F401
from gateway.core.config import Settings

pytestmark = pytest.mark.asyncio

AUTHORIZE = "/oauth/device/authorize"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _row_count(db_session: AsyncSession) -> int:
    """Count rows in device_authorizations."""
    res = await db_session.execute(text("SELECT COUNT(*) FROM device_authorizations"))
    row = res.fetchone()
    return int(row[0]) if row else 0


async def _last_row(db_session: AsyncSession) -> dict[str, Any]:
    """Return the most-recently inserted device_authorizations row as a dict."""
    res = await db_session.execute(
        text(
            "SELECT id, status, scope, interval_seconds, expires_at,"
            " tenant_id, user_id, device_code_hash, user_code_hash"
            " FROM device_authorizations ORDER BY created_at DESC LIMIT 1"
        )
    )
    row = res.fetchone()
    assert row is not None, "No device_authorizations row found"
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# Scenario 1: anonymous agent starts a device flow (happy path)
# ---------------------------------------------------------------------------


async def test_happy_path_200_with_rfc8628_fields(
    client: Any, db_session: AsyncSession, settings: Settings
) -> None:
    """POST /oauth/device/authorize with no body → 200 RFC 8628 §3.2 response + 1 DB row."""
    before = await _row_count(db_session)
    resp = await client.post(AUTHORIZE)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # §3 CONTRACT — all required fields present
    assert "device_code" in body
    assert "user_code" in body
    assert "verification_uri" in body
    assert "verification_uri_complete" in body  # uri is set in these settings
    assert body["expires_in"] == 600
    assert body["interval"] == 5
    assert body["verification_uri"] == "https://app.test/activate"
    assert (
        body["verification_uri_complete"]
        == f"https://app.test/activate?user_code={body['user_code']}"
    )

    # One new pending row
    after = await _row_count(db_session)
    assert after == before + 1

    row = await _last_row(db_session)
    assert row["status"] == "pending"
    assert row["tenant_id"] is None
    assert row["user_id"] is None


# ---------------------------------------------------------------------------
# Scenario 2: codes are hashed at rest, plaintext only in the response
# ---------------------------------------------------------------------------


async def test_codes_hashed_at_rest_plaintext_in_response(
    client: Any, db_session: AsyncSession
) -> None:
    """device_code/user_code in body match sha256 of stored hashes; plaintext not persisted."""
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    resp = await client.post(AUTHORIZE)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    device_code = body["device_code"]
    user_code = body["user_code"]

    hasher = Sha256SecretHasher()
    row = await _last_row(db_session)

    # Stored value is the SHA-256 hash of the plaintext
    assert row["device_code_hash"] == hasher.hash(device_code)
    assert row["user_code_hash"] == hasher.hash(user_code)

    # The stored hashes must NOT equal the plaintext values themselves
    assert row["device_code_hash"] != device_code
    assert row["user_code_hash"] != user_code


# ---------------------------------------------------------------------------
# Scenario 3: client cannot influence ttl/interval
# ---------------------------------------------------------------------------


async def test_client_cannot_influence_ttl_or_interval(
    client: Any, db_session: AsyncSession, settings: Settings
) -> None:
    """Body fields expires_in/interval are ignored; server knobs always win."""
    # Send attacker-controlled values in body — they should be silently ignored
    resp = await client.post(
        AUTHORIZE,
        json={"expires_in": 99999, "interval": 0},
        headers={"Content-Type": "application/json"},
    )
    # expires_in and interval are not declared in the model → extra="ignore"
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Server knobs (ttl=600, interval=5) win unconditionally
    assert body["expires_in"] == 600
    assert body["interval"] == 5

    # Row reflects the server knobs too
    row = await _last_row(db_session)
    assert row["interval_seconds"] == 5


# ---------------------------------------------------------------------------
# Scenario 4: optional scope is honored, default applied otherwise
# ---------------------------------------------------------------------------


async def test_optional_scope_honored_default_applied(
    client: Any, db_session: AsyncSession
) -> None:
    """Explicit scope stored; empty body uses default_scope='proxy'."""
    # Explicit scope
    r1 = await client.post(AUTHORIZE, json={"scope": "proxy"})
    assert r1.status_code == 200, r1.text
    row1 = await _last_row(db_session)
    assert row1["scope"] == "proxy"

    # Empty body → default scope
    r2 = await client.post(AUTHORIZE)
    assert r2.status_code == 200, r2.text
    row2 = await _last_row(db_session)
    assert row2["scope"] == "proxy"


# ---------------------------------------------------------------------------
# Scenario 5: verification_uri_complete omitted when unconfigured
# ---------------------------------------------------------------------------


async def test_verification_uri_complete_omitted_when_uri_empty(
    empty_uri_app_and_client: tuple[Any, Any],
) -> None:
    """When verification_uri is '', verification_uri_complete must be absent."""
    _app, empty_client = empty_uri_app_and_client
    resp = await empty_client.post(AUTHORIZE)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["verification_uri"] == ""
    assert "verification_uri_complete" not in body


# ---------------------------------------------------------------------------
# Scenario 6: per-IP rate limit returns 429
# ---------------------------------------------------------------------------


async def test_per_ip_rate_limit_429_on_third_request(
    low_rpm_app_and_client: tuple[Any, Any],
) -> None:
    """With rpm=2, the 3rd request from the same IP gets 429 + Retry-After."""
    # Row counts go through the LOW-RPM app's own sessionmaker (not the global db_session fixture)
    low_app, low_client = low_rpm_app_and_client

    # First two requests must succeed
    r1 = await low_client.post(AUTHORIZE, headers={"X-Forwarded-For": "10.0.0.1"})
    assert r1.status_code == 200, r1.text

    r2 = await low_client.post(AUTHORIZE, headers={"X-Forwarded-For": "10.0.0.1"})
    assert r2.status_code == 200, r2.text

    # Third request from the same IP must be rate-limited
    r3 = await low_client.post(AUTHORIZE, headers={"X-Forwarded-For": "10.0.0.1"})
    assert r3.status_code == 429, r3.text
    body = r3.json()
    assert body.get("error") == "rate_limited"
    assert "Retry-After" in r3.headers

    # Count rows via the LOW-RPM app's sessionmaker (different session context)
    async with low_app.state.sessionmaker() as session:
        res = await session.execute(text("SELECT COUNT(*) FROM device_authorizations"))
        count_row = res.fetchone()
        count = int(count_row[0]) if count_row else 0
    # Only 2 successful inserts — the 3rd was rejected before DB
    assert count == 2


# ---------------------------------------------------------------------------
# Scenario 7: limiter fails OPEN when Redis is down
# ---------------------------------------------------------------------------


async def test_limiter_fails_open_when_redis_down(
    app: Any, client: Any, db_session: AsyncSession, broken_redis_limiter: Any
) -> None:
    """A Redis outage in the limiter must not block the request (fail-open)."""
    # Install the broken limiter on the app
    app.state.agent_oauth_ip_limiter = broken_redis_limiter

    before = await _row_count(db_session)
    resp = await client.post(AUTHORIZE)

    # Must still succeed — fail-open
    assert resp.status_code == 200, resp.text
    after = await _row_count(db_session)
    assert after == before + 1


# ---------------------------------------------------------------------------
# Scenario 8: transient device_code collision returns retryable 503
# ---------------------------------------------------------------------------


async def test_transient_collision_returns_503_with_retry_after(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """DeviceCodeCollisionError → 503 + Retry-After + no partial row."""
    from gateway.agent_oauth.domain.errors import DeviceCodeCollisionError

    before = await _row_count(db_session)

    with patch(
        "gateway.agent_oauth.application.use_cases.AgentOAuthService.start_device_authorization",
        side_effect=DeviceCodeCollisionError(),
    ):
        resp = await client.post(AUTHORIZE)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body.get("error") == "temporarily_unavailable"
    assert "Retry-After" in resp.headers

    # No row inserted
    after = await _row_count(db_session)
    assert after == before


# ---------------------------------------------------------------------------
# Scenario 9: malformed body is rejected with 422
# ---------------------------------------------------------------------------


async def test_malformed_body_returns_422(client: Any, db_session: AsyncSession) -> None:
    """Non-JSON body with a JSON content-type → 422 invalid_request (oversized covered separately)."""
    before = await _row_count(db_session)

    # Non-JSON body with JSON content-type
    resp = await client.post(
        AUTHORIZE,
        content=b"not-json-at-all!!!",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("error") == "invalid_request"

    # No row inserted
    after = await _row_count(db_session)
    assert after == before


async def test_oversized_body_returns_422(client: Any, db_session: AsyncSession) -> None:
    """A body larger than the cap → 422 invalid_request, no row, no unbounded work."""
    before = await _row_count(db_session)

    # >4KB JSON payload (valid JSON shape, but far larger than a scope string warrants)
    huge = '{"scope":"' + ("a" * 8192) + '"}'
    resp = await client.post(
        AUTHORIZE,
        content=huge.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request"

    # No row inserted — rejected before the DB write
    after = await _row_count(db_session)
    assert after == before


# ---------------------------------------------------------------------------
# Scenario 10: misconfigured knob fails fast at boot
# ---------------------------------------------------------------------------


def test_misconfigured_knob_fails_fast_at_construction() -> None:
    """Settings with ttl/interval/rpm <= 0 raise ValidationError at construction."""
    import pydantic

    # ttl=0 must fail
    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_device_code_ttl_seconds=0,
        )

    # interval=0 must fail
    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_poll_interval_seconds=0,
        )

    # rpm=0 must fail
    with pytest.raises((pydantic.ValidationError, ValueError)):
        Settings(
            database_url="postgresql+asyncpg://x:x@localhost:5433/x",
            agent_oauth_authorize_rpm=0,
        )
